#!/usr/bin/env python3
"""Protocol unit tests + loopback integration tests for server/giants_relay.py.

Run from the repository root:

    python3 server/tests/test_relay.py

Stdlib only; no game build, no Docker, no host networking.  Loopback servers
pick an unused private relay range so the suite can run beside a real relay.
"""

from __future__ import annotations

import json
import secrets
import socket
import struct
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import giants_relay as gr  # noqa: E402

PUBLIC_IP = "203.0.113.9"
BUILD_ID = 0x1234ABCD
GAMEPLAY_PORT = 24872


# ---------------------------------------------------------------------------
# Socket helpers
# ---------------------------------------------------------------------------


def udp_socket(timeout: float = 1.0, bind_ip: str = "127.0.0.1") -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((bind_ip, 0))
    sock.settimeout(timeout)
    return sock


def free_udp_range(size: int) -> tuple[int, int]:
    """Find `size` consecutive UDP ports free on loopback and on 0.0.0.0."""
    for base in range(44000, 60000 - size, 7):
        held: list[socket.socket] = []
        try:
            for port in range(base, base + size):
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                try:
                    sock.bind(("0.0.0.0", port))
                except OSError:
                    sock.close()
                    raise
                held.append(sock)
        except OSError:
            continue
        finally:
            for sock in held:
                sock.close()
        if len(held) == size:
            return base, base + size - 1
    raise RuntimeError("no free UDP port range for the test relay")


def wait_until(predicate, timeout: float = 2.0, interval: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------


class ProtocolTests(unittest.TestCase):
    def test_header_roundtrip(self):
        nonce = 0xDEADBEEFCAFEF00D
        packet = gr.encode_header(gr.KIND_HEALTH, nonce)
        self.assertEqual(len(packet), 15)
        header = gr.parse_header(packet)
        self.assertEqual((header.kind, header.nonce), (gr.KIND_HEALTH, nonce))

    def test_exact_sizes_and_unaligned_nonce(self):
        code = b"0a1b2c3d"
        nonce = 0x0102030405060708
        self.assertEqual(len(gr.encode_create(nonce, 1, BUILD_ID, GAMEPLAY_PORT)), 23)
        self.assertEqual(len(gr.encode_create_ack(nonce, 0, 41100, code, 30)), 31)
        self.assertEqual(len(gr.encode_join(nonce, code, BUILD_ID)), 27)
        self.assertEqual(len(gr.encode_join_ack(nonce, 0, b"\x7f\x00\x00\x01", 41100, 1)), 27)
        self.assertEqual(len(gr.encode_keepalive(nonce, code)), 23)
        self.assertEqual(len(gr.encode_keepalive_ack(nonce, 0)), 19)
        self.assertEqual(len(gr.encode_health(nonce)), 15)
        self.assertEqual(len(gr.encode_health_ack(nonce, b"{}")), 21)
        self.assertEqual(len(gr.encode_error(nonce, gr.STATUS_MALFORMED)), 19)
        self.assertEqual(len(gr.encode_grel(code, nonce)), 21)
        self.assertEqual(len(gr.encode_grak(code, nonce)), 21)
        # The nonce is 8 bytes at offset 7, deliberately unaligned.
        self.assertEqual(struct.unpack_from(">Q", gr.encode_health(nonce), 7)[0], nonce)

    def test_truncated_and_trailing_messages_never_parse(self):
        code = b"aabbccdd"
        samples = [gr.encode_create(7, 1, BUILD_ID, GAMEPLAY_PORT),
                   gr.encode_join(8, code, BUILD_ID),
                   gr.encode_keepalive(9, code)]
        for sample in samples:
            for cut in range(len(sample)):
                with self.assertRaises(gr.ProtocolError, msg=f"prefix {cut}/{len(sample)}"):
                    gr.parse_message(sample[:cut])
            with self.assertRaises(gr.ProtocolError):
                gr.parse_message(sample + b"\x00")

    def test_header_rejects_bad_versions_flags_and_magic(self):
        nonce = 123
        body = struct.pack(">HIH", 1, BUILD_ID, GAMEPLAY_PORT)
        with self.assertRaises(gr.ProtocolError) as bad_version:
            gr.parse_message(struct.pack(">4sBBBQ", b"GRLY", 2, 1, 0, nonce) + body)
        self.assertEqual(bad_version.exception.status, gr.STATUS_BAD_VERSION)
        with self.assertRaises(gr.ProtocolError) as bad_flags:
            gr.parse_message(struct.pack(">4sBBBQ", b"GRLY", 1, 1, 1, nonce) + body)
        self.assertEqual(bad_flags.exception.status, gr.STATUS_MALFORMED)
        with self.assertRaises(gr.ProtocolError):
            gr.parse_message(struct.pack(">4sBBBQ", b"NOPE", 1, 1, 0, nonce) + body)
        with self.assertRaises(gr.ProtocolError):
            gr.parse_message(gr.encode_header(42, nonce))
        with self.assertRaises(gr.ProtocolError):
            gr.parse_message(gr.encode_health(nonce) + b"x")

    def test_token_bucket_burst_refill(self):
        now = [0.0]
        bucket = gr.TokenBucket(2.0, 3, clock=lambda: now[0])
        self.assertEqual([bucket.allow() for _ in range(4)], [True, True, True, False])
        now[0] = 0.25  # half a token, still empty
        self.assertFalse(bucket.allow())
        now[0] = 0.5
        self.assertTrue(bucket.allow())
        self.assertFalse(bucket.allow())
        now[0] = 10.0  # capped at burst
        self.assertEqual([bucket.allow() for _ in range(5)], [True, True, True, False, False])

    def test_room_code_shape(self):
        codes = {gr.new_room_code() for _ in range(64)}
        self.assertEqual(len(codes), 64)
        for code in codes:
            self.assertRegex(code, rb"[0-9a-f]{8}\Z")

    def test_config_from_env(self):
        config = gr.RelayConfig.from_env({
            "GIANTS_PUBLIC_IP": "198.51.100.7",
            "GIANTS_ADVERTISE_IP": "",
            "GIANTS_CONTROL_PORT": "32200",
            "GIANTS_RELAY_MIN": "41100",
            "GIANTS_RELAY_MAX": "41199",
            "GIANTS_TTL": "30",
            "GIANTS_MAX_SESSIONS": "100",
            "GIANTS_RATE_PER_SECOND": "50",
            "GIANTS_RATE_BURST": "100",
            "GIANTS_LOG_LEVEL": "debug",
        })
        self.assertEqual(config.advertise_ip, "198.51.100.7")
        self.assertEqual(config.ttl_seconds, 30)
        self.assertEqual(config.relay_port_count, 100)
        self.assertEqual(config.log_level, "DEBUG")

    def test_config_rejects_bad_values(self):
        with self.assertRaises(gr.ConfigError):
            gr.RelayConfig(relay_min=41110, relay_max=41100)
        with self.assertRaises(gr.ConfigError):
            gr.RelayConfig(control_port=41150, relay_min=41100, relay_max=41199)
        with self.assertRaises(gr.ConfigError):
            gr.RelayConfig(public_ip="not-an-ip")


# ---------------------------------------------------------------------------
# Integration tests (real UDP sockets, selector loop in a thread)
# ---------------------------------------------------------------------------


class ServerTestBase(unittest.TestCase):
    server: gr.RelayServer

    @classmethod
    def make_server(cls, *, ttl: float = 30.0, max_sessions: int = 16,
                    rate: float = 1000.0, burst: int = 2000,
                    range_size: int = 16) -> gr.RelayServer:
        relay_min, relay_max = free_udp_range(range_size)
        config = gr.RelayConfig(
            control_port=0,
            relay_min=relay_min,
            relay_max=relay_max,
            ttl=ttl,
            max_sessions=max_sessions,
            rate_per_second=rate,
            rate_burst=burst,
            public_ip=PUBLIC_IP,
            advertise_ip=PUBLIC_IP,
            log_level="CRITICAL",
        )
        server = gr.RelayServer(config)
        server.start()
        return server

    @classmethod
    def setUpClass(cls):
        cls.server = cls.make_server()
        cls.addr = ("127.0.0.1", cls.server.control_port)

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()
        cls.server.wait(2)

    def rpc(self, sock: socket.socket, packet: bytes) -> bytes:
        sock.sendto(packet, self.addr)
        data, _src = sock.recvfrom(2048)
        return data

    def create_room(self, sock: socket.socket, build_id: int = BUILD_ID,
                    gameplay_port: int = GAMEPLAY_PORT,
                    protocol_version: int = 1) -> tuple[bytes, int, int]:
        nonce = secrets.randbits(64)
        reply = self.rpc(sock, gr.encode_create(nonce, protocol_version, build_id,
                                                gameplay_port))
        header = gr.parse_header(reply)
        self.assertEqual(header.kind, gr.KIND_CREATE_ACK)
        self.assertEqual(header.nonce, nonce)
        status, relay_port, code, ttl = struct.unpack_from(">IH8sH", reply, 15)
        self.assertEqual(status, gr.STATUS_OK, f"create status {status}")
        self.assertRegex(code, rb"[0-9a-f]{8}\Z")
        self.assertTrue(self.server.config.relay_min <= relay_port
                        <= self.server.config.relay_max)
        self.assertGreaterEqual(ttl, 1)
        return code, relay_port, nonce


class ServerIntegrationTests(ServerTestBase):
    def test_create_retry_is_idempotent(self):
        host = udp_socket()
        self.addCleanup(host.close)
        nonce = secrets.randbits(64)
        packet = gr.encode_create(nonce, 1, BUILD_ID, GAMEPLAY_PORT)

        def acknowledgement(reply):
            status, relay_port, room, _ttl = struct.unpack_from(">IH8sH", reply, 15)
            return status, relay_port, room

        first = acknowledgement(self.rpc(host, packet))
        second = acknowledgement(self.rpc(host, packet))
        self.assertEqual(first, second)
        self.assertEqual(first[0], gr.STATUS_OK)
        third = acknowledgement(
            self.rpc(host, gr.encode_create(nonce ^ 1, 1, BUILD_ID, GAMEPLAY_PORT)))
        self.assertEqual(third[0], gr.STATUS_OK)
        self.assertNotEqual(first[2], third[2])

    def test_create_join_and_unknown_room(self):
        host = udp_socket()
        self.addCleanup(host.close)
        code, relay_port, _nonce = self.create_room(host)

        joiner = udp_socket()
        self.addCleanup(joiner.close)
        join_nonce = 0x1122334455667788
        reply = self.rpc(joiner, gr.encode_join(join_nonce, code, BUILD_ID))
        header = gr.parse_header(reply)
        self.assertEqual((header.kind, header.nonce),
                         (gr.KIND_JOIN_ACK, join_nonce))
        status, relay_ip, got_port, gameplay_port = struct.unpack_from(
            ">I4sHH", reply, 15)
        self.assertEqual(status, gr.STATUS_OK)
        self.assertEqual(relay_ip, socket.inet_aton(PUBLIC_IP))
        self.assertEqual(got_port, relay_port)
        self.assertEqual(gameplay_port, GAMEPLAY_PORT)

        # Room codes are case-insensitive on input.
        other = udp_socket()
        self.addCleanup(other.close)
        upper = self.rpc(other, gr.encode_join(9, code.upper(), BUILD_ID))
        self.assertEqual(struct.unpack_from(">I", upper, 15)[0], gr.STATUS_OK)

        missing = self.rpc(other, gr.encode_join(10, b"deadbeef", BUILD_ID))
        self.assertEqual(struct.unpack_from(">I", missing, 15)[0],
                         gr.STATUS_UNKNOWN_ROOM)

        keep = self.rpc(host, gr.encode_keepalive(11, code))
        self.assertEqual(gr.parse_header(keep).kind, gr.KIND_KEEPALIVE_ACK)
        self.assertEqual(struct.unpack_from(">I", keep, 15)[0], gr.STATUS_OK)
        unknown_keep = self.rpc(host, gr.encode_keepalive(12, b"deadbeef"))
        self.assertEqual(struct.unpack_from(">I", unknown_keep, 15)[0],
                         gr.STATUS_UNKNOWN_ROOM)

    def test_build_mismatch(self):
        host = udp_socket()
        self.addCleanup(host.close)
        code, _port, _nonce = self.create_room(host, build_id=0xAAAAAAAA)
        joiner = udp_socket()
        self.addCleanup(joiner.close)
        reply = self.rpc(joiner, gr.encode_join(77, code, 0xBBBBBBBB))
        self.assertEqual(struct.unpack_from(">I", reply, 15)[0],
                         gr.STATUS_BUILD_MISMATCH)
        # The correct build still joins the same room.
        reply = self.rpc(joiner, gr.encode_join(78, code, 0xAAAAAAAA))
        self.assertEqual(struct.unpack_from(">I", reply, 15)[0], gr.STATUS_OK)

    def test_hello_binding_and_two_peer_forwarding(self):
        host_ctrl = udp_socket()
        self.addCleanup(host_ctrl.close)
        code, relay_port, nonce = self.create_room(host_ctrl)
        relay = ("127.0.0.1", relay_port)

        host = udp_socket()
        client = udp_socket()
        outsider = udp_socket(0.3)
        for sock in (host, client, outsider):
            self.addCleanup(sock.close)

        # The first GREL binds the host endpoint and is acknowledged.
        host.sendto(gr.encode_grel(code, nonce), relay)
        ack, _src = host.recvfrom(64)
        self.assertEqual(len(ack), 21)
        magic, version, ack_code, ack_nonce = gr.HELLO.unpack(ack)
        self.assertEqual((magic, version, ack_code, ack_nonce),
                         (b"GRAK", 1, code, nonce))

        # Client's first datagram claims the client slot and reaches the host.
        client.sendto(b"CONNECT", relay)
        data, src = host.recvfrom(64)
        self.assertEqual(data, b"CONNECT")
        self.assertEqual(src, relay)
        host.sendto(b"ACCEPT", relay)
        data, src = client.recvfrom(64)
        self.assertEqual(data, b"ACCEPT")
        self.assertEqual(src, relay)

        # A repeated hello is consumed and acknowledged, not forwarded.
        host.sendto(gr.encode_grel(code, nonce), relay)
        ack2, _ = host.recvfrom(64)
        self.assertEqual(ack2[:4], b"GRAK")
        with self.assertRaises(socket.timeout):
            client.recvfrom(64)

        # A wrong nonce is consumed and gets no GRAK.
        outsider.sendto(gr.encode_grel(code, nonce ^ 1), relay)
        with self.assertRaises(socket.timeout):
            outsider.recvfrom(64)

        # A third source cannot join, receives nothing, and does not evict
        # either peer.
        outsider.sendto(b"INTRUDER", relay)
        with self.assertRaises(socket.timeout):
            outsider.recvfrom(64)
        with self.assertRaises(socket.timeout):
            host.recvfrom(64)
        with self.assertRaises(socket.timeout):
            client.recvfrom(64)
        client.sendto(b"AFTER-INTRUDER", relay)
        data, _ = host.recvfrom(64)
        self.assertEqual(data, b"AFTER-INTRUDER")

    def test_client_before_hello_is_held_and_flushed(self):
        host_ctrl = udp_socket()
        self.addCleanup(host_ctrl.close)
        code, relay_port, nonce = self.create_room(host_ctrl)
        relay = ("127.0.0.1", relay_port)

        client = udp_socket()
        host = udp_socket()
        self.addCleanup(client.close)
        self.addCleanup(host.close)

        # Client's ENet CONNECT arrives between CREATE_ACK and the host's GREL.
        client.sendto(b"EARLY-CONNECT", relay)
        time.sleep(0.1)
        host.sendto(gr.encode_grel(code, nonce), relay)
        ack, _ = host.recvfrom(64)
        self.assertEqual(ack[:4], b"GRAK")
        held, _ = host.recvfrom(64)
        self.assertEqual(held, b"EARLY-CONNECT")

        host.sendto(b"EARLY-ACCEPT", relay)
        data, _ = client.recvfrom(64)
        self.assertEqual(data, b"EARLY-ACCEPT")

    def test_healthjson_shape(self):
        host = udp_socket()
        self.addCleanup(host.close)
        self.create_room(host)
        nonce = 0xAABBCCDD
        reply = self.rpc(host, gr.encode_health(nonce))
        header = gr.parse_header(reply)
        self.assertEqual((header.kind, header.nonce), (gr.KIND_HEALTH_ACK, nonce))
        status = struct.unpack_from(">I", reply, 15)[0]
        self.assertEqual(status, gr.STATUS_OK)
        payload = reply[19:]
        self.assertLessEqual(len(payload), 400)
        data = json.loads(payload.decode("utf-8"))
        for key in ("sessions", "relays", "uptime", "counters", "waiting",
                    "active", "relay_ports_free"):
            self.assertIn(key, data)
        self.assertGreaterEqual(data["sessions"], 1)
        self.assertGreaterEqual(data["relays"], 1)
        self.assertIsInstance(data["counters"], dict)
        self.assertNotIn(b"127.0.0.1", payload)

    def test_malformed_trailing_flags_versions_and_oversize(self):
        sock = udp_socket(0.4)
        self.addCleanup(sock.close)

        def expect_error(packet: bytes, status: int):
            sock.sendto(packet, self.addr)
            reply, _ = sock.recvfrom(2048)
            header = gr.parse_header(reply)
            self.assertEqual(header.kind, gr.KIND_ERROR)
            self.assertEqual(struct.unpack_from(">I", reply, 15)[0], status)

        # Foreign magic and a short GRly prefix are dropped silently.
        sock.sendto(b"XXXX" + bytes(11), self.addr)
        with self.assertRaises(socket.timeout):
            sock.recvfrom(2048)
        sock.sendto(gr.encode_create(1, 1, BUILD_ID, GAMEPLAY_PORT)[:10], self.addr)
        with self.assertRaises(socket.timeout):
            sock.recvfrom(2048)

        create = gr.encode_create(2, 1, BUILD_ID, GAMEPLAY_PORT)
        expect_error(create[:-1], gr.STATUS_MALFORMED)          # truncated body
        expect_error(create + b"\x00", gr.STATUS_MALFORMED)     # trailing byte
        flagged = struct.pack(">4sBBBQ", b"GRLY", 1, gr.KIND_CREATE, 1, 3) + create[15:]
        expect_error(flagged, gr.STATUS_MALFORMED)
        old_version = struct.pack(">4sBBBQ", b"GRLY", 2, gr.KIND_CREATE, 0, 4) + create[15:]
        expect_error(old_version, gr.STATUS_BAD_VERSION)
        expect_error(gr.encode_header(42, 5), gr.STATUS_MALFORMED)

        # CREATE with a bad protocol_version is a CREATE_ACK status, not a
        # header-level error.
        reply = self.rpc(sock, gr.encode_create(6, 2, BUILD_ID, GAMEPLAY_PORT))
        self.assertEqual(gr.parse_header(reply).kind, gr.KIND_CREATE_ACK)
        status, port, code, ttl = struct.unpack_from(">IH8sH", reply, 15)
        self.assertEqual((status, port, code, ttl),
                         (gr.STATUS_BAD_VERSION, 0, bytes(8), 0))

        # 1501 bytes is dropped before parsing and gets no reply.
        oversized = create + b"x" * (1501 - len(create))
        sock.sendto(oversized, self.addr)
        with self.assertRaises(socket.timeout):
            sock.recvfrom(2048)

        # The server is still healthy.
        reply = self.rpc(sock, gr.encode_health(7))
        self.assertEqual(gr.parse_header(reply).kind, gr.KIND_HEALTH_ACK)
        self.assertGreaterEqual(self.server.counters["oversized"], 1)

    def test_duplicate_room_code_is_rejected(self):
        host = udp_socket()
        self.addCleanup(host.close)
        code, _port, _nonce = self.create_room(host)
        original = gr.new_room_code
        gr.new_room_code = lambda: code  # noqa: E731 - exercise the collision path
        try:
            reply = self.rpc(host, gr.encode_create(99, 1, BUILD_ID, GAMEPLAY_PORT))
        finally:
            gr.new_room_code = original
        self.assertEqual(gr.parse_header(reply).kind, gr.KIND_CREATE_ACK)
        self.assertEqual(struct.unpack_from(">I", reply, 15)[0],
                         gr.STATUS_DUPLICATE_ROOM)

    def test_server_full(self):
        server = self.make_server(max_sessions=1, range_size=4)
        self.addCleanup(server.stop)
        try:
            sock = udp_socket()
            self.addCleanup(sock.close)
            addr = ("127.0.0.1", server.control_port)
            first = sock.sendto(gr.encode_create(1, 1, BUILD_ID, GAMEPLAY_PORT), addr)
            self.assertTrue(first)
            reply, _ = sock.recvfrom(2048)
            self.assertEqual(struct.unpack_from(">I", reply, 15)[0], gr.STATUS_OK)
            sock.sendto(gr.encode_create(2, 1, BUILD_ID, GAMEPLAY_PORT), addr)
            reply, _ = sock.recvfrom(2048)
            self.assertEqual(struct.unpack_from(">I", reply, 15)[0],
                             gr.STATUS_SERVER_FULL)
        finally:
            server.stop()
            server.wait(2)

    def test_no_relay_port(self):
        server = self.make_server(max_sessions=4, range_size=1)
        try:
            sock = udp_socket()
            self.addCleanup(sock.close)
            addr = ("127.0.0.1", server.control_port)
            sock.sendto(gr.encode_create(1, 1, BUILD_ID, GAMEPLAY_PORT), addr)
            first, _ = sock.recvfrom(2048)
            self.assertEqual(struct.unpack_from(">I", first, 15)[0], gr.STATUS_OK)
            sock.sendto(gr.encode_create(2, 1, BUILD_ID, GAMEPLAY_PORT), addr)
            second, _ = sock.recvfrom(2048)
            self.assertEqual(struct.unpack_from(">I", second, 15)[0],
                             gr.STATUS_NO_RELAY_PORT)
        finally:
            server.stop()
            server.wait(2)


class ExpiryTests(unittest.TestCase):
    def test_keepalive_refreshes_then_room_expires_and_port_is_reused(self):
        server = ServerTestBase.make_server(ttl=1.5, max_sessions=4, range_size=4)
        sock = udp_socket()
        self.addCleanup(sock.close)
        try:
            addr = ("127.0.0.1", server.control_port)
            sock.sendto(gr.encode_create(1, 1, BUILD_ID, GAMEPLAY_PORT), addr)
            reply, _ = sock.recvfrom(2048)
            status, first_port, code, _ttl = struct.unpack_from(">IH8sH", reply, 15)
            self.assertEqual(status, gr.STATUS_OK)

            time.sleep(0.5)
            sock.sendto(gr.encode_keepalive(2, code), addr)
            reply, _ = sock.recvfrom(2048)
            self.assertEqual(struct.unpack_from(">I", reply, 15)[0], gr.STATUS_OK)

            time.sleep(0.5)  # 1.0 s after CREATE, 0.5 s after KEEPALIVE
            sock.sendto(gr.encode_health(3), addr)
            reply, _ = sock.recvfrom(2048)
            payload = json.loads(reply[19:].decode("utf-8"))
            self.assertEqual(payload["sessions"], 1)

            self.assertTrue(wait_until(
                lambda: json.loads(self._health(sock, addr)[19:].decode("utf-8"))["sessions"] == 0,
                timeout=4.0), "room did not expire")

            # Old code is gone; a fresh CREATE can reuse the freed low port.
            sock.sendto(gr.encode_join(4, code, BUILD_ID), addr)
            reply, _ = sock.recvfrom(2048)
            self.assertEqual(struct.unpack_from(">I", reply, 15)[0],
                             gr.STATUS_UNKNOWN_ROOM)
            sock.sendto(gr.encode_create(5, 1, BUILD_ID, GAMEPLAY_PORT), addr)
            reply, _ = sock.recvfrom(2048)
            status, second_port, _code, _ = struct.unpack_from(">IH8sH", reply, 15)
            self.assertEqual(status, gr.STATUS_OK)
            self.assertEqual(second_port, first_port)
        finally:
            server.stop()
            server.wait(2)

    @staticmethod
    def _health(sock: socket.socket, addr: tuple[str, int]) -> bytes:
        sock.sendto(gr.encode_health(secrets.randbits(64)), addr)
        reply, _ = sock.recvfrom(2048)
        return reply


class RateLimitTests(unittest.TestCase):
    def test_burst_then_drop_then_refill(self):
        server = ServerTestBase.make_server(ttl=30, max_sessions=2,
                                            rate=2.0, burst=2, range_size=2)
        sock = udp_socket(0.35)
        self.addCleanup(sock.close)
        try:
            addr = ("127.0.0.1", server.control_port)
            for nonce in range(20):
                sock.sendto(gr.encode_health(nonce), addr)
            got = 0
            while True:
                try:
                    sock.recvfrom(2048)
                    got += 1
                except socket.timeout:
                    break
            self.assertGreaterEqual(got, 1)
            self.assertLess(got, 20)
            time.sleep(0.2)
            self.assertGreaterEqual(server.counters["rate_limited"], 1)

            # A bucket with a 2/s refill recovers one token in ~0.5 s.
            time.sleep(0.7)
            sock.settimeout(1.0)
            sock.sendto(gr.encode_health(99), addr)
            reply, _ = sock.recvfrom(2048)
            self.assertEqual(gr.parse_header(reply).kind, gr.KIND_HEALTH_ACK)
        finally:
            server.stop()
            server.wait(2)


class HealthcheckTests(unittest.TestCase):
    def test_in_process_healthcheck(self):
        server = ServerTestBase.make_server(range_size=2)
        try:
            self.assertTrue(gr.healthcheck(server.control_port, timeout=1.0))
        finally:
            server.stop()
            server.wait(2)
        self.assertFalse(gr.healthcheck(server.control_port, timeout=0.2))


if __name__ == "__main__":
    unittest.main(verbosity=2)
