# Giants netplay relay and lobby

Self-hosted WAN lobby plus a dumb two-endpoint UDP relay for Giants Recompiled
netplay. One Python 3.13 process, standard library only: no database, no Redis,
no third-party packages, no game code. The host creates a room, reads out an
8-character code, and the client joins it; gameplay then crosses the relay
without either player forwarding a port.

Direct `--netplay_host` / `--netplay_connect` mode is unchanged. The relay does
not speak Giants or ENet, does not add reliability, and never inspects payloads.

## Layout

| File | Purpose |
|---|---|
| `giants_relay.py` | Control socket + one relay socket per room, stdlib only |
| `Dockerfile` | `python:3.13-alpine`, uid/gid 10001, no privileges, UDP healthcheck |
| `docker-compose.yml` | Bridge networking, only UDP ports published, read-only root, JSON logs |
| `.env.example` | Deployment template |
| `tests/test_relay.py` | Protocol unit tests + loopback integration tests |
| `tests/two_process.py` | A real relay process plus two fake peer processes |

## Ports and firewall

| Port | Proto | Purpose |
|---|---|---|
| `32200` | UDP | Control/lobby: CREATE, JOIN, KEEPALIVE, HEALTH |
| `41100-41199` | UDP | One socket per active room (100 concurrent rooms) |

Forward **UDP 32200** and **UDP 41100-41199** on the router to the relay host.
These are new ports; existing forwards for other games do not cover them.
Verify from outside the LAN, not from the relay host itself.

## Protocol summary

All integers are big-endian and every datagram is at most 1500 bytes. The
15-byte header is `"GRLY"` (4s), version `u8`=1, kind `u8`, flags `u8`=0, and a
`u64` nonce; the reply echoes the request nonce.

| Kind | Direction | Body | Total bytes |
|---|---|---|---|
| 1 CREATE | host → control | `>HIH` protocol_version, build_id, gameplay_port | 23 |
| 2 CREATE_ACK | control → host | `>IH8sH` status, relay_port, room_code, ttl_s | 31 |
| 3 JOIN | client → control | `8sI` room_code, build_id | 27 |
| 4 JOIN_ACK | control → client | `>IBHH` status, relay_ipv4 (4 raw), relay_port, gameplay_port | 27 |
| 5 KEEPALIVE | host → control | `8s` room_code | 23 |
| 6 KEEPALIVE_ACK | control → host | `>I` status | 19 |
| 7 HEALTH | any → control | empty | 15 |
| 8 HEALTH_ACK | control → any | `>I` status + UTF-8 JSON | 19 + n |
| 9 ERROR | control → any | `>I` status | 19 |

Status values: 0 OK, 1 MALFORMED, 2 BAD_VERSION, 3 UNKNOWN_ROOM, 4 ROOM_FULL,
5 DUPLICATE_ROOM, 6 BUILD_MISMATCH, 7 NO_RELAY_PORT, 8 SERVER_FULL,
9 RATE_LIMITED, 10 NO_HELLO, 11 TOO_LARGE.

`CREATE` allocates the lowest free relay port and a unique 8-hex room code, and
stores the host's `build_id`, gameplay port and request nonce. `JOIN` is refused
on an unknown room, a different build, or a room whose two endpoints are
already bound. `KEEPALIVE` refreshes the room TTL.

### Relay port / GREL

The host sends a 21-byte hello from the **same ENet socket** that will carry
gameplay:

```
"GREL" (4s) | version u8=1 | room_code (8s) | nonce u64
```

The relay replies with the same layout using `"GRAK"`. The first valid hello
binds the host endpoint to the datagram's source address; later hellos are
consumed and acknowledged (a matching hello updates a NAT-rebound host). Any
datagram starting with `GREL` is never forwarded.

The first non-GREL datagram from a source other than the host claims the client
slot. All traffic is then forwarded between exactly those two endpoints; a
third source is dropped with no reply. If the client's first datagram arrives
before the host's hello, the relay holds the candidate client (and its latest
datagram), binds the host when `GREL` arrives, and flushes it. A host source on
a private or loopback address is forwarded to directly, so a relay on the same
LAN does not depend on router hairpin support.

Every forwarded datagram and every valid `KEEPALIVE` refreshes the room's 30 s
TTL. When the host goes quiet the room expires, its socket closes, and the port
is reused. There is no queueing, reordering or retry: ENet owns reliability.

`HEALTH` returns status 0 and JSON such as:

```json
{"sessions":1,"relays":1,"uptime":12.3,"counters":{"creates":1,"joins":1,"forwarded":240,"rate_limited":0,"rejected":0},"waiting":0,"active":1,"relay_ports_free":99}
```

The same JSON payload is capped at 400 bytes. Logs are one JSON object per line
on stdout.

## Run locally

```sh
cd /home/mitchell/Projects/GiantsRecomp
GIANTS_PUBLIC_IP=127.0.0.1 GIANTS_ADVERTISE_IP=127.0.0.1 \
  python3 server/giants_relay.py
```

The control port defaults to 32200 and the room range to 41100-41199. To test
without touching those ports, set `GIANTS_CONTROL_PORT` and adjust
`GIANTS_RELAY_MIN` / `GIANTS_RELAY_MAX`.

With Docker:

```sh
cd server
cp .env.example .env        # fill in GIANTS_PUBLIC_IP
docker compose up -d --build
docker logs -f giants-relay
docker inspect --format '{{.State.Health.Status}}' giants-relay
```

The healthcheck itself can be run from inside the container or the host:

```sh
python3 server/giants_relay.py --healthcheck
# or, inside the image:
docker exec giants-relay python /app/giants_relay.py --healthcheck
```

## Deploy on Unraid

Target: the Unraid box at `192.168.8.2`, directory `/root/giants-relay`,
Docker Compose v2.

```sh
ssh root@192.168.8.2 'mkdir -p /root/giants-relay'
scp -r server/giants_relay.py server/Dockerfile server/docker-compose.yml \
  server/.env.example server/README.md server/tests \
  root@192.168.8.2:/root/giants-relay/
ssh root@192.168.8.2 'cd /root/giants-relay && cp -n .env.example .env && vi .env'
ssh root@192.168.8.2 'cd /root/giants-relay && docker compose up -d --build'
ssh root@192.168.8.2 'docker inspect --format "{{.State.Health.Status}}" giants-relay'
```

`GIANTS_PUBLIC_IP` is required and must be the public IPv4 players send to.
On the router, forward **UDP 32200** and **UDP 41100-41199** to `192.168.8.2`.
For a LAN-only test before the forwards exist, set `GIANTS_ADVERTISE_IP` to
`192.168.8.2`.

Update with `docker compose up -d --build` after copying the files. There are no
volumes and no persistent state; a restart is always safe because every room
expires and the clients reconnect.

If relay throughput ever becomes a problem, publishing 100 UDP ports through
Docker's userland proxy is the first suspect; `network_mode: host` removes that
proxy but also removes the port isolation this compose file relies on.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `GIANTS_PUBLIC_IP` | required | Public IPv4 players send to |
| `GIANTS_ADVERTISE_IP` | `GIANTS_PUBLIC_IP` | Address returned in JOIN_ACK; LAN IP for testing |
| `GIANTS_CONTROL_PORT` | 32200 | Control/lobby UDP port |
| `GIANTS_RELAY_MIN` | 41100 | First room port |
| `GIANTS_RELAY_MAX` | 41199 | Last room port |
| `GIANTS_TTL` | 30 | Room idle lifetime in seconds |
| `GIANTS_MAX_SESSIONS` | 100 | Room-table cap (port range is the real ceiling) |
| `GIANTS_RATE_PER_SECOND` | 50 | Control tokens/second per source IP; relay sockets use 4x |
| `GIANTS_RATE_BURST` | 100 | Control burst per source IP; relay sockets use 4x |
| `GIANTS_LOG_LEVEL` | INFO | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |

## Tests

```sh
python3 server/tests/test_relay.py
python3 server/tests/two_process.py
```

`test_relay.py` covers exact wire sizes, truncation/trailing bytes, flags and
version rejection, token-bucket burst/refill, CREATE/JOIN/keepalive/health,
build mismatch, duplicate code, server/port exhaustion, GREL binding,
client-before-hello, two-peer forwarding, third-source rejection, oversized
datagrams and TTL expiry with port reuse.

`two_process.py` starts the real server as a child process, creates a room over
UDP, then runs separate host and client peer processes whose datagrams cross the
relay port. The client speaks first to exercise the held-client path.

Both tests use loopback and a private UDP range, and import nothing outside the
Python standard library.

## Security notes

Bounded parsing uses an exact expected length per kind, no attacker-controlled
lengths, and a hard 1500-byte datagram cap. Each source IP gets a token bucket
on the control socket (50/s, burst 100 by default) and on each relay socket
(4x). Room codes are 32 random bits and are never merged after a collision. The
relay forwards only between the two source addresses it has already learned, so
it cannot be used for reflection or to reach an arbitrary destination.

There is **no authentication and no encryption**. Anyone who learns an
8-character code inside its short lifetime can race the intended client for the
client slot. ENet remains unauthenticated. Treat relay sessions as trusted-peer
sessions, exactly like direct mode; do not use this for competitive or
untrusted opponents.
