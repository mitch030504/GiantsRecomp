# giantsrecomp - ReXGlue Recompiled Project
#
# SDK boilerplate lives in generated/rexglue.cmake.

cmake_minimum_required(VERSION 3.25)
project(giantsrecomp LANGUAGES CXX C)

set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Build the SDK from the pinned submodule when present; otherwise generated/rexglue.cmake falls back
# to an installed SDK found through find_package. Pass -DREXSDK_DIR=<path> to use another checkout.
if(NOT REXSDK_DIR AND EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/thirdparty/rexglue-sdk/CMakeLists.txt")
    set(REXSDK_DIR "${CMAKE_CURRENT_SOURCE_DIR}/thirdparty/rexglue-sdk" CACHE PATH
        "Path to the rexglue-sdk source tree" FORCE)
endif()

include(generated/rexglue.cmake)

# Portal of Power support (thirdparty/skylanders-portal, shared with other Skylanders recomps).
if(NOT EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/thirdparty/skylanders-portal/CMakeLists.txt")
    message(FATAL_ERROR "thirdparty/skylanders-portal is missing. Run: git submodule update --init --recursive")
endif()
add_subdirectory(thirdparty/skylanders-portal)
add_library(giantsrecomp_netplay_figures STATIC src/netplay/figure_sync.cpp)
target_include_directories(giantsrecomp_netplay_figures PUBLIC src)
target_link_libraries(giantsrecomp_netplay_figures PUBLIC skylanders_portal_core)

# Reliable UDP transport for the optional, direct-IP netplay session.
if(NOT EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/thirdparty/enet/CMakeLists.txt")
    message(FATAL_ERROR "thirdparty/enet is missing. Run: git submodule update --init --recursive")
endif()
add_subdirectory(thirdparty/enet EXCLUDE_FROM_ALL)
configure_file(thirdparty/enet/LICENSE "${CMAKE_CURRENT_BINARY_DIR}/enet_license.txt" COPYONLY)
# ENet 1.3.18's CMake recipe adds these only for MinGW; clang/MSVC need them too.
if(WIN32)
    target_link_libraries(enet PUBLIC winmm ws2_32)
endif()
add_library(giantsrecomp_netplay STATIC src/netplay/session.cpp src/netplay/lockstep.cpp src/netplay/save_snapshot.cpp)
target_include_directories(giantsrecomp_netplay PUBLIC src PRIVATE thirdparty/enet/include)
find_package(Threads REQUIRED)
target_link_libraries(giantsrecomp_netplay PUBLIC enet Threads::Threads)

# Recompiled code is generated from the user's own default.xex and is not in the repository.
if(NOT EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/generated/default/sources.cmake")
    message(WARNING
        "No recompiled code yet (generated/default/sources.cmake is missing).\n"
        "  1. Put your extracted game disc in rom/ (rom/default.xex must exist).\n"
        "  2. Run:  cmake --build --preset <preset> --target giantsrecomp_codegen\n"
        "  3. Re-run:  cmake --preset <preset>\n"
        "  4. Build:  cmake --build --preset <preset>\n"
        "See README.md.")
endif()

# Sources
set(GIANTSRECOMP_SOURCES
    src/main.cpp
    src/game/kernel_stubs.cpp
    src/hooks/engine_trace_hook.cpp
    src/hooks/decompression_hook.cpp
    src/hooks/intro_skip_hook.cpp
    src/hooks/ultrawide_hook.cpp
    src/game_menu.cpp
    src/localization.cpp
    src/portal_ipc.cpp
    src/netplay/netplay.cpp
    src/netplay/determinism.cpp
    src/netplay/input_log.cpp
    src/netplay/input_script.cpp
)

if(WIN32)
    add_executable(giantsrecompiled WIN32 ${GIANTSRECOMP_SOURCES})
else()
    add_executable(giantsrecompiled ${GIANTSRECOMP_SOURCES})
endif()

rexglue_setup_target(giantsrecompiled GPU_PLUGINS xenos)

# src/netplay/determinism.cpp hashes with rex/hash.h, which includes xxhash.h. The SDK links
# xxHash privately, so without this the header only resolved where the system has one installed.
if(TARGET xxHash::xxhash)
    target_link_libraries(giantsrecompiled PRIVATE xxHash::xxhash)
endif()

if(REXSDK_DIR)
    # An installed SDK ships imgui's headers next to its own; an in-tree build must point at them.
    target_include_directories(giantsrecompiled PRIVATE "${REXSDK_DIR}/thirdparty/imgui")
endif()

# Seed a fresh build dir with the settings-file template. Never overwrites an existing one: the
# in-game F4 Settings overlay saves the user's own changes to this exact file.
if(NOT EXISTS "${CMAKE_BINARY_DIR}/giantsrecomp.toml")
    file(COPY_FILE "${CMAKE_CURRENT_SOURCE_DIR}/giantsrecomp.toml.example" "${CMAKE_BINARY_DIR}/giantsrecomp.toml")
endif()

# --- Pinned game executable -------------------------------------------------
set(GIANTSRECOMP_XEX_HASH_FILE "${CMAKE_CURRENT_SOURCE_DIR}/docs/game/default.xex.sha256")
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${GIANTSRECOMP_XEX_HASH_FILE}")
file(READ "${GIANTSRECOMP_XEX_HASH_FILE}" GIANTSRECOMP_XEX_HASH_TEXT)
string(REGEX MATCH "^[0-9a-fA-F]+" GIANTSRECOMP_XEX_SHA256 "${GIANTSRECOMP_XEX_HASH_TEXT}")
string(LENGTH "${GIANTSRECOMP_XEX_SHA256}" GIANTSRECOMP_XEX_SHA256_LEN)
if(NOT GIANTSRECOMP_XEX_SHA256_LEN EQUAL 64)
    message(FATAL_ERROR "docs/game/default.xex.sha256 must start with a 64-character hex SHA-256 (found ${GIANTSRECOMP_XEX_SHA256_LEN} hex characters)")
endif()

add_library(giantsrecomp_verify STATIC src/xex_verify.cpp)
target_include_directories(giantsrecomp_verify PUBLIC src)
# Uses the SDK's vendored, portable SHA-256 (thirdparty/crypto) instead of a platform crypto API
# (BCrypt on Windows has no Linux/macOS equivalent), so this one implementation covers every preset.
if(NOT REXSDK_DIR)
    message(FATAL_ERROR "REXSDK_DIR must be set (giantsrecomp_verify needs its vendored SHA-256 implementation)")
endif()
target_include_directories(giantsrecomp_verify PRIVATE "${REXSDK_DIR}/thirdparty")
target_sources(giantsrecomp_verify PRIVATE "${REXSDK_DIR}/thirdparty/crypto/sha256.cpp")

target_link_libraries(giantsrecompiled PRIVATE giantsrecomp_verify skylanders_portal_rex giantsrecomp_netplay giantsrecomp_netplay_figures)
target_compile_definitions(giantsrecompiled PRIVATE GIANTSRECOMP_XEX_SHA256="${GIANTSRECOMP_XEX_SHA256}")

# Compatibility fingerprint follows source contents, including local edits, instead of a docs
# commit or build timestamp. SDK changes and codegen configuration must agree on both peers.
file(GLOB_RECURSE GIANTSRECOMP_FINGERPRINT_INPUTS CONFIGURE_DEPENDS
    "${CMAKE_CURRENT_SOURCE_DIR}/src/*.cpp" "${CMAKE_CURRENT_SOURCE_DIR}/src/*.h")
list(APPEND GIANTSRECOMP_FINGERPRINT_INPUTS
    "${CMAKE_CURRENT_SOURCE_DIR}/CMakeLists.txt"
    "${CMAKE_CURRENT_SOURCE_DIR}/config/default.toml"
    "${CMAKE_CURRENT_SOURCE_DIR}/giantsrecomp_manifest.toml")
set(GIANTSRECOMP_BUILD_INPUT "netplay-protocol-1;${CMAKE_BUILD_TYPE};${CMAKE_SYSTEM_PROCESSOR};${CMAKE_CXX_COMPILER_ID};${CMAKE_CXX_COMPILER_VERSION}")
foreach(input IN LISTS GIANTSRECOMP_FINGERPRINT_INPUTS)
    set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${input}")
    file(SHA256 "${input}" input_hash)
    string(APPEND GIANTSRECOMP_BUILD_INPUT "${input_hash}")
endforeach()
foreach(dependency rexglue-sdk skylanders-portal enet)
    execute_process(COMMAND git rev-parse HEAD
        WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}/thirdparty/${dependency}"
        OUTPUT_VARIABLE dependency_revision OUTPUT_STRIP_TRAILING_WHITESPACE COMMAND_ERROR_IS_FATAL ANY)
    string(APPEND GIANTSRECOMP_BUILD_INPUT "${dependency_revision}")
    file(GLOB_RECURSE dependency_sources CONFIGURE_DEPENDS
        "${CMAKE_CURRENT_SOURCE_DIR}/thirdparty/${dependency}/src/*"
        "${CMAKE_CURRENT_SOURCE_DIR}/thirdparty/${dependency}/include/*"
        "${CMAKE_CURRENT_SOURCE_DIR}/thirdparty/${dependency}/resources/*"
        "${CMAKE_CURRENT_SOURCE_DIR}/thirdparty/${dependency}/cmake/*")
    # ENet keeps its C sources at the root; include its build recipe and SDK recipes too.
    file(GLOB dependency_root_sources CONFIGURE_DEPENDS
        "${CMAKE_CURRENT_SOURCE_DIR}/thirdparty/${dependency}/*.c"
        "${CMAKE_CURRENT_SOURCE_DIR}/thirdparty/${dependency}/CMakeLists.txt")
    list(APPEND dependency_sources ${dependency_root_sources})
    foreach(input IN LISTS dependency_sources)
        if(NOT IS_DIRECTORY "${input}")
            set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${input}")
            file(SHA256 "${input}" input_hash)
            string(APPEND GIANTSRECOMP_BUILD_INPUT "${input_hash}")
        endif()
    endforeach()
endforeach()
string(SHA256 GIANTSRECOMP_BUILD_HASH "${GIANTSRECOMP_BUILD_INPUT}")
file(CONFIGURE OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/netplay_build.h"
    CONTENT "#define GIANTSRECOMP_BUILD_HASH \"${GIANTSRECOMP_BUILD_HASH}\"\n")
target_include_directories(giantsrecompiled PRIVATE "${CMAKE_CURRENT_BINARY_DIR}")

enable_testing()
add_executable(giantsrecomp_engine_trace_test tests/engine_trace_test.cpp)
target_include_directories(giantsrecomp_engine_trace_test PRIVATE src)
target_link_libraries(giantsrecomp_engine_trace_test PRIVATE rexruntime)
add_test(NAME engine_trace COMMAND giantsrecomp_engine_trace_test)
add_executable(giantsrecomp_tests tests/xex_verify_test.cpp)
target_link_libraries(giantsrecomp_tests PRIVATE giantsrecomp_verify)
add_test(NAME xex_verify COMMAND giantsrecomp_tests)

add_executable(giantsrecomp_xexcheck src/tools/xex_check_cli.cpp)
target_link_libraries(giantsrecomp_xexcheck PRIVATE giantsrecomp_verify)
target_compile_definitions(giantsrecomp_xexcheck PRIVATE GIANTSRECOMP_XEX_SHA256="${GIANTSRECOMP_XEX_SHA256}")

# Process-boundary test: runs the actual built giantsrecomp_xexcheck.exe against a non-ASCII path to
# catch argv narrowing (a plain main(argc, char**) loses characters outside the system code page on
# Windows). Depends on giantsrecomp_xexcheck existing in the same output directory at test time.
add_executable(xex_check_cli_argv_test tests/xex_check_cli_argv_test.cpp)
add_test(NAME xex_check_cli_argv COMMAND xex_check_cli_argv_test)

add_executable(giantsrecomp_input_log_test tests/input_log_test.cpp src/netplay/input_log.cpp)
target_include_directories(giantsrecomp_input_log_test PRIVATE src)
add_test(NAME input_log COMMAND giantsrecomp_input_log_test)

add_executable(giantsrecomp_input_script_test tests/input_script_test.cpp src/netplay/input_script.cpp)
target_include_directories(giantsrecomp_input_script_test PRIVATE src)
add_test(NAME input_script COMMAND giantsrecomp_input_script_test)

add_executable(xex_check_exit_code_test tests/xex_check_exit_code_test.cpp)
target_include_directories(xex_check_exit_code_test PRIVATE src)
add_test(NAME xex_check_exit_code COMMAND xex_check_exit_code_test)

add_executable(giantsrecomp_netplay_session_test tests/netplay_session_test.cpp)
target_link_libraries(giantsrecomp_netplay_session_test PRIVATE giantsrecomp_netplay)
target_include_directories(giantsrecomp_netplay_session_test PRIVATE thirdparty/enet/include)
add_test(NAME netplay_session COMMAND giantsrecomp_netplay_session_test)
set_tests_properties(netplay_session PROPERTIES TIMEOUT 20)

add_executable(giantsrecomp_netplay_lockstep_test tests/netplay_lockstep_test.cpp)
target_link_libraries(giantsrecomp_netplay_lockstep_test PRIVATE giantsrecomp_netplay)
add_test(NAME netplay_lockstep COMMAND giantsrecomp_netplay_lockstep_test)
set_tests_properties(netplay_lockstep PROPERTIES TIMEOUT 20)

add_executable(giantsrecomp_netplay_save_test tests/netplay_save_test.cpp)
target_link_libraries(giantsrecomp_netplay_save_test PRIVATE giantsrecomp_netplay)
add_test(NAME netplay_save COMMAND giantsrecomp_netplay_save_test)

add_executable(giantsrecomp_netplay_portal_control_test tests/netplay_portal_control_test.cpp)
target_link_libraries(giantsrecomp_netplay_portal_control_test PRIVATE skylanders_portal_rex)
add_test(NAME netplay_portal_control COMMAND giantsrecomp_netplay_portal_control_test)

add_executable(giantsrecomp_netplay_figure_sync_test tests/netplay_figure_sync_test.cpp)
target_link_libraries(giantsrecomp_netplay_figure_sync_test PRIVATE giantsrecomp_netplay_figures)
add_test(NAME netplay_figure_sync COMMAND giantsrecomp_netplay_figure_sync_test)
