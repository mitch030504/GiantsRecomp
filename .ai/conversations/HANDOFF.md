# Agent Handoff
Last updated: 2026-11-01 17:34 CET by Mistral Vibe (/root)

## Current checkpoint
User authorized unattended progress; latest steering: make the portal work like Omoio/skyportal (export in ~/Downloads/skyportal-export.zip).
Root branch feat/linux-upscalers-netplay HEAD ae65bf4 — ALL PUSHED to fork mitch030504/GiantsRecomp.
SDK giants/linux-vulkan HEAD 546b31a — pushed to fork. Portal feat/netplay-figure-control 5278a86 — pushed to new fork mitch030504/skylanders-portal.
ENet pinned2662c0d /v1.3.18.
Latest session: [183100-mistral-vibe-game-menu.md](sessions/2026-11-01/183100-mistral-vibe-game-menu.md).
Also: [181747-mistral-vibe-portal-ipc-server.md](sessions/2026-11-01/181747-mistral-vibe-portal-ipc-server.md).
Earlier: [173443-mistral-vibe-coop-toy-diagnosis.md](sessions/2026-11-01/173443-mistral-vibe-coop-toy-diagnosis.md), [163033-codex-pitch-preserving-audio.md](sessions/2026-11-01/163033-codex-pitch-preserving-audio.md).

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

## Audio fix (latest)
KDE identity fix e725b89 stays: game has its own Giants Recompiled mixer name; other app mute
states preserved. Stutter cause: guest-paced production falls behind real device during slow render.
SDK68e8702 now uses SoundTouch2.4.1 only on mixed device output, preserving pitch. Guest callbacks,
semaphores/scheduling unchanged; ordinary audio bypasses DSP. Bounded buffers,100ms ready target
plus lookahead; long stalls can still go silent. Shared library/license/source archive packaged.
24798assertions/3DSPcases pass standalone native and underProton:440Hz preserved,2/6channels,
0.35–1.25speed, bursty delivery, bounds and recovery. Final Native/Windows builds pass with SDK68e8702 fingerprint. Portable-copy
$ORIGIN resolution and shared-library/license/source staging verified. Initial built CTest266entries have no failures,
4knownBitStreamskips, PPC placeholder excluded.
250s real cutscene run exits0. At36.28FPS:0zero chunks/4512requests over24s; at42.65FPS:
22/5452 over29s. Old slow cutscenes had ~35–57%zeros. Host speed differs, so no controlled speedup
claim.74common before/after guest frames compared, all identical. Final30s startup pair7i6tq29i passes9frames/133064bytes/save preserved;
subjective listening and long paired playback still pending. See newest session for exact artifacts/remaining checks.

## Next work
0. Listen to speech/music and validate longer paired playback after audio fix (600 s paired co-op PASS: out/tmp/netplay-smoke-m8eoslip, 180 identical frames / 2431864 input bytes; subjective listening still human-only).
1. DONE 2026-11-01 (Mistral Vibe): second-figure "toy has a problem" was a fixture timing artifact — placing at read ~11 during boot is rejected by the game; after boot (read 300+) the figure is fully accepted on both peers (64-block read + save writes, paired smoke PASS). Use --netplay_test_client_figure / smoke --client-figure. Two-player co-op validated on one machine for 10 min; cross-machine still open.
2. Longer gameplay, LAN/cross-machine determinism, disconnect/recovery and audio-quality checks.
3. Hash false positives: read-0 fixed via netplay_hash_start_read=600; residual diffs at read 600+ are host paths/pointers and heap pointers shifted between processes (pages move per run) — netplay_hash_skip_pages exists but cannot converge; reliable detection needs gameplay-state hashing (RE offsets). Whole-memory hash stays experimental.
4. DONE 2026-11-01 (Mistral Vibe, fork created by user): SDK, portal (new fork mitch030504/skylanders-portal) and root branches all pushed to the mitch030504 forks; gitlinks resolve. Latest: root ae65bf4, SDK 546b31a.
6. DONE 2026-11-01 (Mistral Vibe): portal IPC server — the game now speaks the Omoio/Cemu SkylanderPortalIPC protocol on 127.0.0.1:28014 (portal_ipc/portal_ipc_port), so skyportal/Omoio menus can drive the portal (hello/status/load/remove/clear/create/hush). Verified with skyportal's own client; placements sync in netplay; hush gated off in netplay. See src/portal_ipc.cpp, tools/portal_ipc_client.py, docs/portal-protocol.md.
7. DONE 2026-11-01 (Mistral Vibe): F9 in-game menu (root c2ab29e) with Graphics / Multiplayer / Controls / Portal tabs — curated graphics settings, netplay host/join forms with save+restart + live status, press-to-capture key rebinding, portal mode + IPC. Headless smoke clean; ctest 1724/1724.
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
