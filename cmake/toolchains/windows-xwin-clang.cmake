# Cross-compile the Windows (x64, MSVC ABI) build on Linux with clang, lld and an xwin-splatted
# MSVC CRT + Windows SDK. Set XWIN_DIR (default ~/opt/xwin) to the folder `xwin splat` wrote.
# The result runs under Wine/Proton, which is how D3D12 gets tested on Linux (vkd3d-proton).

set(CMAKE_SYSTEM_NAME Windows)
set(CMAKE_SYSTEM_PROCESSOR AMD64)

if(DEFINED ENV{XWIN_DIR})
    set(XWIN_DIR "$ENV{XWIN_DIR}")
else()
    set(XWIN_DIR "$ENV{HOME}/opt/xwin")
endif()
if(NOT EXISTS "${XWIN_DIR}/crt/include" OR NOT EXISTS "${XWIN_DIR}/sdk/include/um")
    message(FATAL_ERROR "XWIN_DIR (${XWIN_DIR}) doesn't hold an xwin splat (crt/ and sdk/)")
endif()

set(CMAKE_C_COMPILER clang)
set(CMAKE_CXX_COMPILER clang++)
set(CMAKE_C_COMPILER_TARGET x86_64-pc-windows-msvc)
set(CMAKE_CXX_COMPILER_TARGET x86_64-pc-windows-msvc)
set(CMAKE_RC_COMPILER llvm-rc)
set(CMAKE_MT llvm-mt)
set(CMAKE_AR llvm-ar)
set(CMAKE_LINKER lld-link)

set(_xwin_includes
    "${XWIN_DIR}/crt/include"
    "${XWIN_DIR}/sdk/include/ucrt"
    "${XWIN_DIR}/sdk/include/um"
    "${XWIN_DIR}/sdk/include/shared"
    "${XWIN_DIR}/sdk/include/winrt")
# Linux file names are case-sensitive and the source includes a few Windows headers with
# different capitalisation than the SDK files; point those names at the real files.
set(_xwin_case_dir "${CMAKE_BINARY_DIR}/xwin-case")
file(MAKE_DIRECTORY "${_xwin_case_dir}")
foreach(header ObjBase.h SDKDDKVer.h)
    string(TOLOWER "${header}" lower)
    foreach(dir IN LISTS _xwin_includes)
        if(EXISTS "${dir}/${lower}" AND NOT EXISTS "${_xwin_case_dir}/${header}")
            file(CREATE_LINK "${dir}/${lower}" "${_xwin_case_dir}/${header}" SYMBOLIC)
        endif()
    endforeach()
endforeach()
list(APPEND _xwin_includes "${_xwin_case_dir}")

# -idirafter, like clang-cl's -imsvc: clang's own intrin.h/immintrin.h must win over MSVC's, or
# intrinsics compile to calls to functions that don't exist.
set(_xwin_flags "")
foreach(dir IN LISTS _xwin_includes)
    string(APPEND _xwin_flags " -idirafter \"${dir}\"")
endforeach()
# Same baseline as the native presets. Kept here because a preset's CMAKE_CXX_FLAGS would replace
# these *_INIT flags and drop the xwin include paths.
set(CMAKE_C_FLAGS_INIT "-march=x86-64-v2${_xwin_flags}")
set(CMAKE_CXX_FLAGS_INIT "-march=x86-64-v2${_xwin_flags}")
set(CMAKE_RC_FLAGS_INIT "-I \"${XWIN_DIR}/sdk/include/um\" -I \"${XWIN_DIR}/sdk/include/shared\"")

# xwin ships only the release CRT, so link it in every configuration (try_compile included).
set(CMAKE_MSVC_RUNTIME_LIBRARY MultiThreadedDLL)
set(CMAKE_TRY_COMPILE_CONFIGURATION Release)

set(_xwin_link "-fuse-ld=lld")
foreach(dir crt/lib/x86_64 sdk/lib/um/x86_64 sdk/lib/ucrt/x86_64)
    string(APPEND _xwin_link " -Xlinker /libpath:\"${XWIN_DIR}/${dir}\"")
endforeach()
set(CMAKE_EXE_LINKER_FLAGS_INIT "${_xwin_link}")
set(CMAKE_SHARED_LINKER_FLAGS_INIT "${_xwin_link}")
set(CMAKE_MODULE_LINKER_FLAGS_INIT "${_xwin_link}")

# Build tools (glslang, codegen helpers) must come from the host; libraries and headers from xwin.
set(CMAKE_FIND_ROOT_PATH "${XWIN_DIR}")
set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_PACKAGE ONLY)
