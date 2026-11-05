#!/usr/bin/env python3
"""Giants netplay lobby + UDP relay (stdlib only, Python 3.13).

One process, two kinds of UDP listener:

* the control socket (default 0.0.0.0:32200) speaks the GRly lobby protocol:
  CREATE, JOIN, KEEPALIVE, HEALTH and ERROR;
* one relay socket per room (default 41100-41199) forwards opaque datagrams
  between exactly two learned endpoints and consumes GREL/GRAK hello packets.

The relay never parses ENet.  Gameplay stays end-to-end: ENet's handshake,
reliability and ordering cross the relay untouched.  This is a lobby plus a
two-endpoint forwarder, not a game protocol implementation.

Wire format (big-endian, every message <= 1500 bytes):

  header  15 bytes  "GRLY" (4s), version u8=1, kind u8, flags u8=0, nonce u64
  1  CREATE        >HIH     protocol_version, build_id, gameplay_port
  2  CREATE_ACK    >IH8sH   status, relay_port, room_code, ttl_s
  3  JOIN          8sI      room_code, build_id
  4  JOIN_ACK      >IBHH    status, relay_ipv4(4 raw), relay_port, gameplay_port
  5  KEEPALIVE     8s       room_code
  6  KEEPALIVE_ACK >I       status
  7  HEALTH        empty
  8  HEALTH_ACK    >I + UTF-8 JSON (status, sessions, relays, uptime, counters)
  9  ERROR         >I       status

  GREL/GRAK        >4sB8sQ  magic, version=1, room_code, nonce

Security properties: bounded parsing with exact lengths, no attacker-supplied
lengths, per-source token buckets, random 32-bit room codes, a hard two-endpoint
cap (no reflection: the relay only ever sends to an address it has received
from), and a 30 s TTL with keepalive refresh.  There is no authentication or
encryption; treat relay sessions as trusted-peer sessions.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import logging
import os
import re
import secrets
import selectors
import signal
import socket
import struct
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Iterable, Mapping

# ---------------------------------------------------------------------------
# Protocol constants
# ---------------------------------------------------------------------------

MAGIC = b"GRLY"
VERSION = 1
MAX_DATAGRAM = 1500
MAX_PACKET = MAX_DATAGRAM  # alias used by older deployment notes
HEADER = struct.Struct(">4sBBBQ")
HEADER_SIZE = HEADER.size  # 15
HELLO_MAGIC = b"GREL"
HELLO_ACK_MAGIC = b"GRAK"
HELLO = struct.Struct(">4sB8sQ")
HELLO_SIZE = HELLO.size  # 21

KIND_CREATE = 1
KIND_CREATE_ACK = 2
KIND_JOIN = 3
KIND_JOIN_ACK = 4
KIND_KEEPALIVE = 5
KIND_KEEPALIVE_ACK = 6
KIND_HEALTH = 7
KIND_HEALTH_ACK = 8
KIND_ERROR = 9

KIND_NAMES = {
    KIND_CREATE: "CREATE",
    KIND_CREATE_ACK: "CREATE_ACK",
    KIND_JOIN: "JOIN",
    KIND_JOIN_ACK: "JOIN_ACK",
    KIND_KEEPALIVE: "KEEPALIVE",
    KIND_KEEPALIVE_ACK: "KEEPALIVE_ACK",
    KIND_HEALTH: "HEALTH",
    KIND_HEALTH_ACK: "HEALTH_ACK",
    KIND_ERROR: "ERROR",
}

STATUS_OK = 0
STATUS_MALFORMED = 1
STATUS_BAD_VERSION = 2
STATUS_UNKNOWN_ROOM = 3
STATUS_ROOM_FULL = 4
STATUS_DUPLICATE_ROOM = 5
STATUS_BUILD_MISMATCH = 6
STATUS_NO_RELAY_PORT = 7
STATUS_SERVER_FULL = 8
STATUS_RATE_LIMITED = 9
STATUS_NO_HELLO = 10
STATUS_TOO_LARGE = 11

STATUS_NAMES = {
    STATUS_OK: "OK",
    STATUS_MALFORMED: "MALFORMED",
    STATUS_BAD_VERSION: "BAD_VERSION",
    STATUS_UNKNOWN_ROOM: "UNKNOWN_ROOM",
    STATUS_ROOM_FULL: "ROOM_FULL",
    STATUS_DUPLICATE_ROOM: "DUPLICATE_ROOM",
    STATUS_BUILD_MISMATCH: "BUILD_MISMATCH",
    STATUS_NO_RELAY_PORT: "NO_RELAY_PORT",
    STATUS_SERVER_FULL: "SERVER_FULL",
    STATUS_RATE_LIMITED: "RATE_LIMITED",
    STATUS_NO_HELLO: "NO_HELLO",
    STATUS_TOO_LARGE: "TOO_LARGE",
}

# Inbound control messages and their exact body sizes.  Replies are not
# accepted on the control socket; a peer sending one is MALFORMED.
_INBOUND_BODIES: dict[int, struct.Struct | None] = {
    KIND_CREATE: struct.Struct(">HIH"),
    KIND_JOIN: struct.Struct(">8sI"),
    KIND_KEEPALIVE: struct.Struct("8s"),
    KIND_HEALTH: None,
}

ROOM_CODE_RE = re.compile(rb"[0-9a-f]{8}\Z")
# Relay sockets carry the initial save snapshot (up to 16 MiB) as well as gameplay: 4x the control
# rate throttled a large snapshot for most of the 60 s handshake. 20x leaves headroom while still
# bounding a single room to 1000 packets/s by default.
RELAY_RATE_MULTIPLIER = 20.0
DEFAULT_CONTROL_PORT = 32200
DEFAULT_RELAY_MIN = 41100
DEFAULT_RELAY_MAX = 41199
DEFAULT_TTL = 30.0
DEFAULT_MAX_SESSIONS = 100
DEFAULT_RATE_PER_SECOND = 50.0
DEFAULT_RATE_BURST = 100

# Bounds for bucket tables so spoofed sources cannot grow memory without limit.
MAX_CONTROL_BUCKETS = 4096
MAX_RELAY_BUCKETS = 256
BUCKET_IDLE_SECONDS = 300.0


class ProtocolError(Exception):
    """A GRly datagram that parsed as ours but is not a valid message."""

    def __init__(self, status: int, reason: str = "") -> None:
        self.status = status
        self.reason = reason or STATUS_NAMES.get(status, str(status))
        super().__init__(self.reason)


class ConfigError(ValueError):
    """Invalid server configuration."""


# ---------------------------------------------------------------------------
# Wire helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Header:
    kind: int
    nonce: int


@dataclass(frozen=True)
class Message:
    kind: int
    nonce: int
    body: tuple


def encode_header(kind: int, nonce: int, *, version: int = VERSION, flags: int = 0) -> bytes:
    """Pack a 15-byte GRly header (version/flags overridable for tests)."""
    return HEADER.pack(MAGIC, version, kind, flags, nonce & 0xFFFFFFFFFFFFFFFF)


def parse_header(data: bytes) -> Header:
    """Parse and validate a header.

    Raises ProtocolError for anything that carries our magic but is not a
    well-formed header.  The caller should check the magic first if it wants
    to drop foreign datagrams silently.
    """
    if len(data) < HEADER_SIZE:
        raise ProtocolError(STATUS_MALFORMED, "short header")
    magic, version, kind, flags, nonce = HEADER.unpack_from(data, 0)
    if magic != MAGIC:
        raise ProtocolError(STATUS_MALFORMED, "bad magic")
    if version != VERSION:
        raise ProtocolError(STATUS_BAD_VERSION, "bad header version")
    if flags != 0:
        raise ProtocolError(STATUS_MALFORMED, "non-zero flags")
    return Header(kind, nonce)


def parse_message(data: bytes) -> Message:
    """Parse one complete inbound control message with an exact body length."""
    header = parse_header(data)
    if header.kind not in _INBOUND_BODIES:
        raise ProtocolError(STATUS_MALFORMED, "unknown or reply kind")
    body_struct = _INBOUND_BODIES[header.kind]
    body_size = 0 if body_struct is None else body_struct.size
    if len(data) != HEADER_SIZE + body_size:
        raise ProtocolError(STATUS_MALFORMED, "wrong message length")
    body = () if body_struct is None else body_struct.unpack_from(data, HEADER_SIZE)
    return Message(header.kind, header.nonce, body)


def encode_create(nonce: int, protocol_version: int = 1, build_id: int = 0,
                  gameplay_port: int = 0) -> bytes:
    return encode_header(KIND_CREATE, nonce) + struct.pack(
        ">HIH", protocol_version, build_id & 0xFFFFFFFF, gameplay_port & 0xFFFF)


def encode_join(nonce: int, room_code: bytes, build_id: int) -> bytes:
    if len(room_code) != 8:
        raise ValueError("room code must be exactly 8 bytes")
    return encode_header(KIND_JOIN, nonce) + struct.pack(
        ">8sI", room_code, build_id & 0xFFFFFFFF)


def encode_keepalive(nonce: int, room_code: bytes) -> bytes:
    if len(room_code) != 8:
        raise ValueError("room code must be exactly 8 bytes")
    return encode_header(KIND_KEEPALIVE, nonce) + struct.pack("8s", room_code)


def encode_health(nonce: int) -> bytes:
    return encode_header(KIND_HEALTH, nonce)


def encode_create_ack(nonce: int, status: int, relay_port: int = 0,
                      room_code: bytes = bytes(8), ttl_s: int = 0) -> bytes:
    if len(room_code) != 8:
        raise ValueError("room code must be exactly 8 bytes")
    return encode_header(KIND_CREATE_ACK, nonce) + struct.pack(
        ">IH8sH", status, relay_port & 0xFFFF, room_code, ttl_s & 0xFFFF)


def encode_join_ack(nonce: int, status: int, relay_ipv4: bytes = bytes(4),
                    relay_port: int = 0, gameplay_port: int = 0) -> bytes:
    if len(relay_ipv4) != 4:
        raise ValueError("relay_ipv4 must be four raw octets")
    return encode_header(KIND_JOIN_ACK, nonce) + struct.pack(
        ">I4sHH", status, relay_ipv4, relay_port & 0xFFFF, gameplay_port & 0xFFFF)


def encode_keepalive_ack(nonce: int, status: int) -> bytes:
    return encode_header(KIND_KEEPALIVE_ACK, nonce) + struct.pack(">I", status)


def encode_error(nonce: int, status: int) -> bytes:
    return encode_header(KIND_ERROR, nonce) + struct.pack(">I", status)


def encode_health_ack(nonce: int, payload: bytes) -> bytes:
    return encode_header(KIND_HEALTH_ACK, nonce) + struct.pack(">I", 0) + payload


def encode_grel(room_code: bytes, nonce: int) -> bytes:
    return HELLO.pack(HELLO_MAGIC, VERSION, room_code, nonce)


def encode_grak(room_code: bytes, nonce: int) -> bytes:
    return HELLO.pack(HELLO_ACK_MAGIC, VERSION, room_code, nonce)


def new_room_code() -> bytes:
    """Eight lowercase ASCII hex characters, from 32 random bits."""
    return secrets.token_hex(4).encode("ascii")


def _ipv4(value: str, field_name: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    try:
        address = ipaddress.IPv4Address(text)
    except ipaddress.AddressValueError as exc:
        raise ConfigError(f"{field_name} must be a numeric IPv4 address: {value!r}") from exc
    if address.is_multicast or int(address) == 0xFFFFFFFF:
        raise ConfigError(f"{field_name} must be a unicast IPv4 address: {value!r}")
    return str(address)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def _env_value(env: Mapping[str, str], names: Iterable[str], default: str) -> str:
    for name in names:
        value = env.get(name)
        if value is not None and value != "":
            return value
    return default


def _env_int(env: Mapping[str, str], names: Iterable[str], default: int) -> int:
    raw = _env_value(env, names, str(default))
    try:
        return int(raw, 10)
    except ValueError as exc:
        raise ConfigError(f"{names[0]} must be an integer: {raw!r}") from exc


def _env_float(env: Mapping[str, str], names: Iterable[str], default: float) -> float:
    raw = _env_value(env, names, str(default))
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{names[0]} must be a number: {raw!r}") from exc


@dataclass(frozen=True)
class RelayConfig:
    """Server configuration, normally built with :meth:`from_env`."""

    control_port: int = DEFAULT_CONTROL_PORT
    relay_min: int = DEFAULT_RELAY_MIN
    relay_max: int = DEFAULT_RELAY_MAX
    ttl: float = DEFAULT_TTL
    max_sessions: int = DEFAULT_MAX_SESSIONS
    rate_per_second: float = DEFAULT_RATE_PER_SECOND
    rate_burst: int = DEFAULT_RATE_BURST
    public_ip: str = ""
    advertise_ip: str = ""
    log_level: str = "INFO"
    bind_host: str = "0.0.0.0"

    def __post_init__(self) -> None:
        object.__setattr__(self, "control_port", int(self.control_port))
        object.__setattr__(self, "relay_min", int(self.relay_min))
        object.__setattr__(self, "relay_max", int(self.relay_max))
        object.__setattr__(self, "ttl", float(self.ttl))
        object.__setattr__(self, "max_sessions", int(self.max_sessions))
        object.__setattr__(self, "rate_per_second", float(self.rate_per_second))
        object.__setattr__(self, "rate_burst", int(self.rate_burst))
        object.__setattr__(self, "log_level", str(self.log_level).upper())
        if not 0 <= self.control_port <= 65535:
            raise ConfigError("GIANTS_CONTROL_PORT must be 0-65535")
        if not 1 <= self.relay_min <= 65535 or not 1 <= self.relay_max <= 65535:
            raise ConfigError("relay ports must be 1-65535")
        if self.relay_min > self.relay_max:
            raise ConfigError("GIANTS_RELAY_MIN must not exceed GIANTS_RELAY_MAX")
        if self.control_port and self.relay_min <= self.control_port <= self.relay_max:
            raise ConfigError("GIANTS_CONTROL_PORT must not be inside the relay range")
        if self.ttl <= 0:
            raise ConfigError("GIANTS_TTL must be greater than zero")
        if self.max_sessions < 1:
            raise ConfigError("GIANTS_MAX_SESSIONS must be at least 1")
        if self.rate_per_second <= 0:
            raise ConfigError("GIANTS_RATE_PER_SECOND must be greater than zero")
        if self.rate_burst < 1:
            raise ConfigError("GIANTS_RATE_BURST must be at least 1")
        public = _ipv4(self.public_ip, "GIANTS_PUBLIC_IP")
        advertise = _ipv4(self.advertise_ip, "GIANTS_ADVERTISE_IP") or public
        object.__setattr__(self, "public_ip", public)
        object.__setattr__(self, "advertise_ip", advertise)

    @property
    def relay_port_count(self) -> int:
        return self.relay_max - self.relay_min + 1

    @property
    def ttl_seconds(self) -> int:
        # The protocol carries u16 seconds; sub-second values are test-only.
        return max(1, min(65535, int(round(self.ttl))))

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "RelayConfig":
        env = os.environ if env is None else env
        # The second name in each tuple accepts the older plan/deployment
        # spelling so an existing .env keeps working.
        return cls(
            control_port=_env_int(env, ("GIANTS_CONTROL_PORT",), DEFAULT_CONTROL_PORT),
            relay_min=_env_int(env, ("GIANTS_RELAY_MIN", "GIANTS_RELAY_PORT_MIN"),
                               DEFAULT_RELAY_MIN),
            relay_max=_env_int(env, ("GIANTS_RELAY_MAX", "GIANTS_RELAY_PORT_MAX"),
                               DEFAULT_RELAY_MAX),
            ttl=_env_float(env, ("GIANTS_TTL", "GIANTS_SESSION_TTL"), DEFAULT_TTL),
            max_sessions=_env_int(env, ("GIANTS_MAX_SESSIONS", "GIANTS_MAX_ROOMS"),
                                  DEFAULT_MAX_SESSIONS),
            rate_per_second=_env_float(env, ("GIANTS_RATE_PER_SECOND",),
                                       DEFAULT_RATE_PER_SECOND),
            rate_burst=_env_int(env, ("GIANTS_RATE_BURST",), DEFAULT_RATE_BURST),
            public_ip=env.get("GIANTS_PUBLIC_IP", ""),
            advertise_ip=env.get("GIANTS_ADVERTISE_IP", ""),
            log_level=env.get("GIANTS_LOG_LEVEL", "INFO"),
        )


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


class TokenBucket:
    """Per-source token bucket with an injectable clock for unit tests."""

    def __init__(self, rate: float, burst: int,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.rate = float(rate)
        self.burst = float(burst)
        self.tokens = float(burst)
        self.clock = clock
        self.updated = clock()
        self.lock = threading.Lock()

    def allow(self, now: float | None = None) -> bool:
        with self.lock:
            current = self.clock() if now is None else now
            elapsed = current - self.updated
            if elapsed > 0:
                self.tokens = min(self.burst, self.tokens + elapsed * self.rate)
                self.updated = current
            if self.tokens >= 1.0:
                self.tokens -= 1.0
                return True
            return False


# ---------------------------------------------------------------------------
# Room state
# ---------------------------------------------------------------------------


@dataclass
class _PendingClient:
    endpoint: tuple[str, int]
    data: bytes


@dataclass
class Room:
    code: bytes
    relay_port: int
    sock: socket.socket
    nonce: int
    create_src: tuple[str, int] | None
    build_id: int
    gameplay_port: int
    created: float
    last_seen: float
    host: tuple[str, int] | None = None
    client: tuple[str, int] | None = None
    pending_client: _PendingClient | None = None
    buckets: dict[str, TokenBucket] = field(default_factory=dict)

    @property
    def active(self) -> bool:
        return self.host is not None and self.client is not None


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

log = logging.getLogger("giants-relay")
log.addHandler(logging.NullHandler())


class _JsonFormatter(logging.Formatter):
    _STANDARD = set(vars(logging.LogRecord("", 0, "", 0, "", (), None)).keys())

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        payload: dict[str, object] = {
            "time": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
                "+00:00", "Z"),
            "level": record.levelname,
            "msg": message,
            "event": message,
        }
        for key, value in record.__dict__.items():
            if key not in self._STANDARD and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=False, default=str)


def setup_logging(level: str = "INFO") -> None:
    """Configure one JSON-lines handler on stdout (idempotent)."""
    log.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())
    log.addHandler(handler)
    log.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    log.propagate = False


class RelayServer:
    """Control socket + per-room relay sockets on one selector loop."""

    def __init__(self, config: RelayConfig | None = None) -> None:
        self.config = config or RelayConfig.from_env()
        self._rooms: dict[bytes, Room] = {}
        self._control_buckets: dict[str, TokenBucket] = {}
        self._counters: dict[str, int] = {
            "creates": 0,
            "joins": 0,
            "keepalives": 0,
            "hellos": 0,
            "forwarded": 0,
            "expired": 0,
            "rate_limited": 0,
            "rejected": 0,
            "malformed": 0,
            "oversized": 0,
            "dropped": 0,
        }
        self._started = time.monotonic()
        self._running = False
        self._in_loop = False
        self._closed = False
        self._thread: threading.Thread | None = None
        self._lifecycle = threading.Lock()

        self._selector = selectors.DefaultSelector()
        self._control = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self._control.bind((self.config.bind_host, self.config.control_port))
        except OSError:
            self._control.close()
            raise
        self._control.setblocking(False)
        self.control_port = self._control.getsockname()[1]
        self._selector.register(self._control, selectors.EVENT_READ, ("control", None))
        self._wake_r, self._wake_w = socket.socketpair()
        self._wake_r.setblocking(False)
        self._wake_w.setblocking(False)
        self._selector.register(self._wake_r, selectors.EVENT_READ, ("wake", None))
        self._advertise_ipv4 = socket.inet_aton(self.config.advertise_ip or "0.0.0.0")
        log.info(
            "relay listening",
            extra={
                "control_port": self.control_port,
                "relay_min": self.config.relay_min,
                "relay_max": self.config.relay_max,
                "ttl_s": self.config.ttl_seconds,
                "max_sessions": self.config.max_sessions,
                "advertise_ip": self.config.advertise_ip or "0.0.0.0",
            },
        )

    # -- lifecycle ---------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._running

    @property
    def counters(self) -> dict[str, int]:
        return dict(self._counters)

    @property
    def sessions(self) -> int:
        return len(self._rooms)

    def start(self) -> None:
        """Run the selector loop in a daemon thread (for tests and embedding)."""
        with self._lifecycle:
            if self._closed:
                raise RuntimeError("server is closed")
            if self._thread is not None and self._thread.is_alive():
                return
            self._running = True
            self._thread = threading.Thread(target=self._loop, name="giants-relay", daemon=True)
            self._thread.start()

    def run(self) -> None:
        """Run the selector loop in the calling thread (production entry point)."""
        with self._lifecycle:
            if self._closed:
                raise RuntimeError("server is closed")
            self._running = True
        self._loop()

    def stop(self, timeout: float = 2.0) -> None:
        """Ask the loop to exit; safe from a signal handler and other threads."""
        self._running = False
        try:
            self._wake_w.send(b"x")
        except OSError:
            pass
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
        elif thread is None and not self._in_loop:
            self._close_all()

    def wait(self, timeout: float | None = None) -> None:
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    # -- selector loop -----------------------------------------------------

    def _loop(self) -> None:
        self._in_loop = True
        try:
            while self._running:
                now = time.monotonic()
                self._expire(now)
                timeout = self._select_timeout(now)
                try:
                    events = self._selector.select(timeout)
                except OSError:
                    if not self._running:
                        break
                    raise
                for key, _mask in events:
                    tag, room = key.data
                    try:
                        if tag == "control":
                            self._read_control()
                        elif tag == "relay" and room is not None:
                            self._read_relay(room)
                        else:
                            self._drain_wake()
                    except Exception:  # never let one datagram kill the relay
                        self._counters["rejected"] += 1
                        log.exception("event handler failed", extra={"tag": tag})
        finally:
            self._in_loop = False
            self._running = False
            self._close_all()

    def _select_timeout(self, now: float) -> float:
        if not self._rooms:
            return 0.5
        deadline = min(room.last_seen + self.config.ttl for room in self._rooms.values())
        return max(0.0, min(0.5, deadline - now))

    def _drain_wake(self) -> None:
        try:
            while self._wake_r.recv(256):
                pass
        except (BlockingIOError, OSError):
            pass

    def _close_all(self) -> None:
        if self._closed:
            return
        self._closed = True
        for room in list(self._rooms.values()):
            self._close_room_socket(room)
        self._rooms.clear()
        try:
            self._selector.close()
        except OSError:
            pass
        for sock in (self._control, self._wake_r, self._wake_w):
            try:
                sock.close()
            except OSError:
                pass
        log.info("relay stopped")

    def _close_room_socket(self, room: Room) -> None:
        try:
            self._selector.unregister(room.sock)
        except (KeyError, ValueError, OSError):
            pass
        try:
            room.sock.close()
        except OSError:
            pass

    def _expire(self, now: float) -> None:
        if not self._rooms:
            return
        ttl = self.config.ttl
        for code, room in list(self._rooms.items()):
            if now - room.last_seen >= ttl:
                self._rooms.pop(code, None)
                self._close_room_socket(room)
                self._counters["expired"] += 1
                log.info("room expired", extra={"room": code.decode("ascii", "replace"),
                                                "relay_port": room.relay_port})

    # -- buckets -----------------------------------------------------------

    def _bucket(self, table: dict[str, TokenBucket], ip: str, rate: float, burst: int,
                limit: int) -> TokenBucket | None:
        bucket = table.get(ip)
        if bucket is not None:
            return bucket
        if len(table) >= limit:
            cutoff = time.monotonic() - BUCKET_IDLE_SECONDS
            for key, old in list(table.items()):
                if old.updated < cutoff:
                    table.pop(key, None)
            if len(table) >= limit:
                return None
        bucket = TokenBucket(rate, burst)
        table[ip] = bucket
        return bucket

    # -- control socket ----------------------------------------------------

    def _read_control(self) -> None:
        for _ in range(64):
            try:
                data, src = self._control.recvfrom(MAX_DATAGRAM + 1)
            except (BlockingIOError, OSError):
                return
            try:
                self._handle_control(data, src)
            except Exception:
                self._counters["rejected"] += 1
                log.exception("control handler failed")

    def _handle_control(self, data: bytes, src: tuple[str, int]) -> None:
        if len(data) > MAX_DATAGRAM:
            self._counters["oversized"] += 1
            return
        # Foreign traffic is dropped silently: never answer for a magic we do
        # not own, and never spend a rate-limit token on it.
        if len(data) < HEADER_SIZE or data[:4] != MAGIC:
            return
        bucket = self._bucket(self._control_buckets, src[0],
                              self.config.rate_per_second, self.config.rate_burst,
                              MAX_CONTROL_BUCKETS)
        if bucket is None or not bucket.allow():
            self._counters["rate_limited"] += 1
            return
        nonce = struct.unpack_from(">Q", data, 7)[0]
        try:
            message = parse_message(data)
        except ProtocolError as exc:
            self._counters["rejected"] += 1
            self._counters["malformed"] += 1
            log.debug("rejected control datagram", extra={
                "status": STATUS_NAMES.get(exc.status, str(exc.status)),
                "reason": exc.reason, "bytes": len(data)})
            # A bounded 19-byte reply only to a datagram that carried our
            # magic and a complete header; foreign/short traffic was silent.
            self._send_control(encode_error(nonce, exc.status), src)
            return

        if message.kind == KIND_CREATE:
            reply = self._handle_create(message, src)
        elif message.kind == KIND_JOIN:
            reply = self._handle_join(message, src)
        elif message.kind == KIND_KEEPALIVE:
            reply = self._handle_keepalive(message)
        else:  # KIND_HEALTH
            reply = self._handle_health(message)
        if reply is not None:
            self._send_control(reply, src)

    def _send_control(self, data: bytes, dst: tuple[str, int]) -> None:
        try:
            self._control.sendto(data, dst)
        except OSError:
            self._counters["dropped"] += 1

    # -- lobby handlers ----------------------------------------------------

    def _allocate_code(self) -> bytes | None:
        for _ in range(8):
            code = new_room_code()
            if isinstance(code, str):  # tolerate an injected/patched generator
                code = code.encode("ascii", "replace")
            if ROOM_CODE_RE.match(code) and code not in self._rooms:
                return code
        return None

    def _open_relay_socket(self) -> tuple[int, socket.socket] | None:
        in_use = {room.relay_port for room in self._rooms.values()}
        for port in range(self.config.relay_min, self.config.relay_max + 1):
            if port in in_use:
                continue
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.bind((self.config.bind_host, port))
            except OSError:
                sock.close()
                continue
            sock.setblocking(False)
            return port, sock
        return None

    def _handle_create(self, message: Message, src: tuple[str, int]) -> bytes:
        protocol_version, build_id, gameplay_port = message.body
        zero = dict(nonce=message.nonce, relay_port=0, room_code=bytes(8), ttl_s=0)
        if protocol_version != VERSION:
            self._counters["rejected"] += 1
            return encode_create_ack(status=STATUS_BAD_VERSION, **zero)
        # Retries use the same nonce from the same socket: answer with the existing room instead
        # of allocating a second one when the first ACK was lost.
        for existing in self._rooms.values():
            if existing.nonce == message.nonce and existing.create_src == src:
                return encode_create_ack(nonce=message.nonce, status=STATUS_OK,
                                         relay_port=existing.relay_port,
                                         room_code=existing.code,
                                         ttl_s=self.config.ttl_seconds)
        if len(self._rooms) >= self.config.max_sessions:
            self._counters["rejected"] += 1
            return encode_create_ack(status=STATUS_SERVER_FULL, **zero)
        code = self._allocate_code()
        if code is None:
            self._counters["rejected"] += 1
            return encode_create_ack(status=STATUS_DUPLICATE_ROOM, **zero)
        opened = self._open_relay_socket()
        if opened is None:
            self._counters["rejected"] += 1
            return encode_create_ack(status=STATUS_NO_RELAY_PORT, **zero)
        relay_port, sock = opened
        now = time.monotonic()
        room = Room(code=code, relay_port=relay_port, sock=sock, nonce=message.nonce,
                    create_src=src, build_id=build_id, gameplay_port=gameplay_port, created=now,
                    last_seen=now)
        self._rooms[code] = room
        try:
            self._selector.register(sock, selectors.EVENT_READ, ("relay", room))
        except Exception:
            self._rooms.pop(code, None)
            sock.close()
            raise
        self._counters["creates"] += 1
        log.info("room created", extra={
            "room": code.decode("ascii"), "relay_port": relay_port,
            "build_id": build_id, "gameplay_port": gameplay_port, "src": src[0]})
        return encode_create_ack(nonce=message.nonce, status=STATUS_OK,
                                 relay_port=relay_port, room_code=code,
                                 ttl_s=self.config.ttl_seconds)

    def _handle_join(self, message: Message, src: tuple[str, int]) -> bytes:
        code_raw, build_id = message.body
        room = self._rooms.get(code_raw.lower())
        zero = dict(nonce=message.nonce, relay_ipv4=bytes(4), relay_port=0,
                    gameplay_port=0)
        if room is None:
            status = STATUS_UNKNOWN_ROOM
        elif build_id != room.build_id:
            status = STATUS_BUILD_MISMATCH
            log.info("join rejected", extra={
                "room": room.code.decode("ascii"), "build_id": build_id,
                "room_build_id": room.build_id, "src": src[0]})
        elif room.active:
            status = STATUS_ROOM_FULL
        else:
            status = STATUS_OK
            self._counters["joins"] += 1
            log.info("client joined", extra={
                "room": room.code.decode("ascii"), "relay_port": room.relay_port,
                "build_id": build_id, "src": src[0]})
        if status != STATUS_OK:
            return encode_join_ack(status=status, **zero)
        return encode_join_ack(nonce=message.nonce, status=STATUS_OK,
                               relay_ipv4=self._advertise_ipv4,
                               relay_port=room.relay_port,
                               gameplay_port=room.gameplay_port)

    def _handle_keepalive(self, message: Message) -> bytes:
        (code_raw,) = message.body
        room = self._rooms.get(code_raw.lower())
        if room is None:
            status = STATUS_UNKNOWN_ROOM
        else:
            room.last_seen = time.monotonic()
            self._counters["keepalives"] += 1
            status = STATUS_OK
        return encode_keepalive_ack(message.nonce, status)

    def _handle_health(self, message: Message) -> bytes:
        payload = self._health_payload()
        return encode_health_ack(message.nonce, payload)

    def _health_payload(self) -> bytes:
        rooms = len(self._rooms)
        waiting = sum(1 for room in self._rooms.values() if not room.active)
        active = rooms - waiting
        counters = {name: self._counters[name] for name in (
            "creates", "joins", "keepalives", "hellos", "forwarded", "expired",
            "rate_limited", "rejected", "malformed", "oversized", "dropped")}
        payload = {
            "sessions": rooms,
            "relays": rooms,
            "uptime": round(time.monotonic() - self._started, 1),
            "counters": counters,
            "waiting": waiting,
            "active": active,
            "relay_ports_free": max(0, self.config.relay_port_count - rooms),
            "registers": counters["creates"],
            "joins": counters["joins"],
            "expired": counters["expired"],
            "rate_limited": counters["rate_limited"],
            "rejected": counters["rejected"],
        }
        raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        if len(raw) > 400:
            # Keep the documented cap even if counters grow unusual names.
            raw = json.dumps(
                {"sessions": rooms, "relays": rooms,
                 "uptime": payload["uptime"], "counters": counters},
                separators=(",", ":"), sort_keys=True).encode("utf-8")[:400]
        return raw

    # -- relay sockets -----------------------------------------------------

    def _read_relay(self, room: Room) -> None:
        if self._rooms.get(room.code) is not room:
            return
        for _ in range(64):
            try:
                data, src = room.sock.recvfrom(MAX_DATAGRAM + 1)
            except (BlockingIOError, OSError):
                return
            try:
                self._handle_relay(room, data, src)
            except Exception:
                self._counters["rejected"] += 1
                log.exception("relay handler failed",
                              extra={"room": room.code.decode("ascii", "replace")})

    def _handle_relay(self, room: Room, data: bytes, src: tuple[str, int]) -> None:
        if len(data) > MAX_DATAGRAM:
            self._counters["oversized"] += 1
            return
        bucket = self._bucket(
            room.buckets, src[0],
            self.config.rate_per_second * RELAY_RATE_MULTIPLIER,
            max(1, int(self.config.rate_burst * RELAY_RATE_MULTIPLIER)),
            MAX_RELAY_BUCKETS)
        if bucket is None or not bucket.allow():
            self._counters["rate_limited"] += 1
            return
        if data[:4] == HELLO_MAGIC:
            self._handle_hello(room, data, src)
            return
        self._forward(room, data, src)

    def _handle_hello(self, room: Room, data: bytes, src: tuple[str, int]) -> None:
        if len(data) != HELLO_SIZE:
            self._counters["rejected"] += 1
            return
        try:
            magic, version, code_raw, nonce = HELLO.unpack(data)
        except struct.error:
            self._counters["rejected"] += 1
            return
        if (magic != HELLO_MAGIC or version != VERSION
                or code_raw.lower() != room.code or nonce != room.nonce):
            self._counters["rejected"] += 1
            return
        if room.client is not None and src == room.client:
            # One tuple may not occupy both slots.
            self._counters["rejected"] += 1
            return
        rebound = room.host is not None and room.host != src
        room.host = src
        room.last_seen = time.monotonic()
        self._counters["hellos"] += 1
        # GRAK is a diagnostic/acknowledgement; it is generated here and is
        # never forwarded to the other peer.
        self._send_relay(room, encode_grak(room.code, room.nonce), src)
        if room.pending_client is not None and room.client is None:
            pending = room.pending_client
            room.pending_client = None
            if pending.endpoint != src:
                # A host-side datagram can arrive before its own GREL after
                # network reordering; it must not occupy the client slot too.
                room.client = pending.endpoint
                self._send_relay(room, pending.data, src)
                self._counters["forwarded"] += 1
                room.last_seen = time.monotonic()
                log.info("client-before-hello datagram flushed", extra={
                    "room": room.code.decode("ascii"), "relay_port": room.relay_port})
        if rebound:
            log.info("host endpoint rebound", extra={
                "room": room.code.decode("ascii"), "relay_port": room.relay_port})

    def _forward(self, room: Room, data: bytes, src: tuple[str, int]) -> None:
        now = time.monotonic()
        if room.host is None:
            # The host has not said hello yet.  Hold one candidate client and
            # its latest datagram; bind and flush it when GREL arrives.
            if room.pending_client is None:
                room.pending_client = _PendingClient(src, bytes(data))
            elif room.pending_client.endpoint == src:
                room.pending_client.data = bytes(data)
            else:
                self._counters["dropped"] += 1
            return
        if src == room.host:
            target = room.client
        elif room.client is None:
            # First non-GREL datagram from a source other than the host claims
            # the client slot, and this same datagram is forwarded.
            room.client = src
            target = room.host
            log.info("client endpoint bound", extra={
                "room": room.code.decode("ascii"), "relay_port": room.relay_port})
        elif src == room.client:
            target = room.host
        else:
            # Hard two-endpoint cap: a third source is dropped with no reply,
            # so the relay can never be used for reflection or discovery.
            self._counters["dropped"] += 1
            return
        if target is None or target == src:
            return
        self._send_relay(room, data, target)
        room.last_seen = now
        self._counters["forwarded"] += 1

    def _send_relay(self, room: Room, data: bytes, dst: tuple[str, int]) -> None:
        try:
            room.sock.sendto(data, dst)
        except OSError:
            self._counters["dropped"] += 1


# ---------------------------------------------------------------------------
# Healthcheck and entry point
# ---------------------------------------------------------------------------


def healthcheck(control_port: int, timeout: float = 3.0) -> bool:
    """Send HEALTH to 127.0.0.1:control_port and validate HEALTH_ACK."""
    nonce = secrets.randbits(64)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(encode_health(nonce), ("127.0.0.1", control_port))
        data, _src = sock.recvfrom(MAX_DATAGRAM)
    except OSError:
        return False
    finally:
        sock.close()
    try:
        header = parse_header(data)
        if header.kind != KIND_HEALTH_ACK or header.nonce != nonce:
            return False
        status = struct.unpack_from(">I", data, HEADER_SIZE)[0]
        return status == STATUS_OK
    except (ProtocolError, struct.error):
        return False


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Giants netplay relay/lobby server")
    parser.add_argument("--healthcheck", action="store_true",
                        help="probe the local control socket and exit 0/1")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        config = RelayConfig.from_env()
    except ConfigError as exc:
        setup_logging(os.environ.get("GIANTS_LOG_LEVEL", "INFO"))
        log.error("invalid configuration", extra={"error": str(exc)})
        return 2

    setup_logging(config.log_level)

    if args.healthcheck:
        return 0 if healthcheck(config.control_port) else 1

    if not config.public_ip or config.public_ip == "0.0.0.0":
        log.error("GIANTS_PUBLIC_IP is required",
                  extra={"hint": "set it to the IPv4 players send UDP to"})
        return 2

    try:
        server = RelayServer(config)
    except OSError as exc:
        log.error("cannot bind relay listeners", extra={"error": str(exc)})
        return 1

    def shutdown(signum: int, _frame: object) -> None:
        log.info("signal received, shutting down", extra={"signal": signum})
        server.stop()

    for signum in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(signum, shutdown)
        except (ValueError, OSError):
            pass

    try:
        server.run()
    except KeyboardInterrupt:
        server.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
