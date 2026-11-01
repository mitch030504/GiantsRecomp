# Netplay implementation and actual gameplay gate
Agent: Codex
Date: 2026-11-01 14:53 CET

## Authorization
User authorized as much unattended progress as possible, then requested handoff+summary near quota.
No release, PR or push performed. All owned test games/debuggers have exited.

## Source state
Root branch feat/linux-upscalers-netplay, original HEAD e51f3e6. Root implementation remains uncommitted.
SDK giants/linux-vulkan clean, local commits815f095(date range),e553455(depfile literal test),45e989b(Windows Yield macro),c129d37(input caps/vibration filters),eb3ee67(atomic cvar locks).
Portal branch feat/netplay-figure-control clean, commit5278a86. Neither submodule changes pushed.
ENet added, .gitmodules staged, checkout pinned2662c0d(v1.3.18); gitlink still needs final staging.
Do not lose untracked netplay source/test/tools files listed by git status.

## Implementation
- src/netplay/session.*: ENet worker, explicit bounded wire format, protocol/XEX/build compatibility, ordered input stream, timeout/cancel/disconnect. stop/join serialized, cancellation before async Start checked.
- lockstep.*: delayed read-index states (default3), bounded pending queues, strict portal/input packet parsing.
- save_snapshot.*: only title415608DA profiles and per-XUID content;16MiB/4096files; portable safe paths, no links, private fresh extraction; real client saves untouched.
- netplay.*: async responsive handshake, UI-thread post-handshake cvar changes, forced+locked deterministic config, fresh client user/cache paths, F8 status, pause-on-error, uniform caps, local-player vibration.
- figure_sync.*: authoritative copies by immutable toy identity; client reinsertion uses host-earned progress. Client proposals first synchronize removal, then host resolves+rebroadcasts canonical placement. Source mapping preserves host original files; clients don't write progression.
- Portal FigureControl intercepts F6 changes and prevents backend switching during session.
- Projection hooks bypass local ultrawide/temporal jitter for netplay; temporal presentation forced off.
- Source-content fingerprint includes SDK/portal/ENet revisions+contents and toolchain/build type; final native configure/relink completed at checkpoint; Windows final fingerprint/license recipe rebuild remains.
- tools/netplay_smoke.py, tools/bot/netplay-story.txt; development bot_skip_movies flag shortens Bink movies and is host-synchronized.
- README/docs/netplay/settings/packaging updated; ENet license staged by CMake and included by packaging glob (latest packaging edits not rebuilt).

## Verification
- Native app rebuilt successfully after final fingerprint/license edits; ENet license exists in build folder. Windows app build predates those final CMake-only recipe edits.
- Root10focusedCTest tests pass (including portal coordinator, toy progress retention), ASan/UBSan session+lockstep+save tests pass.
- SDK full unit binary238cases:234pass,4existingBitStream skips;963assertions pass. Dates1601/year9999 fixed rather than skipped.
- FullCTest259entries:254pass4skip1NotRun placeholder ppc_tests_NOT_BUILT. PPC test executable was not built; do not call fullCTest green.
- Windows underProton: session,lockstep,save,figure_sync tests pass. Save tests skip ONLY verified Wine no-op symlink creation; native link-rejection tests pass.
- Native smoke180s out/tmp/netplay-smoke-2um1lbpj:81identical common frames,1,097,272shared input bytes, client sentinel preserved (menus/Battle only).
- Native Story600s out/tmp/netplay-smoke-i3locxcl:137identical frames,1,849,352bytes, openingmovie (not gameplay).
- Native Story420s+bot_skip_movies out/tmp/netplay-smoke-c99gozfp:203identical frames,2,738,296bytes, reachesChapter1Loading then stuck.
- Native paired420s with actual client CreateAndPlaceFigure underGDB out/tmp/netplay-smoke-l0iu4yt3:198identical frames,2,670,760bytes, both appliedsecondtoy(slot1)atread11; still loadinghang. TemporaryGDB scripts inout/tmp/netplay-tests; SIGSEGV must pass for normal guestfault handler.
- Offline sched=true out/tmp/netplay-offline reproducesChapter1Loadinghang, so NOT network-specific.
- Offline sched=false out/tmp/netplay-baseline DOES reach realChapter1gameplay (Spyro,17gold screenshot out/tmp/netplay-baseline-live.png) with same bot_skip_movies=true. Scheduler is actual remaining gate.
- Windows actual pairedVulkan smoke out/tmp/netplay-smoke-im735mfu handshakes but never reachesread0 in180s; failed harness checkpoint honestly recorded.
- Raw memory-hash diagnostic interval300 out/tmp/netplay-smoke-501cfu5_ pausesatread0. Default0; automatic gameplay desync protection NOT complete.

## Critical correction to prior handoff
Archivedw1 frames and oldplay.inputlog were labelledChapter1gameplay by earlier agent, but inspected frames showmenus/BattleMode. Do NOT rely on historical23880-frame visual agreement as gameplay determinism proof. New capture route reaches realChapter1loading, exposes schedulerhang.

## Blockers and next actions
1. Linuxscheduler loading: debugout/tmp/netplay-debug/gdb.log captures tfbLoadThread at sub8225B838(.69.cpp:7235)->sub8225C6C0(.56:7058)->sub8225F220(.65:6806), yielding from loop budget repeatedly. Main6 atsub821E7C00(.5:4183). No rootcause yet. Capturechild/vtable state; generatedctx locals optimizedout in currentrelease. Debuggerrun alreadycleanedup.
2. Windowsboot ROOTCAUSE: threading_win.cpp:142-146 IsSignaled alwaysTRUE stub. XObject::WaitUntilGuestDeadline uses it atxobject.cpp:317/371; schedulerkeeps selecting unsignalledpollers asready and never advancesidlevirtualtime. MainstuckKeDelayExecutionThread LR8240B9F4. Implement non-consuming readiness with tracked/nativequeries forEvent/Semaphore/Mutant/Thread/Timer; testunsignalledfalse+signalnotconsumed. Do NOT naivelyuseWaitForSingleObject(0), whichconsumessemaphore/autoresetsignals.
3. Native final build+10root tests+fullSDK rerun PASS at checkpoint. Windows final configure/rebuild stillneeded. Buildppc_tests iffeasible, elseexplicitlyreportplaceholderNotRun.
4. Proveactualgameplay andtwo-playerco-op afterloaderfix; testrealdisconnect/F8 andextendedLAN/crossmachine. Snapshot/coretransportdoesnotprovegameplay.
5. Resolvefalse-positive rawmemoryhashes beforedefaultdesyncdetection. Adaptive delay,rollback,lobby/relay/NATremainunimplemented.
6. Stage/commitrootandpushSDK/portaltoaccessibleforks beforemakingrootgitlinksavailable remotely; portalstillTheBiemGamerURLandhaslocalcommitonly, needaccessibleforkURLbeforeremotePR.

## References
.ai/plans/splendid-sprouting-goblet.md;tasks/plan.md;tasks/todo.md;docs/netplay.md;
out/tmp/netplay-*.log and named smoke dirs, prior135531session.

## Final checkpoint validation
Native latest source built;10projecttests pass;238SDKcases234pass4skip/963assertions pass. Whitespace checks clean. Session authority decision recorded in decisions/2026-11-01-netplay-session-boundaries.md. All owned games/debuggers exited. Root implementation remains uncommitted and nothing pushed.
