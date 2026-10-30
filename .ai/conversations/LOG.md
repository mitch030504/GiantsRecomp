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
