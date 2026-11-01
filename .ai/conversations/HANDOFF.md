# Agent Handoff
Last updated: 2026-11-01 14:54 CET by Codex

## Active Work
Unattended netplay implementation on feat/linux-upscalers-netplay. No owned games/debuggers remain running.
Latest complete checkpoint: [sessions/2026-11-01/145320-codex-netplay-final-handoff.md](sessions/2026-11-01/145320-codex-netplay-final-handoff.md).
Root implementation remains UNCOMMITTED, including untracked src/netplay files/tests/tools. Preserve it.
SDK local HEAD eb3ee67 (giants/linux-vulkan); portal5278a86 (feat/netplay-figure-control); both clean, not pushed.
ENet added successfully, pinned2662c0d/v1.3.18. Earlier approval blocker cleared.

## What is implemented
- Direct IPv4 ENet host/join, protocol/game/build handshake, bounded ordered delayed inputs.
- Host save/settings transfer; strict title-only archives and fresh private client save storage.
- Software figure requests, host-authoritative progression retention/reinsertion; local-only rumble.
- Deterministic settings forced/locked, uniform controller capabilities, F8 status and pause-on-error.
- Responsive async startup; cvar mutations finalized on UI thread. Offline behavior retained.
- Native+Windows build/test support, smoke harness, docs/settings, ENet license staging.

## Evidence (do not overclaim)
Native10root tests pass; SDK238cases234pass4skip/963assertions. FullCTest has one PPC_NOT_BUILT placeholder.
Windows app builds; four netplay core tests run successfully underProton (Wine-only link test skip).
Native420s pair:203identical captured frames/2,738,296shared input bytes; client saves preserved.
Second pair: client F6API placement appliedslot1atread11onBOTHpeers;198identical frames.
Both reach Chapter1LOADING, then stall. Actual online gameplay is NOT proven.
Historical w1/play.inputlog claims of Chapter1gameplay were wrong: inspected frames are menus/Battle.
Memory hash diagnostic falsely reportsdesyncatread0; default0, automaticgameplaydesync detection unfinished.

## Next Up (actual blockers)
1. Linux deterministic scheduler stalls Chapter1 loading, also OFFLINE. Non-scheduler offline reaches real gameplay.
   Stackout/tmp/netplay-debug/gdb.log: tfbLoadThread sub8225B838 -> sub8225C6C0 -> sub8225F220.
   Loop-budget yields continue, no rootcauseyet. Capturechild/vtable/memory state next.
2. Windows scheduler boot never reachesinputread0: threading_win.cpp142 IsSignaled ALWAYS TRUE stub.
   XObject317/371usesasreadiness; pollersalwaysready preventidlevirtualtimeadvance. Implementnonconsumingreadiness;
   don'treplacewithconsumingWaitForSingleObject(0). Sourceevidenceinlatest session.
3. Latest native configure/relink COMPLETE at checkpoint;10root tests pass, SDK963assertions pass.
   Windows binary predates final CMake fingerprint/license recipe edits; reconfigure/relink before cross-platform checks.
4. Actualco-op/load/disconnect/LAN/crossmachine validation; memorydesynchashfalsepositives;adaptive delay/rollbacklater.
5. Rootcommitandpushsubmoduleforks/gitlinks aftervalidation. PortalURLcurrentlyTheBiemGamer,localcommitnotremotelyavailable.

## Key Context
- This desktop: RX9070XT/RADV, clang23; noRTX, so DLSS requiresotherhardware.
- Configure: cmake --preset linux-amd64-release -DCMAKE_C_COMPILER=clang -DCMAKE_CXX_COMPILER=clang++
- SDK tests enabled locally(-DREXGLUE_BUILD_TESTS=ON); rununit_tests orbuildppc_tests beforefullCTest.
- Do not replace runtime .so while owned game tests run. Use headlessgamescope; never screenshotdesktop.
- Stringflagsneed --flag=value. Testartifactsunderout/tmp/; rootusergameassetsuntouched.
- Userauthorized unattendedwork. ServerUnraidis lobby/relayonly, not gamehost; no deploymentdone.
- Linux/upscalerwork largelyimplemented; hardwarevalidation/settingspolish stillpending (seePLAN).

## Recent Completions
- Codex: ENet/session/input/save/portal/status implementation; adversarial parser/archive tests.
- Codex SDK commits815f095(date range),e553455(depfiletest),45e989b(YieldThreadWindows),c129d37(inputfilters),eb3ee67(cvarlocks).
- Codex portal5278a86: FigureControl coordinator for scheduledplacements/removals.
- Prior sessions: Linux packaging/Wayland/Vulkan fixes; FSR/XeSS/DLSSintegrations andcrossbuildtooling.

## Pending Decisions / limits
Netplayisexperimental andcurrentlyblockedbyruntimeissues. No claimofplayableonlineco-op.
NoWANauthentication/encryption,rollback,automaticresync,adaptive delay,matchmakingorrelayimplemented.
SDKremoteis mitch030504 fork; portalneedsaccessibleforkbeforepushingitsnewcommit.
