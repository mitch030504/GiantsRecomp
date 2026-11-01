# Unattended netplay implementation (in progress)
Agent: Codex
Date: 2026-11-01 13:55 CET

## Authorization and scope
User requested as much completion as possible unattended. Follow approved direct-IP lockstep plan.
ENet1.3.18 added successfully; old approval blocker no longer applies.

## Implemented so far
- ENet session/handshake, bounded messages/queues, game/build checks, timeout/disconnect handling.
- Read-index input delay; uniform connected capabilities, local-player vibration, no live fallback.
- Host save/settings snapshot, strict received-path validation, private fresh client save folder.
- Software portal control interception, F8 status/error UI; forced deterministic settings.
- Critical settings locked before UI; client settings applied+locked atomically (latest source).
- Latest source adds host-authoritative per-toy progression retention/reinsertion, with reply in host input stream.
- SDK fixes from validation: Windows Yield macro collision,1601 date overflow; depfile test escape typo.

## Evidence / caveats
- Native180s smoke out/tmp/netplay-smoke-2um1lbpj:81 matching frames identical;1,097,272 shared input bytes identical; existing client-save sentinel preserved.
- IMPORTANT: image inspection shows this OLD replay and archived w1 replay navigate menus/Battle, not claimed Chapter1 gameplay. Historical full-gameplay determinism claim is unsupported.
- Corrected frame-based tools/bot/netplay-story.txt now running600s in out/tmp/netplay-smoke-i3locxcl (session78396); source edits made since that binary, needs final rebuild/retest after peers exit.
- Opt-in memory-hash smoke out/tmp/netplay-smoke-501cfu5_ pauses at read0: raw memory hashing is diagnostic only and false-positives remain. Default0.
- Root9CTest cases pass, including portal coordinator gate. Full SDK:237cases,233pass/4skip,960assertions pass after chrono/depfile fixes (before latest atomic-lock fixture).
- Windows full app cross-build succeeds after ENet WinMM/Winsock link fix and scheduler YieldThread rename. Proton transport/lockstep tests pass. Save test passes with explicit skip for verified Wine symlink-creation no-op (helper protections unchanged).

## Next
Finish600s scene validation; rebuild latest code once native peers exit; run full SDK/root tests and Windows builds/tests. Review/commit submodule changes then root; final configure after submodule commits for fingerprint.
Update docs for authoritative client figure round trip and remaining validation limits. Native libs must not be replaced while test games run.

## References
Approved .ai/plans/splendid-sprouting-goblet.md, tasks/plan.md and tasks/todo.md,
docs/netplay.md, out/tmp/netplay-*.log and smoke directories.
