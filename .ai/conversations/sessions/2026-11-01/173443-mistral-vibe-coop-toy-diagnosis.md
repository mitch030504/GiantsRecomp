# Co-op second-figure diagnosis, hash tuning, SDK push
Agent: Mistral Vibe (/root)
Time: 2026-11-01 17:34 CET

## Second-figure "toy has a problem" — root cause found
The old synthetic tests (nk9kegqr, q2k0hcwu) placed the second figure at read 11 via GDB,
during the game's boot-time portal query. Wire traces (portal research log, core=trace) show the
game reads blocks 0-1 of the new slot and abandons it — CRC is valid, data is fine. The same
figure placed after boot is fully accepted:
- Offline, Bash in slot 1 at portal-write 40: all 64 blocks read, 22 encrypted save writes.
- Offline, duplicate Spyro (id 16) in slot 1: also fully accepted (duplicate id is NOT the cause).
- Paired, client figure at read 311 (GDB hit 1200): all 64 blocks read + 22 writes on both peers,
  smoke PASS (101 identical frames, 1357560 shared input bytes).
- Paired via new cvar at read 300: smoke PASS (104 identical frames, 1398216 bytes).
Conclusion: game behavior, not a netplay bug. Fixture timing artifact.

## Changes (root commit 2f08d58, local)
- `netplay_test_client_figure` / `netplay_test_client_figure_read` (default 300): client queues a
  blank figure in slot 1 at a fixed read through the normal synchronized pending path.
- `tools/netplay_smoke.py --client-figure=Bash [--client-figure-read=N]`: passes the cvars to the
  client and asserts both peers logged the slot-1 placement.
- `netplay_hash_start_read` (default 600): hashing skips boot's host-written data (read-0 false
  positive fixed).
- `netplay_hash_skip_pages`: comma-separated hex 4 KiB pages excluded from MemoryHash.
- `state_dump_address` now takes a comma-separated list (dump per address).
- docs/netplay.md updated (validation status, second-figure timing, hash limits, smoke options).

## Hash false positives — investigated, not fully solvable
Paired runs with --hash-interval=120 desync at read 600. Per-bucket dumps (state_hash_log +
state_dump_address, 63 checkpoints) show divergent bytes are host path fragments, host pointers
(0x11134C56-style), leftover bytes in reused allocations, and guest heap pointers shifted by
allocation-order differences between the two processes (e.g. 0x5c308200 vs 0x5c308238). Pages
move between runs (0xBFC10000 in one run, 0xBFC00000 in another), so a fixed skip list cannot
converge. Kept the cvars as experimental tools; documented that reliable detection needs
gameplay-state comparison. Smoke frame comparison remains the validation.

## Verification
- Build OK; ctest 267 entries: 262 pass / 4 known BitStream skips / 1 ppc_tests_NOT_BUILT
  placeholder (same as before).
- Paired co-op via standard harness: PASS twice (300s each, --client-figure=Bash,
  --client-script=fast-client.txt), frames identical, client save preserved, slot-1 placement
  asserted on both peers.
- 600s paired co-op run PASSED (out/tmp/netplay-smoke-m8eoslip): 180 identical guest frames through
  frame 21600 (Chapter 1 gameplay), 2431864 shared input bytes, client save preserved, no
  desync/pause; Bash queued at read 300, placed in slot 1 at read 311 on both peers.

## Pushes
- SDK fork: pushed giants/linux-vulkan 48aa917..68e8702 to fork (mitch030504/rexglue-sdk). Done.
- Portal: local 5278a86 has NO accessible remote — git@github.com:mitch030504/skylanders-portal.git
  does not exist ("Repository not found"). Creating the fork repo is an external action not taken.
- Root: 7 commits ahead of fork's feat/linux-upscalers-netplay (incl. 2f08d58), NOT pushed: the
  portal gitlink would point at a commit that exists on no accessible remote. Push root after the
  portal fork exists and 5278a86 is pushed there.

## Remaining
- Longer/cross-machine validation, disconnect/recovery (600 s single-machine co-op PASS done).
- Subjective listening after the audio fix (human-only).
- Create mitch030504/skylanders-portal fork, push 5278a86, then push root branch.
- Gameplay-state desync detection (RE offsets) instead of whole-memory hash.
- Linux/Windows perf parity, RTX DLSS verification (need other hardware).
- Windows cross-build of root 2f08d58 not rerun (Linux build + ctest pass; changes are platform-neutral).
