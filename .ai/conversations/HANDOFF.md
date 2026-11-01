# Agent Handoff
Last updated: 2026-11-01 16:10 CET by Codex (/root)

## Current checkpoint
User authorized unattended progress; latest steering: investigate stutter and possible KDE taskbar mute.
Root branch feat/linux-upscalers-netplay: implementation45ccc12, loading/audio-test fix6a67e39; mixer identity fixe725b89.
SDK giants/linux-vulkan HEAD1bb05c0; portal feat/netplay-figure-control HEAD5278a86.
ENet pinned2662c0d /v1.3.18. All commits local; no pushes or deployments.
Latest session: [161019-codex-audio-root-cause.md](sessions/2026-11-01/161019-codex-audio-root-cause.md).
Earlier investigation: sessions/2026-11-01/151022-codex-windows-readiness-and-loader.md.

## Implemented
- ENet direct IPv4 host/join with protocol/XEX/source handshake and bounded queues/messages.
- Ordered delayed inputs; async startup; F8 status/error pause without live-input fallback.
- Validated title-only host save snapshots; fresh private client storage preserves original saves.
- Host-authoritative software figure progression, scheduled removals/placements; local rumble.
- Deterministic guest settings forced/locked; host audio queue synchronized; uniform capabilities.
- Windows native wait readiness fixed (a1c2d93), five regressions46assertions, real boot proven.
- Bounded <=64KiB leaf LZMA batching clears Chapter1 scheduler loading slowdown on both platforms.
- Test harness silent by default through SDL dummy (SDK1bb05c0), --audible opts into playback.

## Evidence and limits
Final Linux+Windows app builds pass. Built CTest:263 entries,259pass/4knownskips, no failures;
ppc_tests_NOT_BUILT explicitly excluded. Windows SDK243cases/1009assertions pass (4knownskips).
Final native30s smoke wxem7917:8identical frames/111896inputbytes/client save preserved.
Guarded-hook Windows420s pair qyyrzqcg:150identical frames/2027320bytes; f018000 inspected:
actual Spyro Chapter1 single-player gameplay. Native initial300s reaches opening cutscene.
Native second-toy420s nk9kegqr and second-Spyro300s q2k0hcwu match peer outputs but show
"toy has a problem". These do NOT prove two-player gameplay; diagnose fixture/portal next.
Historical w1/play.inputlog was menus/Battle, not gameplay. Do not reuse its old gameplay claim.
Memory hashes false-positive at read0; default0. Automatic gameplay desync detection unfinished.

## Audio report (confirmed)
Native game inherited KDE mute=true under generic SDL Application, shared with UnrealEditor.
Portal initializes SDL audio before runtime driver metadata. e725b89 sets Giants Recompiled
app-name hint before portal startup; live PulseAudio35s + PipeWire15s checks confirm separate,
unmuted identity. Unreal remains muted; no unrelated volume/mute edits. Test routes cleaned up.
Separate stutter measured: single-instance deterministic cutscene at29.24FPS supplied1740 of3572
required256-sample chunks over19s;1832chunks were zero. At steady60FPS both ordinary and
scheduled title runs have no measured shortages. Guest-paced production is tied to VdSwap;
below60FPS cannot feed device in real time. Larger queue cannot solve sustained shortage.
Netplay audio smoothness remains OPEN. No subjective listening validation or pitch/rate change.
Latest native+Windows builds pass; CTest259pass/4knownskips, PPC placeholder excluded.

## Next work
1. Diagnose synthetic second-figure toy error; verify valid figure loading and actual player2 join.
2. Longer gameplay, LAN/cross-machine determinism, disconnect/recovery and audio-quality checks.
3. Reliable gameplay desync detection; raw memory diagnostic still false-positives at boot.
4. Push accessible SDK/portal forks and root gitlinks when appropriate. Portal URL still points to
   TheBiemGamer; its local5278a86 commit needs an accessible remote. SDK URL is mitch030504 fork.
5. Linux/Windows performance parity, RTX DLSS verification and minor upscaler settings polish.

## Working context
- RX9070XT/RADV, clang23; noRTX. User assets under rom; do not modify user saves/figure originals.
- Native configure: cmake --preset linux-amd64-release -DCMAKE_C_COMPILER=clang -DCMAKE_CXX_COMPILER=clang++
- Windows: presetwin-amd64-release-xwin; Proton10 used for execution (not a native Windows box).
- Tests/logs/images under ignored out/tmp. Never screenshot desktop; inspect dumped guest PPMs.
- Do not overwrite runtime.so during owned game runs. No owned games active at this checkpoint;
  Wine services may persist. Never kill unrelated games/audio streams.
- Source-content handshake fingerprint includes toolchain/platform, so different-platform peers
  may intentionally reject; same-platform cross-machine validation is the next safe target.
- Unraid is lobby/relay only, not gamehost. No relay, WAN auth/encryption, rollback, resync,
  adaptive delay, matchmaking or deployment in current scope.
- Architectural limits: decisions/2026-11-01-bounded-decoder-scheduling.md and netplay-session-boundaries.md.
