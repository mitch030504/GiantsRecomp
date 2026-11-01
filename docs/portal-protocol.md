# Portal wire protocol

The command set and frame layout the game and a Portal of Power exchange. See
`docs/architecture.md` for where this fits (`PortalDevice`, `SoftwarePortal`, `UsbPortal`).

## Frames

Every frame, in both directions, is 32 bytes on the wire: on the Xbox 360 side that's the `0B 14`
header plus a 30-byte payload (`thirdparty/skylanders-portal/src/portal/xbox_frame.h`); on the raw `PortalDevice` side (and on
the real Wii U portal's own USB reports) it's the 32-byte payload with no header. The payload's
first byte is the command letter; unused bytes are zero.

## Commands

| Command | Payload | Meaning | Reply |
|---|---|---|---|
| `R` | `52` | ready poll, sent roughly every 300 ms until answered | `52 02 1B` |
| `A` | `41 <n>` | activate (`n=1`) / deactivate (`n=0`) | `41 <n> FF 77` |
| `S` | `53` | status poll | status frame, see below |
| `C` | `43 R G B` | set LED colour | none |
| `Q` | `51 <slot in low nibble> <block>` | read one 16-byte block of a figure | command byte + 16 data bytes |
| `W` | `57 <slot in low nibble> <block> <16 bytes>` | write one 16-byte block | none |

## Status frames

A read with no queued command reply returns a status frame instead of repeating the last reply:

```
0B 14 53 <4-byte little-endian slot states> <counter> <active>
```

Slot states are 2 bits per slot, slot 0 lowest: `0` empty, `1` present, `2` removing, `3` added.
The counter increments on every status frame; the active flag is 1 after an `A 01`. The game polls
the read side continuously (tens of times a second) regardless of whether a portal answers.

## Figure validation

A figure is read and written as its 64 16-byte blocks over `Q`/`W`. The game validates figure data:
an all-zero or otherwise invalid figure is accepted onto the portal but shown in-game as "A toy on
the Portal of Power has a problem." A figure with a correct block-0 checksum and a valid id/variant
at their fixed offsets is accepted and played normally, including progress writes back to block 8
and others during play. See `docs/figures.md` for the figure file format itself.

## External menu IPC (Omoio/Cemu-compatible)

`giantsrecompiled` also serves the line-based portal IPC that Cemu's `SkylanderPortalIPC` (and
the Omoio/skyportal menu built on it) speaks, so an external portal menu can drive this game's
portal. It listens on **127.0.0.1 only** (`portal_ipc_port`, default 28014; disable with
`portal_ipc=false`). One command per TCP connection: send one line (max 4096 bytes), read the
reply until close; the reply's last line starts with `ok` or `error`. Implemented in
`src/portal_ipc.cpp`; test with `tools/portal_ipc_client.py`.

| Command | Reply | Effect |
| --- | --- | --- |
| `hello` | `ok giantsrecomp-portal 1` | identity |
| `status` | `slot<N> loaded <id> <variant>` or `slot<N> empty` per slot 0-15, then `ok` | read portal state |
| `load <slot> <path>` | `ok <slot>` | place a figure file; `-1` = first free slot. Path must be absolute; the file must be exactly 1024 bytes with a valid CRC-16/CCITT-FALSE over the first 0x1E bytes (little-endian at 0x1E). Errors: `invalid slot`, `path must be absolute`, `cannot open file`, `not a skylander file`, `no free slot`, `no software portal`, `cannot place figure` |
| `remove <slot>` | `ok` | remove the figure; errors: `missing slot`, `invalid slot`, `slot empty` |
| `clear` | `ok` | remove all figures |
| `create <id> <variant> <path>` | `ok` | write a new blank figure file (never overwrites); errors: `missing path`, `invalid id`, `invalid variant`, `path must be absolute`, `file exists`, `cannot create file` |
| `hush on\|off` | `ok` | while on, the game reads a neutral pad (menu open). Ignored during netplay sessions so lockstep stays in sync |

Placements and removals go through the same path as the F6 overlay (`PlaceFigureFromFile` /
`RemoveFigureFromSlot`), so in a netplay session they are queued and synchronized like any other
figure change. `status` reports the software portal, or the USB portal's detected figures when
running in `usb` mode.
