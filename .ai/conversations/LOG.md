# Agent Activity Log
Append-only. Newest at the bottom.

---
## 2026-10-30 15:44 — claude-code #setup #feature #bugfix
**Task:** Plan + start Linux/Wayland, temporal upscalers and netplay; bootstrap agent handoff.
**Outcome:** Linux builds/presents (Wayland fix), tarball packaging, netplay determinism tools, FSR 3.1 context working; real FSR inputs in progress (uncommitted, output not yet verified).
**Files:** see sessions/2026-10-30/154429-claude-code-linux-upscalers-netplay.md
**Next:** debug missing FSR output dumps, verify jitter, commit; then vendor backends, Wayland items, netplay time virtualization.
---

---
## 2026-10-30 15:46 — claude-code #setup #docs
**Task:** Prepare handoff to the user's CachyOS desktop (RX 9070 XT) and package the repo + ROM as a zip.
**Outcome:** HANDOFF gains a "Resuming on the RX 9070 XT desktop" section; plan + Claude memory copied into `.ai/`; agent snippet injected into CLAUDE.md/AGENTS.md/codex.md; zip created (excludes `out/`).
**Files:** .ai/*, CLAUDE.md, AGENTS.md, codex.md
**Next:** On the desktop: build, finish FSR 3.1 output verification, retry red tint on RADV.
---

---
## 2026-10-30 15:51 CET — claude-code #config
**Task:** Push both branches (user: "it needs to be on a fork").
**Outcome:** Forked to mitch030504/{GiantsRecomp,rexglue-sdk}; pushed giants/linux-vulkan then feat/linux-upscalers-netplay to remote `fork`.
**Files:** .git configs (fork remotes), .ai/conversations/HANDOFF.md
**Next:** Decide whether .gitmodules should point at the SDK fork.
---

---
## 2026-10-30 16:53 CET — claude-code #bugfix #feature
**Task:** "fix the issues": FSR output missing, uncommitted work, submodule URL, handoff files not committed.
**Outcome:** Found Presenter ODR layout bug (frame index stuck at 0); FSR 3.1 now upscales with real inputs (verified with dumps). Committed + pushed SDK (c1bf1cb, 90cf5b8, 05a33af) and main (jitter hook, .gitmodules -> fork, .ai/ + agent configs; .ai/claude-memory/ kept out of git).
**Files:** SDK presenter/temporal files, src/hooks/ultrawide_hook.cpp, .gitmodules, .gitignore, .ai/*, CLAUDE.md, AGENTS.md, codex.md
**Next:** Jitter-sign A/B and quality check on the 9070 XT; vendor backends; Wayland items.
---

---
## 2026-10-30 18:41 — claude-code #feature #bugfix #investigation
**Task:** Resume on the RX 9070 XT desktop: build, verify FSR 3.1, red tint, profile, then the plan.
**Outcome:** Built + ctest 14/14. FSR 3.1 verified against native 4K; x jitter flipped. Red tint not reproducible on RADV. SDK: frame-locked guest time, perf CSV fix, swapchain validation fix. Determinism: still diverges from boot → stop point, options in docs/netplay.md. Nothing pushed.
**Files:** see sessions/2026-10-30/184130-claude-code-desktop-fsr-verify-netplay-time.md
**Next:** user picks a netplay option; gameplay input recording for FSR A/B + profiling; hands-on Wayland checks; push when asked.
---

---
## 2026-10-30 22:35 CET — claude-code #feature #bugfix #tooling
**Task:** Finish the open items; D3D12/Vulkan switch in F4; then DLSS5VKLayer and XeSS on Vulkan.
**Outcome:** gpu_backend + Restart now; Windows cross-build + Proton runner; D3D12 temporal parity; FSR 4.1.2 (SDK 2.4) on Windows; XeSS on D3D12+Vulkan; DLSS via NGX (fallback verified); dlss5_layer. Netplay/profile wait for a gameplay recording.
**Files:** see sessions/2026-10-30/223500-claude-code-backend-switch-windows-cross-upscalers.md
**Next:** user's gameplay recording -> netplay option 3 + profile; hands-on Wayland/F4 checks; push when asked.
---

---
## 2026-10-30 23:35 CET — claude-code #feature #investigation
**Task:** Automate the gameplay recording (user: "cant the recording be done automated? ideally in the background"), then netplay option 3.
**Outcome:** input_script + portal_bot_figure + tools/bot_record.sh record ~2.5 min of Chapter 1 headless (33bc5e8). Two replays diverge ~10 s into play → lockstep ruled out (d3ee542).
**Files:** src/netplay/input_script.*, src/netplay/determinism.cpp, tools/bot_record.sh, tools/bot/chapter1.txt, docs/netplay.md
**Next:** user picks streaming vs deterministic scheduling; profile + upscaler A/B with the recording.
---

---
## 2026-10-30 23:50 CET — claude-code #planning #handoff
**Task:** Netplay direction (user wants lag-insensitive play, asked about a dedicated Unraid server); then hand off and shut down.
**Outcome:** Route chosen: determinism -> delay lockstep -> rollback; Unraid as lobby/relay/clock only. Experiment helpers saved in out/tmp/bot/helpers; no experiment run yet.
**Files:** .ai/PLAN.md, .ai/conversations/HANDOFF.md
**Next:** experiment 1 (single-core replays), experiment 2 (guest-time audio driver), then cooperative scheduling.
---
## 2026-10-31 06:31 CET — claude-code
**Task:** Q&A: summarise what's done and what's left.
**Outcome:** Answered from PLAN.md/HANDOFF.md; no changes. Main still has 4 unpushed commits (33bc5e8..3465593); SDK in sync with fork.
---
## 2026-10-31 07:16 CET — claude-code
**Task:** Continue netplay determinism: experiments 1 (single core) and 2 (guest-paced audio).
**Outcome:** Exp 1 invalid (4 fps on one core -> stall frames). Exp 2 (`audio_guest_paced`) still splits at ~7560. Added `sync_trace_log`; causes are host-timed sleeps/timeouts and lock races. SDK changes staged, not committed.
**Files:** SDK audio_system, sdl_audio_driver, xboxkrnl_video, xobject, xthread, xtimer, xsemaphore; docs/netplay.md; .ai/*
**Next:** scheduler step 1 (guest-time sleeps/timeouts), verify with sync_frames.py.
---
## 2026-10-31 07:59 CET — claude-code
**Task:** Commit previous work; scheduler step 1 (guest-time sleeps/timeouts); find first diverging memory.
**Outcome:** clock_frame_locked_waits (SDK aaa81e0) makes pollers match per frame; split remains. Windowed state hashes: first gameplay divergence at read 7050 = heap allocation order between threads. Step 2 designed (decision record).
**Files:** SDK clock, xobject, xthread; src/netplay/determinism.cpp; docs/netplay.md; .ai/*
**Next:** step 2, run token.
---
## 2026-10-31 08:49 CET — claude-code
**Task:** Scheduler step 2: one guest thread at a time (sched_deterministic) #feature #netplay
**Outcome:** Built and committed in SDK 00b0697 (not pushed; main bump not committed). Fixed four boot deadlocks (zero-timeout polls, lock-poll loops, GPU busy-wait, quantum throughput); runs faster than without it. Two-replay determinism test k1/k2 was still running at handoff.
**Files:** SDK guest_scheduler.*, xobject/xthread/xevent/xmutant/xsemaphore, threading_*, codegen builders + pch template; .ai/*
**Next:** read k1/k2 result, commit bump + docs, then step 3 (host events at fixed guest points).
---
## 2026-10-31 18:18 CET — claude-code
**Task:** Status review for the user (what's done, where we left off, what's next).
**Outcome:** Found an unrecorded session: SDK 5752e48 (clock_frame_locked_stall_ms) + replays m1/m2. Step 2 moves the pixel split 7560 -> 7920; memory still diverges from read ~7020. No code changes.
**Files:** .ai/* only
**Next:** commit SDK bump + netplay doc, then scheduler step 3.
---
## 2026-10-31 19:05 CET — claude-code (interim)
**Task:** Scheduler step 3: host threads (GPU commands, vblank, audio, XMA, kernel dispatch) on fixed turns; idle/busy virtual frames.
**Outcome:** Built (SDK uncommitted), runs at 60 fps in play. Two-replay test q1/q2 running (out/tmp/bot). Kernel dispatch change written but not built yet (binary in use).
**Next:** read q1/q2 (cmp.py, hash_onset.py), build, commit SDK + bump + docs.
---
## 2026-10-31 19:05 CET — claude-code
**Task:** Scheduler step 3 #feature #netplay #bugfix (handoff write-back via /agent-handoff)
**Outcome:** GPU commands, vblank, audio callback, kernel dispatch on fixed turns (SetDue); idle/busy virtual frames instead of the host-timed stall fallback; NoSwitchScope for interrupts; adaptive quanta; POSIX alertable 0 ms wait fix. 60 fps in play. SDK uncommitted; dispatch part unbuilt; q1/q2 replay pair still running.
**Files:** SDK clock, guest_scheduler, command_processor, graphics_system, audio_system, xma_decoder, kernel_state, threading_posix, function_dispatcher, xboxkrnl_video, xthread.h; docs/netplay.md; .ai/*
**Next:** read q1/q2, build, commit SDK + bump + docs, push (approved).
---
## 2026-10-31 19:20 CET — claude-code
**Task:** Read q1/q2, commit + push step 3 #netplay #feature
**Outcome:** 408/491 buckets always match (135 before), 11 diverge for good (315); picture split still ~7920. Committed SDK 8562ed2 + 4153831, main eb847f0 (bump + docs/netplay.md).
**Files:** docs/netplay.md; thirdparty/rexglue-sdk; .ai/*
**Next:** diff 0xB5F00000 / 0xB9200000 between runs to find the remaining racing writer.
---
## 2026-10-31 19:25 CET — claude-code
**Task:** Start r1/r2 dump pair for 0xB5F00000 #netplay #investigation
**Outcome:** running in background (420 s).
---
## 2026-10-31 19:40 CET — claude-code
**Task:** 0xB5F00000 diff #netplay #investigation
**Outcome:** only 0xB5F0002C (counter 7 vs 8) differs transiently from read 7070; real split at 7800. Running sync-trace pair t1/t2.
---
## 2026-10-31 20:20 CET — claude-code
**Task:** Deterministic boot + KeTimeStampBundle at frames #netplay #feature
**Outcome:** t1/t2 traces diverged at frame 0 (host-timed boot). Boot now guest-timed, timestamp bundle updated at frames, busy frame 2^22 back-edges. Uncommitted. u1/u2 pair running.
---
## 2026-10-31 20:55 CET — claude-code
**Task:** Boot determinism + interrupt deadlocks #netplay #bugfix
**Outcome:** SDK 2405954 (guest-timed boot, KeTimeStampBundle at frames, busy frame 2^22) and 20dda51 (spinning interrupts yield with the global lock released; vblank wrap after time reset fixed). Not pushed. u1/u2 pair running.
---
## 2026-10-31 21:20 CET — claude-code
**Task:** Determinism verification #netplay
**Outcome:** u1/u2: sync traces + pictures identical through frame 11040. v1/v2: only host-data buckets (kernel objects, stacks) differ. Pushed SDK 20dda51, main 61a3f9a. Running full-length w1/w2.
---
## 2026-10-31 21:35 CET — claude-code
**Task:** Full-length determinism check #netplay
**Outcome:** w1/w2: pictures identical through frame 23880 (whole recording). 3 buckets split intermittently at read ~8065 without visible effect. PLAN: blocker cleared, phase 1 next.
---
