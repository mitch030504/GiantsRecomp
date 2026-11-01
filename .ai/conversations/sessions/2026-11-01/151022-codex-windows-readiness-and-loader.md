# Windows scheduler boot fix and Linux loader investigation
Agent: Codex
Date: 2026-11-01 15:10 CET

## Completed
Root checkpoint committed as45ccc12 (experimental netplay feature; no release/push).
SDK a1c2d93 fixes Windows IsSignaled with non-consuming NtQuery* native queries for event,
semaphore, mutant and timer; thread completion uses non-consuming thread wait. Unknown query
failure logs once and retains conservative true-hint semantics. Five regression cases fail
11assertions with the oldstub and pass46assertions with fix. FullWindows suite1009assertions
passes239cases/4existing skips. Parent independently reran46assertions before SDKcommit.
Actual60s offlineProton deterministic boot now reachesread967 /108424input bytes; previously
read0 was never reached in180s. Forced termination needed after timeout grace; no gameplay claim.
Artifacts: out/tmp/windows-readiness-*.log, out/tmp/windows-readiness-boot/.
PairedWindows180s smoke running out/tmp/windows-readiness-pair.log (firstread0 reached).

## Linux evidence
Capture out/tmp/netplay-loader2/gdb.log: loaderglobal826ED14C ->BF4CFC2C contains3items
BF4CFAD4/BF4CFB18/BF4CFB5C sharingobjBF4CFBA0/resourceBF781A58. Itemstate+12=1,
completionword+20=0; resourceflag+35=1. JQworkers actively inside sub822BBA60 (LZMA/range
decoder arithmetic, NOT a spinlock), calledthrough822BC838/822BCF68/8225B300/8225B4C0.
Thus don't call workers idle: decompression jobs are still executing. Need quantify progress,
corruptdecoderstate vs extreme scheduling slowdown. NewGDB runout/tmp/netplay-loader3 tracks
PPCcontext pointers at decoderentry, then dumpsregisters at270s. Do not overwrite runtime.so
until that ownedrun exits. Scriptout/tmp/netplay-loader2/capture.gdb. Session69541.

## Remaining
VerifyWindowspairedsmoke, run native readiness/fullSDK once Linuxcapture ends, update SDKgitlink,
finalbuildfingerprint, commit docs/checkpoint. LinuxChapter1loading remains unresolved.

## Subsequent progress
- Windows paired180s smoke passed:37identical frames,505912shared input bytes,client savepreserved(read4200);out/tmp/netplay-smoke-nc9g8iy3.
- Linux loader2/3 captures show changing job counters and active LZMA decoder registers, so prior 'stall' is extreme scheduling slowdown, not proven deadlock. Experimental smallchunk atomic decode hook clears Chapter1loading;300s pair passes117frames/1580552bytes into opening scene.
- Final hook includes generated PCH (inline TLS definition reachable), applies only to positive <=64KiB input/output ranges; static assertions exercise range bounds, offline scheduler-disabled path unchanged. Windows+Linux app builds pass with finalhook. Root10tests and full243SDKcases(239pass4skip),1009assertions pass.
- Longer420s native co-op fixture with second toy and client START/movement nowactive:out/tmp/netplay-smoke-nk9kegqr (logout/tmp/netplay-decoder-coop2.log). Windows420spair:out/tmp/netplay-smoke-qyyrzqcg (logout/tmp/netplay-decoder-windows-pair.log). Waitownedgamesexitbeforeanylibraryrebuild.
- Initialco-opretry failed because GDB C mode didnotrecognize boolcast; correctedtestscripttointreturnmaskedlowbyte. Thiswasatestfixturefailure, notguestcrash.
