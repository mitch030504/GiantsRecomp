# Netplay implementation plan
Agent: Codex. Follows the approved .ai/plans/splendid-sprouting-goblet.md Part 3 and user's unattended authorization.

1. ENet transport/session: pin v1.3.18, explicit wire format and bounded messages; reject protocol, XEX and build mismatches; localhost success/failure tests.
2. Startup isolation: snapshot host saves and gameplay settings; client writes only to a fresh session directory. Reject unsafe paths, links, oversized or malformed snapshots; test round-trip and rejection.
3. Lockstep integration: fixed guest read indices, delayed local/remote controller states, deterministic flags enforced before runtime; no live fallback on lost connection. Test queue ordering, timeout/disconnect and a two-instance run.
4. Portal synchronization: schedule software-portal mutations on the same guest read on both peers; host-only figure persistence. Verify placement/removal and avoid modifying ordinary offline behavior.
5. Status/desync UX and documentation: expose session status, ping/delay and errors; bounded desync checks with known host-only memory ranges excluded; document limitations.

Checkpoint after every slice: focused tests, compile changed application code, review before claiming completion. Final: ctest, headless two-instance game smoke/replay where feasible, Windows cross-build if available.

Risks: same-machine visual replay agreement is weaker than cross-machine determinism; blocking input must retain the deterministic run token so host network timing cannot affect guest order; save paths and received sizes are trust boundaries. No rollback, matchmaking, relay or deployment in this slice.
