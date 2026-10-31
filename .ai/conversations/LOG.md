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
