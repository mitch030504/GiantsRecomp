# Giants relay: self-hosted WAN lobby and UDP relay

Status: implemented in `Giants server/giants_relay.py`, `src/netplay/lobby.cpp`, `src/netplay/session.cpp`
and the netplay cvars/UI; loopback protocol and two-process tests exist. WAN validation and the gaps
listed in §9 remain. Agent: opencode, 2026-11-05.

Reference implementation: `/home/mitchell/Projects/Sega-Superstars-Tennis-ReMatch/server/`.
Citations prefixed `Sega server/...` are that directory; `Giants server/...` is this repo's
`server/`; unprefixed `src/...` and `docs/...` are Giants. This document is the design and the
review of what is still missing.

## 1. Goal and constraints

Make a two-player Giants session work between any two machines without either player forwarding a
port, through one relay we host. The relay is a lobby (room-code exchange) plus a dumb two-endpoint
UDP forwarder. It does not speak Giants, does not touch lockstep, and does not add reliability.

**Direct mode stays the default and stays untouched.** `--netplay_host` / `--netplay_connect`
(`src/netplay/netplay.cpp:35-38`) keep their current meaning. Relay mode is selected by
`--netplay_lobby`; with it empty (or `--netplay_relay=off`) the executable behaves exactly as it
does today: no control datagrams, no intercept, no extra socket.

**The relay is a NAT solution, not an authentication boundary.** It answers "how do two players
reach each other when neither can accept an inbound port", not "who is allowed to play". ENet
remains unauthenticated and unencrypted (see §6). Treat relay sessions exactly like the direct
sessions `docs/netplay.md:35-39` already describes: trusted peers on a network you trust.

Constraints:

- **Self-hosted.** One Python process (stdlib only) on the Unraid box, one Docker container.
- **No third-party dependencies.** Server: Python 3.13 stdlib. Client: the ENet 1.3.18 already
  vendored in `thirdparty/enet` (`thirdparty/enet/include/enet/enet.h:26-28`). No new libraries, no
  database, no TLS stack.
- **One relay instance, 100 concurrent rooms.** Not a fleet; the relay port range is the hard
  ceiling (`GIANTS_RELAY_MIN`..`GIANTS_RELAY_MAX`, default 41100-41199).
- **No NAT traversal in v1.** There is no hole punching, no STUN, no candidate exchange, no
  direct-versus-relay selection. Relay mode is the fallback path for players who cannot forward a
  port; direct mode remains the fast path.
- **Unraid Docker deployment.** Target `/root/giants-relay` on `192.168.8.2`, Docker Compose
  v2.40.3, bridge networking, UDP 32200 plus 41100-41199.
- **Address handling.** Direct `--netplay_connect` remains numeric IPv4 only (`ParseEndpoint`,
  `src/netplay/session.cpp:62-76`). `--netplay_lobby` accepts `host:port` with a hostname or numeric
  IPv4; the syntax is parsed without I/O (`src/netplay/lobby.cpp:139-151`) and ENet resolves the
  host once on the worker thread (`lobby.cpp:180-183`), never on the UI thread.

Non-goals: a browsable server list, accounts, encryption, IPv6, spectator slots, rollback, adaptive
delay, relay-side matchmaking or skill matching.

### What is ported, and what is not

The Sega title uses its own `SSTD` directory protocol over guest sockets and rewrites `XNADDR`
advertisements inside an ad body (`Sega server/matchmaking.py:21-33`). None of that transfers to an ENet
host: we have no guest directory socket, no ad body, no title id, and no reason to make the relay
parse ENet. The **architecture** transfers:

| Behaviour | Sega reference | Giants implementation |
| --- | --- | --- |
| Control listener plus one data socket per room, allocated lowest-free from a fixed range | `Sega server/matchmaking.py:318-337`, `357-363`, `432` | **Ported**: `Giants server/giants_relay.py:495-547`, `764-816` |
| Forward only between the room's two learned peers; a third source is dropped with no reply | `Sega server/matchmaking.py:250-291`, especially `277-280` | **Ported**: `Giants server/giants_relay.py:964-996` |
| Consume relay-control datagrams before the forwarding decision | `Sega server/matchmaking.py:262-265`; `Sega server/quick_play.py:258-265` | **Ported for `GREL`**: `Giants server/giants_relay.py:919-922` |
| Bounded receive: 1500-byte ceiling, drop before parsing | `Sega server/matchmaking.py:83`, `253-259`; `Sega server/quick_play.py:250-257` | **Ported**: `Giants server/giants_relay.py:907-922` |
| Per-source token bucket; gameplay gets a larger budget than control | `Sega server/matchmaking.py:242-248`; `Sega server/quick_play.py:11-14`, `17-34` | **Ported at 4x** (control 50/s, relay 200/s): `Giants server/giants_relay.py:403-425`, `911-918` |
| Room TTL with heartbeat refresh, swept by a timer | `Sega server/matchmaking.py:87`, `445-457`, `662-673` | **Ported**: `Giants server/giants_relay.py:673-683`, `845-854` |
| Host heartbeat every 10 s | `Sega src/net/lan_sessions.cpp:155-161` | **Ported**: `src/netplay/lobby.cpp:254-257` |
| Host keeps its data-socket mapping alive while it waits for a client | `Sega src/net/lan_sessions.cpp:151-154`, `217-255` | **Partial**: `GREL` every 1 s in `kHello` (`lobby.cpp:248-257`), not yet in `kReady`; see §9.1 |
| Join returns the reachable endpoint and the host's gameplay port; the client adopts it | `Sega server/matchmaking.py:472-502`, especially `483-492`; `Sega src/net/lan_sessions.cpp:1182-1210` | **Ported**: `Giants server/giants_relay.py:818-843`; `src/netplay/lobby.cpp:321-340` |
| Nonce echo so a stale or spoofed reply cannot satisfy a request | `Sega src/net/lan_directory.cpp:419-421`, `435-437`; `Sega server/matchmaking.py:101-111` | **Ported for control replies**: `lobby.cpp:286-341`; ERROR/keepalive-ack nonces are not checked, see §9.6 |
| Co-located host: prefer the LAN endpoint and forward host-side hairpin replies without learning them | `Sega server/matchmaking.py:232-240`, `267-271`, `438-441` | **Partial**: a private/loopback `GREL` source is forwarded to directly; there is no explicit `GIANTS_SERVER_LAN_CIDR` seed. See the same-LAN hairpin bullet in §4 |
| Re-registration in place keeps the relay port and its learned peers | `Sega server/matchmaking.py:397-408` | **Not ported**: a lost `CREATE_ACK` currently allocates another room; see §9.2 |
| Unregister on teardown instead of waiting for TTL | `Sega server/matchmaking.py:459-470`; `Sega src/net/lan_sessions.cpp:853-858` | **Not ported**: v1 relies on the 30 s TTL; see §9.5 |
| Health kind carrying JSON, used as the container healthcheck | `Sega server/matchmaking.py:654-660`; `Sega server/Dockerfile:17-22` | **Ported**: `Giants server/giants_relay.py:856-888`, `1010-1030`; `Giants server/docker-compose.yml:40-45` |
| Non-root container, read-only rootfs, no-new-privileges, log rotation | `Sega server/Dockerfile:3-11`; `Sega server/docker-compose.yml:31-41` | **Ported**: `Giants server/Dockerfile:5-17`; `Giants server/docker-compose.yml:31-51` |
| Router-forward + public health-probe deployment pattern | `Sega server/README.md:60-71` | **Ported as a pattern**; the Giants ports are new, see §7 |

Not borrowed: the `SSTD` header/title id, directory/list/identity replies, `XNADDR` ad rewriting,
the Quick Play queue/tickets/challenges/probe reports, transport selection, the guest-socket
destination-port map, and the Sega server's thread-per-relay-socket implementation. The Sega relay
answers directory queries with the host's rewritten ad (`Sega server/matchmaking.py:211-213`,
`262-265`); Giants has no equivalent query, so our relay only consumes `GREL`/`GRAK` and forwards
everything else.

### Why this is a reimplementation

The Sega repository has **no LICENSE file**. Nothing here is copied from it. What is borrowed is
*behaviour*, re-derived for a different transport. Our wire format, port layout, client integration,
selector loop, room state and client-before-hello hold are new.

## 2. Topology and roles

```
host                         relay (control 32200, data 41100-41199)          client
  |  CREATE / KEEPALIVE (ENet socket)        |                                     |
  |--------------------------------------->|  allocate room + relay port         |
  |<------------- CREATE_ACK(code, port) --|                                     |
  |  GREL  from the *ENet* socket           |                                     |
  |--------------------------------------->|  bind host endpoint                 |
  |        GREL every 1 s while waiting     |                                     |
  |--------------------------------------->|                                     |
  |                                        |<---------- JOIN(code, build) -------|
  |                                        |------ JOIN_ACK(relay ip, port) ---->|
  |                                        |<===== ENet CONNECT (client) ========|
  |<===== ENet CONNECT (from relay) =======|                                     |
  |<=========== forwarding, both ways =====|============ forwarding ============>|
```

**Roles.**

- **Relay.** Two listeners. The *control* socket (fixed port) handles CREATE, JOIN, KEEPALIVE and
  HEALTH. The *relay* socket (one per room, from the range) forwards opaque datagrams between the
  room's two bound endpoints and consumes `GREL`/`GRAK`.
- **Host.** Creates its ENet host on `netplay_port`, sends CREATE from that ENet socket, sends
  `GREL` from the same socket, then waits. Sends GREL every 1 s while waiting and KEEPALIVE every
  10 s. `netplay_port` is the ENet bind port; the relay only uses it for diagnostics and the
  co-location path.
- **Client.** Creates an ephemeral ENet host, sends JOIN from that socket, then points its ENet
  connect at the relay endpoint from JOIN_ACK. No keepalive: its ENet traffic is the keepalive.

**Everything uses the ENet socket.** `LobbyClient::Send` calls `enet_socket_send(host.socket, ...)`
for CREATE, JOIN, KEEPALIVE and GREL (`src/netplay/lobby.cpp:125-131`). This is deliberate:

- The relay sees one source endpoint per peer. The host's CREATE, GREL and gameplay all come from
  the same public `IP:port`; the client's JOIN and ENet CONNECT do too. There is no second NAT
  mapping to keep alive and no control-socket port to leak.
- The replies arrive on the ENet socket. `host->intercept` (`thirdparty/enet/include/enet/enet.h:397`;
  the callback runs before command parsing in `thirdparty/enet/protocol.c:1264-1280`) consumes
  them before ENet sees them. `LobbyClient::Intercept` (`src/netplay/lobby.cpp:270-284`) returns 1
  for datagrams from the control endpoint and for `GRAK` from the relay endpoint; all other
  relay-endpoint datagrams fall through to ENet (`return 0`).
- `ENetHost.socket` is a public field (`enet.h:367`) and `enet_socket_send` is exported
  (`enet.h:505`). This is the whole enabler. The header says no fields should be modified unless
  stated; we only read `socket`, and `intercept` is an explicitly user-settable callback.

`GREL`/`GRAK` cannot collide with an ENet datagram: ENet's first field is a 16-bit peer id written
network-order (`thirdparty/enet/protocol.c:1026`, `1706`), so its first byte is in `0x00-0x0F` or
the flag range, never `'G'` (`0x47`). A 21-byte datagram beginning `GREL` is unambiguous.

**RTT reporting.** The relay is transparent, so a reliable packet and its ACK both cross it and
`peer->roundTripTime` stays a true end-to-end figure. The overlay keeps showing the same number
(`src/netplay/netplay.cpp:605`); it now includes the relay hop, which is what the player
experiences.

**Payload size.** `maximumPacketSize` is ENet's reliable *reassembly* ceiling, not the datagram
size; `host->mtu` is 1392 by default (`thirdparty/enet/host.c:85`), so every UDP datagram ENet emits
is under 1500. The relay's 1500-byte cap is safe. Do not raise ENet's MTU in relay mode.

## 3. Wire protocol

All integers are big-endian. There is no padding: 64-bit fields are unaligned, and both ends write
bytes by hand (`struct.pack` in Python, the `Put16/32/64` helpers in C++). Every message is at most
1500 bytes including the header. The server's structs are `HEADER = struct.Struct(">4sBBBQ")`
(15 bytes) and `HELLO = struct.Struct(">4sB8sQ")` (21 bytes), `Giants server/giants_relay.py:64-79`.

### Header (15 bytes)

| Offset | Size | Field |
| --- | --- | --- |
| 0 | 4 | magic `"GRLY"` |
| 4 | 1 | version, must be 1 |
| 5 | 1 | kind |
| 6 | 1 | flags, must be 0 |
| 7 | 8 | nonce u64, random per request, echoed in the reply |

`flags` is reserved and must be zero; a non-zero value is rejected rather than ignored, so a future
extension cannot be silently misread by an old peer. The server enforces this
(`Giants server/giants_relay.py:182-198`); the client parser currently checks magic and version but not
`flags` (`src/netplay/lobby.cpp:63-69`) — a one-line fix, see §9.6.

The nonce is the anti-stale-reply mechanism: a reply is discarded unless its nonce matches the
outstanding request, which also means a spoofed datagram cannot complete a handshake. It is not an
authentication token.

### Kinds

**1 CREATE** — host → control. Total 23 bytes. Body `>HIH`.

| Offset | Size | Field |
| --- | --- | --- |
| 15 | 2 | protocol_version u16, must be 1 |
| 17 | 4 | build_id u32 |
| 21 | 2 | gameplay_port u16 |

`protocol_version` is the relay protocol version, independent of ENet's netplay `kProtocol`
(`src/netplay/session.cpp:17`). `build_id` is the first eight hex characters of
`GIANTSRECOMP_BUILD_HASH` (`CMakeLists.txt:157-159`) read as a big-endian u32; both ends use the same
derivation (`src/netplay/lobby.cpp:100-113`). `gameplay_port` is the host's ENet port
(`netplay_port`, default 24872); it is stored for logging and echoed in JOIN_ACK, never used as a
forwarding destination for a remote host.

**2 CREATE_ACK** — control → host. Total 31 bytes. Body `>IH8sH`.

| Offset | Size | Field |
| --- | --- | --- |
| 15 | 4 | status u32 |
| 19 | 2 | relay_port u16 |
| 21 | 8 | room_code, 8 ASCII hex characters |
| 29 | 2 | ttl_s u16, 30 |

On a non-zero status, `relay_port` is 0, `room_code` is eight NUL bytes and `ttl_s` is 0.

**3 JOIN** — client → control. Total 27 bytes. Body `8sI`.

| Offset | Size | Field |
| --- | --- | --- |
| 15 | 8 | room_code |
| 23 | 4 | build_id u32 |

The code is 8 ASCII hex characters. The server generates lower case (`secrets.token_hex(4)`,
`Giants server/giants_relay.py:273-275`), the client canonicalises user input to upper case
(`src/netplay/lobby.cpp:171-179`), and the server lower-cases JOIN/KEEPALIVE before lookup
(`Giants server/giants_relay.py:818-820`, `845-847`). Either case works. The client rejects non-hex
input before sending (`ValidRoomCode`); the server currently lower-cases any 8 bytes and returns
UNKNOWN_ROOM for a non-hex code. If strictness is wanted, apply `ROOM_CODE_RE` in the JOIN parser.

**4 JOIN_ACK** — control → client. Total 27 bytes. Body `>I4sHH`.

| Offset | Size | Field |
| --- | --- | --- |
| 15 | 4 | status u32 |
| 19 | 4 | relay_ipv4, four raw octets in network order |
| 23 | 2 | relay_port u16 |
| 25 | 2 | gameplay_port u16 |

The body is `>I4sHH` (4 + 4 + 2 + 2 = 12 bytes). A literal `>IBHH` is a 9-byte typo: `B` is one
byte, not four. The server's encoder is `struct.pack(">I4sHH", ...)` (`Giants server/giants_relay.py:245-250`).

On a non-zero status, `relay_ipv4` is 0.0.0.0 and both ports are 0. `relay_ipv4` is
`GIANTS_ADVERTISE_IP`, not the control socket's source address, because the server may sit behind
its own NAT or Docker bridge (the Sega server rewrites the ad endpoint for the same reason:
`Sega server/matchmaking.py:365-376`). The client copies the four octets into `ENetAddress.host` in
network byte order (`src/netplay/lobby.cpp:329-337`).

**5 KEEPALIVE** — host → control. Total 23 bytes. Body `8s`: room_code.

**6 KEEPALIVE_ACK** — control → host. Total 19 bytes. Body `>I`: status.

**7 HEALTH** — any → control. Total 15 bytes. Empty body.

**8 HEALTH_ACK** — control → any. Total 19 + n bytes. Body `>I` + JSON.

| Offset | Size | Field |
| --- | --- | --- |
| 15 | 4 | status u32 |
| 19 | n | UTF-8 JSON, at most 400 bytes |

JSON keys: `sessions`, `relays`, `uptime`, `counters`, `waiting`, `active`, `relay_ports_free`,
`registers`, `joins`, `expired`, `rate_limited`, `rejected` (`Giants server/giants_relay.py:860-888`). The
`counters` object adds `creates`, `joins`, `keepalives`, `hellos`, `forwarded`, `expired`,
`rate_limited`, `rejected`, `malformed`, `oversized`, `dropped`. No room codes, no addresses, no
payload bytes.

**9 ERROR** — control → any. Total 19 bytes. Body `>I`: code. Best effort and amplification-safe:
it is only sent in response to a datagram whose 15-byte header parsed (so a nonce can be echoed)
and whose source is not rate-limited (`Giants server/giants_relay.py:717-743`). ERROR is not an
inbound kind; a client that sends one gets MALFORMED back, and the client never answers an ERROR, so
there is no loop.

### Host hello (relay port, not the control port)

**GREL** — host → relay, 21 bytes, `>4sB8sQ`.

| Offset | Size | Field |
| --- | --- | --- |
| 0 | 4 | `"GREL"` |
| 4 | 1 | version, must be 1 |
| 5 | 8 | room_code |
| 13 | 8 | nonce, the CREATE request's nonce |

**GRAK** — relay → host, same 21-byte layout with magic `"GRAK"` and the same nonce echoed. The
server sends it on every valid GREL (`Giants server/giants_relay.py:924-947`); the client consumes it in
`LobbyClient::Intercept` (`lobby.cpp:276-280`). It is a diagnostic/acknowledgement, never forwarded
to the other peer.

`GREL` deliberately has a different magic and length from an ENet datagram so the relay can consume
it with one `memcmp` before any forwarding decision, and so a stray `GRLY` message on a relay port
is not mistaken for one. Note: the server checks only `data[:4] == b"GREL"` and otherwise forwards;
a `GRLY` datagram on a relay port would be treated as gameplay. It is harmless (ENet will drop it)
but a strict "drop `GRLY` on relay ports" rule is a cheap cleanup.

### Worked example

A CREATE from protocol 1, build `0x12345678`, gameplay port 24872 (`0x6128`), nonce `0x2A`:

```
47 52 4c 59  01 01 00  00 00 00 00 00 00 00 2a   GR LY  v k f  nonce
00 01  12 34 56 78  61 28                        proto  build  port
```

The matching successful CREATE_ACK for relay port 41123 (`0xA0A3`), code `abcd1234`, TTL 30:

```
47 52 4c 59 01 02 00 00 00 00 00 00 00 00 2a      header, kind 2
00 00 00 00  a0 a3  61 62 63 64 31 32 33 34  00 1e
status 0      port  code "abcd1234"           ttl 30
```

### Status and failure codes

Shared by every `status` and `ERROR` field.

| Code | Name | Meaning |
| --- | --- | --- |
| 0 | OK | |
| 1 | MALFORMED | wrong length, trailing bytes, non-zero flags, unknown kind |
| 2 | BAD_VERSION | header version or CREATE `protocol_version` is not 1 |
| 3 | UNKNOWN_ROOM | no such room, or it expired; also a non-hex JOIN code in the current server |
| 4 | ROOM_FULL | both endpoints already bound |
| 5 | DUPLICATE_ROOM | room-code collision after bounded regeneration (CREATE) |
| 6 | BUILD_MISMATCH | JOIN `build_id` differs from the room's CREATE `build_id` |
| 7 | NO_RELAY_PORT | relay port range exhausted |
| 8 | SERVER_FULL | room table at capacity |
| 9 | RATE_LIMITED | token bucket empty (normally dropped silently, never amplified) |
| 10 | NO_HELLO | reserved: the implementation holds a client that arrives before GREL instead of returning this (§4) |
| 11 | TOO_LARGE | datagram over 1500 bytes (reserved; the current server drops it silently) |

## 4. State machine

A room is `(code, relay_port, socket, nonce, build_id, gameplay_port, created, last_seen, host,
client, pending_client, buckets)` (`Giants server/giants_relay.py:439-456`). There is no explicit enum; the
state is the pair `(host, client)` plus an optional held client datagram:

| State | Meaning |
| --- | --- |
| EMPTY | no room |
| WAITING | room exists, `host is None`, `client is None` |
| PENDING | `host is None`, one candidate client datagram is held |
| HELLO | `host` set, `client is None` |
| ACTIVE | `host` and `client` set |
| EXPIRED | room removed, socket closed, port free |

| From | Trigger | Guard | Action | Reply |
| --- | --- | --- | --- | --- |
| EMPTY | CREATE | table not full, a free relay port, unique code after ≤ 8 draws | bind relay socket, remember nonce/build/port | CREATE_ACK OK |
| EMPTY | CREATE (protocol_version != 1) | — | — | CREATE_ACK BAD_VERSION |
| EMPTY | CREATE (table full / no port / code collision) | — | — | CREATE_ACK SERVER_FULL / NO_RELAY_PORT / DUPLICATE_ROOM |
| WAITING / PENDING / HELLO | JOIN | code found, build matches, `not room.active` | — | JOIN_ACK OK (even before GREL) |
| WAITING | JOIN (unknown / build mismatch / active) | — | — | JOIN_ACK UNKNOWN_ROOM / BUILD_MISMATCH / ROOM_FULL |
| WAITING | first non-GREL datagram | `host is None` | hold `(src, data)` | — (PENDING) |
| PENDING | another non-GREL from the held source | — | replace the held datagram | — |
| PENDING | non-GREL from a different source | — | drop | — |
| WAITING / PENDING / HELLO | GREL | 21 bytes, magic/version/code/nonce match | bind/rebind host, send GRAK; if a client is held, bind it and flush the held datagram | GRAK |
| HELLO | non-GREL from the host | — | no client yet, drop | — |
| HELLO | non-GREL from a source other than the host | — | bind client, forward this datagram to the host | — (ACTIVE) |
| ACTIVE | datagram from host | `src == host` | forward to client | — |
| ACTIVE | datagram from client | `src == client` | forward to host | — |
| ACTIVE | datagram from a third source | — | drop, no reply | — |
| ACTIVE | GREL | nonce matches | update host endpoint (NAT rebind), send GRAK | GRAK |
| any | KEEPALIVE | code found | refresh `last_seen` | KEEPALIVE_ACK OK |
| any | KEEPALIVE | unknown code | — | KEEPALIVE_ACK UNKNOWN_ROOM |
| any | HEALTH | — | — | HEALTH_ACK OK + JSON |
| any | malformed control after a full header | header parsed, not rate-limited | — | ERROR (1 or 2) |
| any | oversized datagram | — | drop | — (TOO_LARGE reserved) |
| any | rate-limited | — | drop | — |
| any | TTL 30 s | `now - last_seen >= ttl` | close socket, free port, delete room | — (EXPIRED) |

The host endpoint is the room's binding anchor: a client that flaps cannot destroy the host's
registration. The room TTL is refreshed by either peer's forwarded traffic, the host's KEEPALIVE and
a valid GREL, so a dead client's slot is **not** reclaimed while the host is alive. A dead host is
usually noticed by the client's ENet timeout first; the room then expires 30 s after the last
forwarded packet or KEEPALIVE. A second client is a third source and is dropped while the slot is
occupied. The missing per-endpoint client TTL is a known gap; see §9.3 and §9.8.

### Client-before-hello, not NO_HELLO

This is the one place the implementation deliberately differs from the obvious strict design. A
client can JOIN as soon as the room exists, before the host's GREL. The client's ENet CONNECT then
arrives at the relay while `host is None`. Instead of refusing the join (`NO_HELLO`), the relay
holds **one** candidate client endpoint and its latest datagram (`WAITING → PENDING`). When the
host's GREL arrives, the relay binds the host, binds the held client, and flushes that datagram
(`Giants server/giants_relay.py:948-959`). The client's ENet handshake then continues normally.

This is better than `NO_HELLO` for the common case (GREL is lost or delayed by a few hundred ms)
because it saves a JOIN retry RTT. It has two costs:

- The relay holds up to 1500 bytes of unauthenticated data per room until the host appears. Bounded
  by the room table, acceptable.
- Any source that sends a non-GREL datagram before the host is held as the candidate client. A
  relay-port scanner can occupy the pending slot. When the real client arrives from a different
  source, `_forward` drops its datagram because `pending_client` is set and the source differs
  (`Giants server/giants_relay.py:969-975`); it is not replaced. A scanner can therefore deny the
  real client until the host's GREL flushes the wrong endpoint, after which the real client is a
  third source. This is the same class of race as the client-slot claim in §6; a JOIN-IP
  reservation is the fix (§9.3).

`NO_HELLO` (status 10) is defined but unused. Keep it for a future strict mode or for a
control-only join that refuses to hand out the relay endpoint before GREL.

### Synchronous NAT and the GREL cadence

This is the part that is easy to get wrong. The host's ENet socket is behind NAT. For a symmetric
NAT, the mapping is per-destination: the host's socket only accepts inbound from the exact
`relay_ip:relay_port` it has sent to. `KEEPALIVE` goes to the **control** port (32200) and does
nothing for the mapping to the **relay** port (411xx). If the host GRELs once at startup and then
waits, a NAT with a 30 s UDP timeout expires the 411xx mapping; the relay's forwarded ENet CONNECT
and handshake are then dropped, and the host looks dead while it is still running.

Therefore the host must send `GREL` to the relay endpoint for the whole room lifetime, not just at
startup and on rebind. This both refreshes the mapping and lets the relay update the host endpoint
if the NAT assigns a new public port. The cost is one 21-byte datagram per interval.

Current implementation: `LobbyClient::Tick` sends GREL every **1 s** in `kHello` (waiting for a
client) and every 10 s KEEPALIVE (`src/netplay/lobby.cpp:248-257`). In `kReady` (client connected)
it sends only KEEPALIVE every 10 s (`lobby.cpp:259-264`); it **stops GREL**. That is sufficient
while gameplay traffic is flowing, because the host's ENet datagrams refresh the mapping, but it is
not sufficient for an idle menu or a paused session where ENet has nothing to send. The requirement
is to keep GREL at 10 s in `kReady` as well; see §9.1. A 1 s cadence while waiting is harmless
(21 bytes/s) but more than needed; 10 s is the intended steady state.

The client side has the mirror image: its ENet traffic to the relay port keeps its mapping alive.
Its JOIN control datagrams share the same socket, so there is no second mapping. If the client is
idle long enough for its NAT mapping to expire, the relay's client slot also expires; a later ENet
datagram from a new public port can claim the client slot again, because the server does not pin
the slot to the JOIN source. A mid-session NAT rebind is not transparent; see §9.8.

### Failure and race cases

- **Host death.** No GREL, no KEEPALIVE and no forwarded traffic for 30 s → EXPIRED, port freed.
  The client sees ENet give up first: `enet_peer_timeout(peer, 8, 3000, 10000)`
  (`src/netplay/session.cpp:268`) is a 10 s ceiling, so the overlay pauses with
  "Netplay peer disconnected" (`session.cpp:277-278`; `netplay.cpp:269-278`) about 20 s before the
  room is reclaimed. That gap is the main argument for an unregister kind in v2 (§9.5).
- **Client death.** The room stays ACTIVE with the dead client's slot, because the host's KEEPALIVE
  keeps the room TTL fresh and there is no separate client TTL. The host's ENet peer disconnects
  after its own 10 s timeout, so the host surfaces the failure and pauses permanently (`Pause`
  retains the run token). A replacement client cannot claim the slot; recovery is a new session, not
  a transparent reconnect. A per-endpoint client TTL is the fix (§9.3).
- **Duplicate room.** Codes are 32 random bits; regenerate on collision, at most 8 draws, then
  DUPLICATE_ROOM. Never reuse a code that is still live, even if the requester's build matches
  (`Giants server/giants_relay.py:764-769`).
- **Build mismatch.** JOIN compares its `build_id` with the room's. A mismatch returns
  BUILD_MISMATCH before any endpoint is handed out, so a wrong build costs no relay port
  (`Giants server/giants_relay.py:818-843`). This is a coarse gate only; the authoritative check remains the
  64-hex `build_hash` comparison in the ENet handshake (`src/netplay/session.cpp:128-132`), which
  still runs and still rejects.
- **Relay port exhaustion.** Allocate the lowest free port from the configured range
  (`Giants server/giants_relay.py:771-784`). If none is free, CREATE fails with NO_RELAY_PORT and no room is
  created. Never silently return port 0.
- **Room table exhaustion.** `GIANTS_MAX_SESSIONS` caps rooms independently of the port range; a
  CREATE beyond it gets SERVER_FULL (`Giants server/giants_relay.py:792-794`). Per-IP create rate limiting is the
  first defence; a distributed fill is a residual DoS (§6).
- **Hello/client race.** Covered by the hold-and-flush path above. A GREL that arrives after the
  room is ACTIVE is a rebind, not an error, as long as the nonce and code match.
- **CREATE_ACK loss.** The client resends CREATE every 1 s for up to 10 s with the same nonce
  (`lobby.cpp:200-217`, `234-246`), but the server has no `(source, nonce)` idempotency: every
  retry allocates a new room and relay port (`Giants server/giants_relay.py:786-816`). The abandoned rooms have
  no GREL and expire after 30 s, but ten lost ACKs can fill the 100-room table. This is the
  highest-value server fix in §9.2.
- **Same-LAN hairpin.** The server does not have an explicit LAN-CIDR rule. What happens depends on
  the GREL source: if the host is on the relay's LAN and its packet is delivered with the private
  source, the relay binds and sends to that private endpoint directly (`sendto` routes on the LAN).
  If the router hairpins the GREL to the public address, the relay binds the public endpoint and
  sends back through the router; that works only if the router supports NAT loopback. For a
  LAN-only test, set `GIANTS_ADVERTISE_IP=192.168.8.2` so both peers use the LAN path. An explicit
  `GIANTS_SERVER_LAN_CIDR` seed, as the Sega server does, would remove the router dependency; see
  §9.7.
- **Relay restart.** All rooms are lost; there is no persistence. The host's KEEPALIVE gets
  UNKNOWN_ROOM, but `LobbyClient` currently ignores KEEPALIVE_ACK status (`lobby.cpp:297`), so the
  host notices only when ENet times out (10 s). The client's JOIN gets UNKNOWN_ROOM. The host
  re-CREATEs only after the user restarts the session; a new code must be exchanged out of band.
  Handling UNKNOWN_ROOM in the keepalive path would surface the failure immediately (§9.6).
- **Startup snapshot burst.** The host sends up to 16 MiB of save data (`src/netplay/session.h:13`,
  `kMaxStartupBytes`) as ENet fragments. At ENet's 1392-byte MTU that is ~12k datagrams. The relay's
  data-socket bucket is 4x the control rate = 200/s, burst 400 (`Giants server/giants_relay.py:911-918`), so a
  worst-case snapshot takes ~60 s and collides with the default 60 s netplay timeout. This is a
  real risk; see §9.4.

### Implementation status and remaining work

Already in place:

1. Server: control + per-room sockets, selector loop, CREATE/JOIN/KEEPALIVE/HEALTH, bounded parsing,
   token buckets, TTL, GREL binding, GRAK, hold-and-flush, healthcheck (`Giants server/giants_relay.py`).
2. Client: `LobbyClient` state machine, ENet-socket control, intercept, room code and relay endpoint
   in `SessionStatus`, host/client paths (`src/netplay/lobby.cpp`, `session.cpp`).
3. App: `netplay_lobby`, `netplay_room`, `netplay_relay` cvars, validation, F8 overlay and game-menu
   fields (`src/netplay/netplay.cpp:45-50`, `src/game_menu.cpp:42-44`, `299`, `320-343`).
4. Tests: `server/tests/test_relay.py` (protocol + in-process integration, 21 tests),
   `server/tests/two_process.py` (real server + two peer processes), `tests/netplay_lobby_test.cpp`
   (endpoint/room-code parsing, registered as ctest `netplay_lobby`), and
   `tools/netplay_relay_smoke.py` (headless host + client through the relay).

Remaining, in priority order:

1. GREL refresh in `kReady` (symmetric NAT; §9.1).
2. CREATE idempotency per `(source IP, nonce)` (§9.2).
3. Client-slot binding decision: JOIN-IP reservation or accept the scanner race (§9.3).
4. Relay bucket vs the 16 MiB snapshot (§9.4).
5. Room-code logging policy (§9.6).
6. WAN tests: public hairpin, netem, disconnect/recovery, direct-mode regression (§8).
7. Optional: explicit LAN seed/hairpin rule (§9.7), UNREGISTER (§9.5), client re-JOIN (§9.8).

## 5. Relay rules

Per room, on the relay socket:

1. **Exactly two endpoint slots.** The host slot is bound by a valid `GREL`; the client slot by the
   first non-`GREL` datagram from a source other than the host. Before the host appears, one
   candidate client datagram is held and flushed when `GREL` arrives. There is no destination field
   anywhere in the protocol, so a packet cannot ask the relay to send anywhere.
2. **Forward only between the two.** A datagram from the host goes to the client and vice versa.
   Never to the source, never to a third party, never broadcast. A third source is dropped with no
   reply (`Sega server/matchmaking.py:277-280`; Giants: `Giants server/giants_relay.py:985-991`).
3. **Consume control datagrams.** On a relay socket, a datagram beginning `GREL` is handled and
   never forwarded; `GRAK` is generated, never forwarded. The server does not currently drop
   `GRLY` on a relay port; it would be forwarded as opaque data (harmless, but a cleanup).
4. **Drop oversized and undersized.** `> 1500` bytes is dropped before parsing; control messages
   require the exact body length for their kind; `GREL` must be exactly 21 bytes. No partial reads,
   no length-prefixed expansion (`Giants server/giants_relay.py:182-211`, `907-936`).
5. **Per-source token buckets.** Control: 50/s, burst 100, keyed by source IP. Relay: 4x that,
   200/s burst 400, also keyed by source IP (`RELAY_RATE_MULTIPLIER = 4.0`,
   `Giants server/giants_relay.py:911-918`). Bucket tables are bounded (4096 control, 256 relay) and idle entries
   are evicted after 300 s (`Giants server/giants_relay.py:140-143`, `687-701`). The 4x rate is a straight port of
   the Sega rule (`Sega server/quick_play.py:11-14`); it is **not** sized for the 16 MiB startup
   snapshot, see §9.4.
6. **TTL 30 s, keepalive 10 s, GREL 1 s while waiting (10 s in `kReady` once §9.1 is fixed).** Any
   valid control message or forwarded datagram refreshes the room's `last_seen`; the host's
   KEEPALIVE covers idle menus; GREL covers the host's data-socket mapping. The selector's timeout
   is capped at 0.5 s and expiry is checked every loop (`Giants server/giants_relay.py:602-636`,
   `673-683`).
7. **No queueing.** The relay does not buffer, reorder or retry. A dropped datagram is dropped, and
   ENet decides what that means. The only buffer is the single held client datagram in PENDING.
8. **One selector loop, one socket per room.** Borrow the Sega socket-per-room layout, but not its
   thread-per-socket implementation (`Sega server/matchmaking.py:229-230`). A single
   `selectors.DefaultSelector()` registers the control socket, a wake socketpair and every relay
   socket; one thread receives, decides and forwards (`Giants server/giants_relay.py:522-535`, `602-626`). At 100
   rooms this avoids 100 Python threads and 100 × 2 ms poll loops competing for the GIL.
9. **Co-location is implicit.** A `GREL` whose source is a private or loopback address is forwarded
   to directly; there is no explicit CIDR seed and no special hairpin branch. See the same-LAN hairpin bullet in §4 and §9.7.
10. **The relay never parses ENet.** It cannot distinguish input frames from a disconnect notice.
    That is deliberate: `enet_peer_disconnect_now` (`session.cpp:295`) is forwarded like any other
    datagram, and the peer's ENet state machine handles it.

## 6. Security

- **Bounded parsing.** A fixed 1500-byte receive buffer, an exact expected length per kind, no
  trailing bytes, `flags == 0`, magic and version checked before anything else. A malformed
  datagram costs one comparison and is dropped; nothing allocates from an attacker-controlled
  length. The server is strict (`Giants server/giants_relay.py:182-211`); the client parser should also check
  `flags` (§9.6).
- **Random room codes.** Eight characters from `[0-9a-f]`, generated with `secrets.token_hex(4)`
  (32 bits, `Giants server/giants_relay.py:273-275`). The parser accepts either case and normalises on lookup.
  Collision probability with 100 live rooms is about 1e-6, and a collision is retried rather than
  merged. A code is a short-lived capability: anyone who learns it inside its 30 s window and races
  the intended client can take the client slot. The host slot is stronger: GREL must echo the
  64-bit CREATE nonce, which the client never sees.
- **Build check.** JOIN carries a 32-bit build id and the relay refuses a mismatch, but that is
  only a cheap compatibility filter; the exact build hash and XEX hash checks in the ENet handshake
  remain the authority (`session.cpp:128-132`).
- **No arbitrary forwarding.** Endpoints are learned from source addresses, never supplied in a
  body. There is no way to make the relay emit a datagram to an address it has not already seen
  traffic from. This is the property that rules out reflection and amplification.
- **No secrets in logs — currently violated.** The server logs the room code at INFO on create,
  join, the client-before-hello flush and expiry (`Giants server/giants_relay.py:811-813`, `835-837`,
  `958-962`, `682-683`), and logs the source IP on create/join. If room codes are capabilities,
  `docker logs` is a capability leak. The fix is a random per-room `room_id`
  (`secrets.token_hex(4)` at CREATE) in the log record instead of the code; the host already shows
  the code locally. This is §9.6. Logs are JSON lines and never include payload bytes, nonces or
  save data.
- **Constant-time comparisons for capabilities.** The GREL nonce is compared with `!=`
  (`Giants server/giants_relay.py:933-934`); use `secrets.compare_digest` for that. Room-code lookup
  is a dict lookup after lower-casing; the code is a capability, not a password, so do not
  over-engineer it.
- **ENet is still unauthenticated.** Say it plainly: there is no key exchange, no encryption, no
  integrity check beyond ENet's own checksum, and no proof that the machine claiming a room is the
  host that created it. The relay protocol itself is plaintext. The mitigations are narrow: codes
  are random, short lived, shared out of band, the host slot is bound by a 64-bit nonce the client
  never sees, the relay binds only two endpoints, and the pending-client hold is bounded. They are
  not authentication. A real fix means wrapping ENet in Noise or DTLS, which is out of scope for v1
  and would need both ends changed together. Treat relay sessions as trusted-peer sessions, exactly
  as `docs/netplay.md:35-39` says for direct mode.
- **JOIN is a room-existence oracle.** 32-bit codes and the control rate limit make enumeration
  impractical for a friends-and-family relay, but a botnet can guess harder. The room can live as
  long as the host keeps sending KEEPALIVE, so the window is not strictly 30 s. Not an auth
  boundary; do not put anything valuable behind a room code.
- **Client-slot race.** The first non-`GREL` datagram from a source other than the host claims the
  client slot. A scanner that finds the relay port can claim it before the real client, and the
  real client's datagrams are then a third source and are dropped for as long as the host keeps the
  room alive; there is no separate client TTL. JOIN does not reserve the source IP. This is the
  cheapest remaining hardening step (§9.3); it is still not authentication.
- **Residual DoS.** A distributed attacker can fill the 100-room table or the 100-port range with
  CREATEs and let TTL recycle them. Global capacity, TTL and per-IP control rate limiting bound the
  damage; there is no per-IP room cap in v1 (§9.12).
- **Container hardening.** Non-root uid 10001, read-only root filesystem, `no-new-privileges`,
  `cap_drop: ALL`, `tmpfs: /tmp`, no docker socket, no host network, only the two UDP ranges
  published (`Giants server/Dockerfile:5-17`, `Giants server/docker-compose.yml:31-45`).

## 7. Deployment

Target: the Unraid box at `192.168.8.2`, directory `/root/giants-relay`, Docker Compose v2.40.3
(the compose file uses only v2 syntax).

```
giants-relay/
  giants_relay.py     control + relay sockets, stdlib only
  Dockerfile          python:3.13-alpine, uid 10001, no capabilities
  docker-compose.yml  bridge, publishes the control port + relay range
  .env.example        deployment template
  README.md           operator notes
  tests/test_relay.py protocol + in-process integration
  tests/two_process.py real server + two peer processes
```

Ports:

| Port | Proto | Purpose |
| --- | --- | --- |
| 32200 | UDP | control: CREATE, JOIN, KEEPALIVE, HEALTH |
| 41100-41199 | UDP | one socket per room, 100 rooms |

Environment (`Giants server/README.md:154-167`, `Giants server/.env.example`):

| Variable | Default | Notes |
| --- | --- | --- |
| `GIANTS_CONTROL_PORT` | 32200 | |
| `GIANTS_RELAY_MIN` | 41100 | first room port |
| `GIANTS_RELAY_MAX` | 41199 | last room port; range size is the hard capacity |
| `GIANTS_TTL` | 30 | seconds; advertised in CREATE_ACK |
| `GIANTS_MAX_SESSIONS` | 100 | room-table cap; cannot exceed the range |
| `GIANTS_RATE_PER_SECOND` | 50 | control socket; relay sockets use 4x |
| `GIANTS_RATE_BURST` | 100 | control socket; relay sockets use 4x |
| `GIANTS_PUBLIC_IP` | required | what players send to |
| `GIANTS_ADVERTISE_IP` | `GIANTS_PUBLIC_IP` | address returned in JOIN_ACK; set to `192.168.8.2` for LAN testing |
| `GIANTS_LOG_LEVEL` | INFO | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |

`RelayConfig.from_env` also accepts the older plan spellings `GIANTS_RELAY_PORT_MIN/MAX`,
`GIANTS_SESSION_TTL` and `GIANTS_MAX_ROOMS` as fallbacks (`Giants server/giants_relay.py:375-395`), so an existing
`.env` keeps working. Use the canonical names above in new deployments.

`GIANTS_PUBLIC_IP` is required and the server refuses to start without it (`Giants server/giants_relay.py:1063-1066`):
advertising the container's bridge address would hand players an unroutable endpoint, and a silent
failure here is much worse than a startup error (`Giants server/docker-compose.yml:21` does the same with
`:?`). Validate it with `ipaddress.IPv4Address`; reject `0.0.0.0` and multicast. `GIANTS_ADVERTISE_IP`
may be a LAN address for testing, but then only LAN clients can reach it.

Deploy:

```sh
ssh root@192.168.8.2 'mkdir -p /root/giants-relay'
scp -r server/giants_relay.py server/Dockerfile server/docker-compose.yml \
  server/.env.example server/README.md server/tests \
  root@192.168.8.2:/root/giants-relay/
ssh root@192.168.8.2 'cd /root/giants-relay && cp -n .env.example .env && vi .env'
ssh root@192.168.8.2 'cd /root/giants-relay && docker compose version && docker compose up -d --build'
ssh root@192.168.8.2 'docker inspect --format "{{.State.Health.Status}}" giants-relay'
```

Update: `docker compose up -d --build` after copying the files. No volumes, no state; a restart is
always safe — every room expires and clients reconnect.

### Router and public reachability

The Sega deployment already forwards **UDP 32100** and **UDP 41000-41099** to `192.168.8.2`, and its
public health probe has been verified; the procedure is in `Sega server/README.md:69-71`. That
proves the router path and the probe shape, not the Giants ports. The Giants ranges are separate;
confirm UDP 32200 and 41100-41199 are forwarded (or add them) before announcing the relay. The
Giants server README is explicit that these are new ports and existing forwards do not cover them
(`Giants server/README.md:30-32`). Verify from outside the LAN, not from the Unraid host:

```sh
python3 - <<'PY'
import socket, struct
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.settimeout(3)
probe = struct.pack(">4sBBBQ", b"GRLY", 1, 7, 0, 1234)   # 15-byte header, kind 7
s.sendto(probe, ("<public-ip>", 32200))
data, _ = s.recvfrom(1500)
assert data[:4] == b"GRLY" and data[4] == 1 and data[5] == 8
status = struct.unpack_from(">I", data, 15)[0]
print(status, data[19:])
PY
```

The server also has a built-in probe, which is what the container healthcheck runs:

```sh
python3 server/giants_relay.py --healthcheck
# or, inside the image:
docker exec giants-relay python /app/giants_relay.py --healthcheck
```

It sends HEALTH (kind 7) to `127.0.0.1:GIANTS_CONTROL_PORT`, checks the nonce and expects kind 8
with status 0 (`Giants server/giants_relay.py:1010-1030`). The probe must use the 15-byte header
(`>4sBBBQ`), not the Sega 20-byte header; the two protocols are different and a copied Sega probe
will be rejected as MALFORMED.

### Container

The image and compose file follow the Sega shape (`Giants server/Dockerfile:1-36`,
`Giants server/docker-compose.yml:1-51`):

- `FROM python:3.13-alpine`, user `giants` uid/gid 10001, `WORKDIR /app`.
- `PYTHONDONTWRITEBYTECODE=1` and `PYTHONUNBUFFERED=1` so a read-only rootfs never sees a
  `__pycache__` write and logs are unbuffered.
- `EXPOSE 32200/udp` and `EXPOSE 41100-41199/udp`.
- `HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3` running
  `python /app/giants_relay.py --healthcheck`.
- Compose: `restart: unless-stopped`, bridge networking, `read_only: true`,
  `security_opt: [no-new-privileges:true]`, `cap_drop: [ALL]`, `tmpfs: /tmp`,
  `stop_grace_period: 5s`, JSON logs capped at 10 MB × 3.
- Publish only `${GIANTS_CONTROL_PORT:-32200}:${GIANTS_CONTROL_PORT:-32200}/udp` and
  `${GIANTS_RELAY_MIN:-41100}-${GIANTS_RELAY_MAX:-41199}:${GIANTS_RELAY_MIN:-41100}-${GIANTS_RELAY_MAX:-41199}/udp`.
- No privileged mode, no docker socket, no host network.

Two operational notes:

- Publishing 100 UDP ports goes through Docker's userland proxy. It works, but if relay throughput
  becomes a problem the fallback is `network_mode: host`, which is a one-line change and removes the
  proxy from the data path. On Unraid, host networking also means the container sees the host's
  interfaces directly; re-check `GIANTS_ADVERTISE_IP` afterwards.
- One socket per room means 100 file descriptors plus the control, wake and selector fds. Python's
  default limits are ample, but check `ulimit -n` in the container if the range is ever widened. A
  failed healthcheck marks the container unhealthy but does not restart it; use an external monitor
  if the relay is left unattended.

### Client configuration

`src/netplay/netplay.cpp:45-50` defines the three cvars; `Configure` validates them at
`netplay.cpp:311-339`:

| Mode | Flags |
| --- | --- |
| Direct host | `--netplay_host=true --netplay_port=24872` |
| Direct client | `--netplay_connect=192.168.1.10:24872` |
| Relay host | `--netplay_host=true --netplay_lobby=203.0.113.10:32200` |
| Relay client | `--netplay_lobby=203.0.113.10:32200 --netplay_room=ABCD1234` |
| Force direct | add `--netplay_relay=off` (ignores a set `netplay_lobby`) |

`netplay_relay` defaults to `auto`: relay is used exactly when `netplay_lobby` is non-empty and the
mode is not `off` (`netplay.cpp:281-283`). A relay host must not set `netplay_room`; a relay client
must set it and must not set `netplay_connect`; `netplay_room` requires `netplay_lobby`
(`netplay.cpp:324-339`). All three are `kRequiresRestart`, like the existing netplay cvars.

The host shows the room code on the F8 overlay (the server logs it, which is how the smoke tool
learns it); the client echoes the code it is joining. The overlay also shows the relay endpoint and
the room state (creating/joining/waiting/connected) from `SessionStatus`
(`src/netplay/session.h:34-44`, `src/netplay/netplay.cpp:608-609`). The game menu has the same
fields (`src/game_menu.cpp:123-124`, `299`, `320-343`).

## 8. Test plan

**A. Server protocol and integration** (`server/tests/test_relay.py`, 20 stdlib `unittest` cases,
no game):

- Exact wire sizes and the unaligned nonce; truncation at every offset and trailing bytes rejected;
  bad versions, non-zero flags and foreign magic rejected.
- `CREATE`/`JOIN`/unknown room, build mismatch, duplicate code, server full, no relay port.
- GREL binding, two-peer forwarding, third-source rejection, client-before-hello hold/flush,
  client-before-hello across process boundaries (`server/tests/two_process.py`).
- Token-bucket burst/refill; TTL expiry, keepalive refresh and port reuse; oversized datagrams;
  health JSON shape and in-process healthcheck.
- Add: CREATE idempotency per `(source IP, nonce)` if §9.2 is implemented; GREL-in-ready refresh;
  room-code logging policy; client-slot reservation if §9.3 is implemented; a 16 MiB-equivalent
  burst against the relay bucket (§9.4).

**B. Client unit test** (`tests/netplay_lobby_test.cpp`, ctest `netplay_lobby`): endpoint parsing
(hostname and numeric, bad ports/hosts), room-code validation (upper/lower, length, non-hex). Add
parser tests for non-zero flags, wrong version, short/trailing control replies, and ERROR nonce
checking once §9.6 is fixed.

**C. Two-process loopback.** `tools/netplay_relay_smoke.py` starts the real server on loopback,
launches the headless host, reads the room code from the server's JSON log, launches the client,
and applies the `netplay_smoke.py` checks (identical inputs, matching frames, isolated saves). Run
it with `--seconds=120` and assert the session reaches ready, the 16 MiB startup snapshot completes,
and HEALTH reports zero rooms after both exit or after `GIANTS_TTL` if the host is killed.

**D. Public address (hairpin).** Both processes on `192.168.8.x`, `GIANTS_ADVERTISE_IP` set to the
public IPv4, so both peers reach the relay through the router's loopback. Assert join and 60 s of
play. Repeat with `GIANTS_ADVERTISE_IP=192.168.8.2`. Record which combination works: a failure here
is a router capability, not a code defect, and the fix belongs in the deployment notes or §9.7.

**E. Latency and loss.** On the loopback path, `tc qdisc add dev lo root netem delay 50ms loss 1%`,
then 100 ms. Assert: handshake completes inside 5 s, no desync, ping tracks the injected delay, and
the session survives the loss without a stall (ENet retransmits; the relay must not duplicate).
Then repeat on the real WAN path at 100 ms and note the `netplay_delay` needed to cover it —
lockstep is fixed-delay, so added RTT shows up as waiting, not as desync (`docs/netplay.md:83-84`;
`src/netplay/lockstep.cpp:56-81`).

**F. Disconnect and recovery.** `kill -9` the host mid-session: client pauses within 10 s, the room
disappears within 30 s, a JOIN to the old code returns UNKNOWN_ROOM, and a fresh session works.
`kill -9` the client: the host pauses within 10 s; the room stays ACTIVE with the dead client's slot
(the host's KEEPALIVE keeps the room TTL fresh), so a replacement client cannot claim it. This is
the known client-slot gap (§9.3/§9.8). Restart the
relay container mid-session: both peers fail cleanly with no hang, and new sessions work afterwards.
Restart it while a host is waiting between CREATE and GREL: the host's retry gets a new room or
times out with a clear error. Stop the host's GREL for 40 s while leaving the process alive: confirm
the relay's host endpoint goes stale and a new client cannot connect, then confirm periodic GREL in
`kReady` restores it (this is the symmetric-NAT regression from §9.1).

**G. Direct-mode regression.** `ctest --test-dir out/build/linux-amd64-release` unchanged, plus
`tools/netplay_smoke.py` with no relay cvar set. Assert no control datagram is sent, no intercept is
installed, no relay thread/socket is created, and the log and frame comparison match the pre-change
run. This is the gate that keeps "direct stays default" honest.

Pass criteria: A, B, C and G green before the relay is deployed at all; D and E green before it is
announced to another player; F green before it is left running unattended.

## 9. Open questions and decisions

1. **GREL refresh in `kReady`.** Current `LobbyClient::Tick` sends GREL every 1 s only while
   waiting for a client (`src/netplay/lobby.cpp:248-257`) and stops in `kReady` (`lobby.cpp:259-264`).
   A symmetric NAT can expire the 411xx mapping during an idle menu, after which the relay's
   forwarded traffic is dropped. Decision: send GREL every 10 s in `kReady` too, and keep the 1 s
   burst while waiting. This is the one change the symmetric-NAT requirement makes mandatory; it is
   small and low-risk.
2. **CREATE idempotency.** The client retries CREATE every 1 s for 10 s with the same nonce
   (`lobby.cpp:200-217`), but the server allocates a new room on every retry. Decision: index rooms
   by `(source IP, nonce)` for the room's lifetime and return the existing code/port on a retry.
   This is the same in-place-update idea as the Sega re-registration (`Sega server/matchmaking.py:397-408`)
   and removes a 10-room leak per lost ACK. It is the highest-value server fix.
3. **Client-slot binding.** The current rule is "first non-GREL datagram from a source other than
   the host claims the client slot" (`Giants server/giants_relay.py:976-984`). A scanner can steal it. Options:
   (a) record the JOIN source IP and only accept the first client datagram from that IP for 10 s;
   (b) add a client hello analogous to GREL; (c) leave it and rely on the code being short-lived.
   Decision for v1: implement (a) if the code change is small; it is a cheap binding, not
   authentication, and it also makes the PENDING hold less abusable. A spoofer on the same public
   IP can still race it. The same pass should add a separate `client_last_seen` that clears the
   client slot after 30 s of client inactivity, so a dead client does not hold the room forever
   while the host keeps sending KEEPALIVE.
4. **Relay bucket vs the startup snapshot.** The 4x rule (200/s, burst 400) is correct for gameplay
   but wrong for the 16 MiB snapshot: ~12k datagrams at 200/s is ~60 s, which collides with the
   default `netplay_timeout_seconds=60`. Options: raise the relay bucket to ~2000/s burst 4000
   (enough that ENet's congestion control decides the rate), add a one-shot burst credit for the
   first N seconds of a room, or compress the snapshot. Decision: raise the relay bucket for v1 and
   measure; compression is the better long-term fix but changes the session payload. Do not lower
   the control bucket.
5. **UNREGISTER now, or rely on TTL?** The v1 wire table has no unregister kind. Relying on TTL
   leaks a relay port for up to 20 s after ENet has already declared the peer dead (10 s peer
   timeout vs 30 s TTL), and a crashed host that never exits cleanly holds it for the full 30 s.
   With 100 ports that is acceptable for v1. Decision: TTL is the authority; add kind 10
   UNREGISTER in a v2 protocol revision (one handler, `8s` room code, answered with
   KEEPALIVE_ACK's status shape). Never let a third-party UNREGISTER free someone else's room.
6. **Room codes in logs and parser strictness.** The server logs room codes and source IPs at INFO
   (`Giants server/giants_relay.py:811-813`, `835-837`, `958-962`, `682-683`); if codes are capabilities, that is
   a leak. Decision: log a random `room_id` instead of the code. In the same pass, make the client
   parser check `flags == 0` (`lobby.cpp:63-69`), check the nonce on ERROR and KEEPALIVE_ACK rather
   than only on CREATE_ACK/JOIN_ACK (`lobby.cpp:292-298`), and handle KEEPALIVE_ACK status
   `UNKNOWN_ROOM` (currently ignored at `lobby.cpp:297`) so a relay restart surfaces before the
   10 s ENet timeout. These are small correctness and hygiene fixes, not protocol changes.
7. **Explicit LAN seed/hairpin.** The server currently relies on the GREL source being the host's
   private address for a co-located host. The Sega server seeds the host's LAN endpoint from the
   register request and forwards host-side hairpin replies without learning them
   (`Sega server/matchmaking.py:232-240`, `267-271`, `438-441`). Decision: add `GIANTS_SERVER_LAN_CIDR`
   and seed `(create_src, gameplay_port)` when the CREATE source is inside it; treat other datagrams
   from that CIDR as host-side hairpin replies for that room only. This removes the router's NAT
   loopback from the LAN path. Deferred until a real LAN test fails without it.
8. **Client re-JOIN after a half-open connect.** The client has no GREL-equivalent hello, so the
   relay cannot distinguish a NAT-rebound client from a third source. The room TTL does **not** clear
   the client slot while the host is alive, so a mid-session NAT rebind is not transparent and a
   dead client's slot stays bound. A per-endpoint client TTL (or a client hello, `b"GREC"` + code +
   nonce) is the fix. It is not in v1 because it adds another control datagram and magic to the
   parser.
9. **IPv6.** No. Numeric IPv4 only for direct mode and the relay endpoints. The lobby hostname may
   resolve to IPv4 only; `ENetAddress` is IPv4. This is a v1 scope decision, not a permanent one.
10. **UI surface.** Relay mode is cvar + game-menu fields now (`src/game_menu.cpp:123-124`,
    `299`, `320-343`), with the room code and relay endpoint on the F8 overlay. The host needs a code
    it can read out, so the overlay text matters more than the input field. A copy-to-clipboard
    button is a later polish item.
11. **Authentication.** Deferred, with the honest caveat in §6. If it is ever added it must not be
    bolted onto the relay alone — the two game peers have to agree on it too, otherwise the relay is
    only authenticating itself to the clients. A Noise/DTLS wrapper around ENet is the realistic
    shape, and it changes both ends together.
12. **Per-IP room cap.** Global `GIANTS_MAX_SESSIONS=100` only. A per-source CREATE cap (e.g. 4 live
    rooms) would blunt a single-host fill; it is a small table and a cheap addition, but it also
    breaks a LAN party behind one NAT. Deferred until an abuse case appears.
13. **Relay observability.** HEALTH JSON is enough for v1. If the relay is left unattended, add a
    periodic external probe (the same kind 7/8 exchange) and alert on `status != 0`, a rising
    `rejected` counter, or `relay_ports_free == 0`.

### Implementation status (2026-11-05)

Applied in the v1 code: GREL refresh in `kReady` (every 5 s), CREATE idempotency keyed by
(source, nonce), relay bucket raised to 20x the control rate (1000/s, burst 2000), client header
`flags` rejection, ERROR nonce checking, and KEEPALIVE_ACK room-loss handling. Not applied:
client-slot JOIN binding, UNREGISTER, client-side inactivity expiry, code hiding in logs, a
per-IP room cap, and authentication. The same-LAN path uses the relay's LAN address; the public-IP
hairpin is not relied on.
