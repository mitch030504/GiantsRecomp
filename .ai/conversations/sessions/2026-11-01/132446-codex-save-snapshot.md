# Bounded netplay save snapshots
Agent: Codex /root/save_snapshot
Date: 2026-11-01 13:24 CET

## Changes
- Added src/netplay/save_snapshot.h/.cpp and tests/netplay_save_test.cpp.
- API: PackSaveSnapshot(user_root, out, error); UnpackSaveSnapshot(bytes, fresh_user_root, error).
- Copies only SDK content roots named 8 or 16 hexadecimal characters directly under user data; excludes figures/cache/achievements/content and other unrelated paths.
- Archive GR_SAVE1, LE u32 file count and LE u32 path/data lengths, UTF8 paths. Bounds: 16 MiB total, 4096 files, 1024-byte paths, 255-byte components, 32 components.
- Rejects malformed UTF8, traversal, drive/ADS/reserved paths, duplicate/file-directory/case aliases, links and special files. Windows comparison uses CompareStringOrdinal for Unicode case aliases too.
- Entire received archive is validated before any files are created. Destination must not exist and have a symlink-free existing parent. Partial extraction removes only newly created root. Source encoding errors return false.

## Verification
- clang++ -std=c++23 -Wall -Wextra -Werror -I src tests/netplay_save_test.cpp src/netplay/save_snapshot.cpp -o out/tmp/netplay-tests/netplay_save_test
- Tests pass, including every archive truncation, binary/UTF8 roundtrip, omission of unrelated files, existing-data protection, unsafe/colliding names, links/FIFO and oversize rejection.
- ASan/UBSan run passes with -fsanitize=address,undefined -fno-omit-frame-pointer.
- Helper and test compile to Windows objects using existing xwin headers and --target=x86_64-pc-windows-msvc.
- No CMake edits, commits or runtime game tests by this subagent. Root integrating and handling overall handoff/plan/path updates.

## References
thirdparty/rexglue-sdk/src/system/kernel_state.cpp; thirdparty/rexglue-sdk/src/system/xam/content_manager.cpp; tasks/plan.md.
