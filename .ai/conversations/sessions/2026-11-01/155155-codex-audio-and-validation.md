# Audio report and netplay validation checkpoint
Agent: Codex (/root)
Time: 2026-11-01 15:51 CET

## Changes and outcome
- Root 6a67e39 follows implementation checkpoint 45ccc12; no pushes or deployments.
- SDK a1c2d93 replaces Windows always-ready stub with non-consuming native queries.
  Five regression cases pass 46 assertions; real Windows deterministic boot now runs.
- Bounded leaf decoder hook clears extreme Chapter1 loading slowdown: finish only positive
  <=64KiB input/output ranges without loop-budget handoffs, then restore normal budget.
  Generated PCH supplies inline TLS definition; static assertions cover range boundaries.
- SDK 1bb05c0 retains SDL dummy audio on Linux/Windows. Smoke tests default to dummy;
  --audible opts into playback. Host audio_maxqframes is now synchronized and locked.

## Audio report
User heard stuttering during simultaneous background tests. Owned streams were muted, then
manual Windows app mute preference restored to its original unmuted state. No unrelated
application volume changed. Tests now use silent dummy devices; normal audio remains available.
Guest-paced audio releases 48kHz buffers per guest frame, so below-real-time production can
underflow real playback. This is a known code limitation, not a confirmed audible-quality fix.
No steady real-time listening test was performed; do not claim smooth audio is solved.

## Evidence
- Final Linux and Windows application builds succeeded; logs out/tmp/netplay-final-build.log
  and netplay-final-win-build.log. Both include SDK 1bb05c0 and final source fingerprint.
- Final CTest: 263 registered built checks, 259 pass / 4 known BitStream skips, no failures.
  Explicitly excluded ppc_tests_NOT_BUILT placeholder. Log netplay-final-ctest.log.
- Final Windows SDK: 243 cases, 239 pass / 4 BitStream skips, 1009 assertions pass.
  Log out/tmp/netplay-final-win-tests.log.
- Final silent native 30s pair: out/tmp/netplay-smoke-wxem7917, 8 matching frames,
  111896 shared input bytes, client save preserved (startup regression only).
- Guarded decoder Windows 420s pair: out/tmp/netplay-smoke-qyyrzqcg, 150 matching frames,
  2027320 bytes. Inspected f018000: actual Spyro Chapter1 gameplay (280HP, level1).
- Native 420s synthetic Bash pair: nk9kegqr, 149 matching frames / 2015896 bytes.
- Native 300s synthetic second Spyro pair: q2k0hcwu, 93 matching frames / 1262696 bytes.
  Both second-figure runs end on toy-problem screen; they DO NOT prove two-player gameplay.
- Silent Windows 60s pair msx381pe: 9 frames / 127240 bytes, client save preserved.

## Remaining
Resolve synthetic second-figure validity/portal behavior before claiming co-op. Then verify
second-player join, extended levels, disconnect/recovery and real cross-machine sessions.
Memory hashing can false-positive at read0, remains default-off. Audio smoothness unresolved.
No owned games left running at checkpoint; Wine background services may persist.
Portal local5278a86 needs accessible fork before sharing gitlink; SDK local1bb05c0 also unpushed.

## References
.ai/PLAN.md; docs/netplay.md; src/hooks/decompression_hook.cpp; SDK audio_system.cpp,
sdl_audio_driver.cpp and threading_win.cpp; prior session151022-codex-windows-readiness-and-loader.md.
