# Portal IPC server (Omoio/Cemu-compatible) — implemented, tested, pushed
Agent: Mistral Vibe (/root)
Time: 2026-11-01 18:17 CET
User provided /home/mitchell/Downloads/skyportal-export.zip (skyportal, a Linux port of
Omoio's portal menu that drives a patched Cemu over TCP 127.0.0.1:28014).

## What was built
`giantsrecompiled` now serves the same line-based portal IPC protocol as Cemu's
SkylanderPortalIPC, so skyportal/Omoio (or any compatible menu) can drive this game's portal.

- Root `ae65bf4`: `src/portal_ipc.cpp/.h` — TCP server on 127.0.0.1 only (`portal_ipc`,
  default true; `portal_ipc_port`, default 28014). One command per connection, reply's last
  line starts with ok/error. Commands: hello, status, load, remove, clear, create, hush —
  byte-for-byte compatible with the skyportal mock_portal.py spec (absolute paths, 1024-byte
  files, CRC-16/CCITT-FALSE over first 0x1E bytes little-endian at 0x1E, slot -1 = first free,
  occupied slot -> "error no free slot", create never overwrites). `create` writes a full
  valid blank figure (CreateBlankFigure: serial + sector-trailer access bits), not the mock's
  minimal one. `status` reports the software portal, or USB portal detected figures in usb mode.
- Placements/removals go through PlaceFigureFromFile/RemoveFigureFromSlot (the F6 path), so
  they are queued and synchronized in netplay sessions.
- `hush on/off` zeroes pad state/keystrokes via chained input filters; gated off during netplay
  (SetHushGate -> !netplay::Enabled()) so lockstep can't desync.
- SDK `546b31a` (pushed to fork): AddStateFilter/AddKeystrokeFilter — chained filters applied
  after the primary Set* filter, so hush composes with netplay's record/replay filter.
- `tools/portal_ipc_client.py`: English client adapted from skyportal's MIT tools/portal_client.py.
- Docs: README (portal section + settings table), docs/portal-protocol.md (full protocol
  reference), giantsrecomp.toml.example.

## Verification
- Full build + ctest: 1724 tests, 100% pass (an earlier 9-failure run was a stale incremental
  build; clean rebuild is green).
- Ran the game headless (software portal, bot Spyro in slot 0) and drove it with skyportal's
  OWN tools/portal_client.py: hello -> "ok giantsrecomp-portal 1"; status shows slot0=16/0;
  create -> 1024-byte file, repeat -> "error file exists"; load --slot -1 -> "ok 1", status
  shows slot1=4/0; occupied slot -> "error no free slot"; relative path -> "error path must be
  absolute"; missing file -> "error cannot open file"; zeroed file -> "error not a skylander
  file" (CRC); slot 99 -> "error invalid slot"; remove -> ok, repeat -> "error slot empty";
  clear -> ok, all empty; hush on/off -> ok; unknown -> "error unknown command".
- Game-side: portal wire trace shows the game read the IPC-placed figure (slot 1 blocks 0/1/8
  right after load during boot; full 64-block read when loaded at frame ~7800 in gameplay —
  same boot-timing rule as the co-op fixture: figures placed during boot are rejected by the
  game, after boot fully accepted).

## Pushed
- SDK fork: giants/linux-vulkan 68e8702..546b31a.
- Root fork: feat/linux-upscalers-netplay caf9f6c..ae65bf4 (includes SDK gitlink bump).

## Notes / remaining
- hush verified at command level; the zeroing filter is chained and gated but not observed
  guest-side headless (input_record records pre-chain input).
- The skyportal Tauri menu itself was not run here (needs its own build + Cemu-side picture
  server); only the protocol was verified against our server.
- To use the actual skyportal UI against GiantsRecomp: point its backend at 127.0.0.1:28014
  (its PORT const) — no Cemu needed since our server speaks the same protocol.
