#!/usr/bin/env python3
"""End-to-end check: a real relay process plus two separate fake peers.

The parent process starts `giants_relay.py` as a child, creates one room over
UDP control, then starts two peer processes (this same file with `--peer`) on
separate sockets.  The client peer sends gameplay *before* the host's GREL, so
this also proves the client-before-hello hold/flush path across process
boundaries.

Run from the repository root:

    python3 server/tests/two_process.py
"""

from __future__ import annotations

import argparse
import os
import secrets
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parents[1]
SERVER = SERVER_DIR / "giants_relay.py"
sys.path.insert(0, str(SERVER_DIR))
import giants_relay as gr  # noqa: E402

BUILD_ID = 0x0BADF00D
GAMEPLAY_PORT = 24872


# ---------------------------------------------------------------------------
# Port helpers
# ---------------------------------------------------------------------------


def free_udp_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def free_udp_range(size: int) -> tuple[int, int]:
    for base in range(43000, 60000 - size, 11):
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
    raise RuntimeError("no free UDP range")


# ---------------------------------------------------------------------------
# Peer child: owns one UDP socket and blindly exchanges markers
# ---------------------------------------------------------------------------


def run_peer(role: str, relay_port: int, code: bytes, nonce: int) -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.settimeout(8.0)
    relay = ("127.0.0.1", relay_port)
    try:
        if role == "client":
            # Deliberately before the host's GREL.
            sock.sendto(b"CLIENT-HELLO", relay)
            print("CLIENT-SENT", flush=True)
            data, _src = sock.recvfrom(1500)
            if data != b"HOST-HELLO":
                print(f"PEER client FAIL: got {data!r}", flush=True)
                return 1
            sock.sendto(b"CLIENT-ACK", relay)
            print("PEER client PASS", flush=True)
            return 0

        sock.sendto(gr.encode_grel(code, nonce), relay)
        ack, _src = sock.recvfrom(1500)
        if len(ack) != gr.HELLO_SIZE or ack[:4] != gr.HELLO_ACK_MAGIC:
            print(f"PEER host FAIL: bad hello ack {ack!r}", flush=True)
            return 1
        _magic, version, ack_code, ack_nonce = gr.HELLO.unpack(ack)
        if (version, ack_code, ack_nonce) != (gr.VERSION, code, nonce):
            print("PEER host FAIL: hello ack mismatch", flush=True)
            return 1
        data, _src = sock.recvfrom(1500)
        if data != b"CLIENT-HELLO":
            print(f"PEER host FAIL: held client datagram was {data!r}", flush=True)
            return 1
        sock.sendto(b"HOST-HELLO", relay)
        data, _src = sock.recvfrom(1500)
        if data != b"CLIENT-ACK":
            print(f"PEER host FAIL: got {data!r}", flush=True)
            return 1
        print("PEER host PASS", flush=True)
        return 0
    except OSError as exc:
        print(f"PEER {role} FAIL: {exc}", flush=True)
        return 1
    finally:
        sock.close()


# ---------------------------------------------------------------------------
# Parent / orchestrator
# ---------------------------------------------------------------------------


def wait_for_health(control_port: int, timeout: float = 6.0) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(0.3)
    addr = ("127.0.0.1", control_port)
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            nonce = secrets.randbits(64)
            try:
                sock.sendto(gr.encode_health(nonce), addr)
                reply, _ = sock.recvfrom(2048)
                header = gr.parse_header(reply)
                if header.kind == gr.KIND_HEALTH_ACK and header.nonce == nonce:
                    return True
            except (OSError, gr.ProtocolError):
                pass
            time.sleep(0.05)
        return False
    finally:
        sock.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="two-process relay integration check")
    parser.add_argument("--peer", choices=("host", "client"))
    parser.add_argument("--relay-port", type=int, default=0)
    parser.add_argument("--code", default="")
    parser.add_argument("--nonce", type=lambda value: int(value, 0), default=0)
    args = parser.parse_args(argv)

    if args.peer:
        if not args.relay_port or len(args.code) != 8:
            print("peer needs --relay-port and 8-character --code", file=sys.stderr)
            return 2
        return run_peer(args.peer, args.relay_port, args.code.encode("ascii", "replace"),
                        args.nonce)

    control_port = free_udp_port()
    relay_min, relay_max = free_udp_range(8)
    env = dict(os.environ)
    env.update({
        "GIANTS_CONTROL_PORT": str(control_port),
        "GIANTS_RELAY_MIN": str(relay_min),
        "GIANTS_RELAY_MAX": str(relay_max),
        "GIANTS_TTL": "30",
        "GIANTS_MAX_SESSIONS": "8",
        "GIANTS_RATE_PER_SECOND": "1000",
        "GIANTS_RATE_BURST": "2000",
        "GIANTS_PUBLIC_IP": "127.0.0.1",
        "GIANTS_ADVERTISE_IP": "127.0.0.1",
        "GIANTS_LOG_LEVEL": "INFO",
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    server = subprocess.Popen(
        [sys.executable, "-u", str(SERVER)],
        cwd=str(SERVER_DIR), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    peers: list[subprocess.Popen] = []
    try:
        if not wait_for_health(control_port, timeout=6.0):
            raise RuntimeError("relay did not answer HEALTH")
        print(f"relay: control UDP 127.0.0.1:{control_port}, "
              f"rooms UDP {relay_min}-{relay_max}")

        control = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        control.bind(("127.0.0.1", 0))
        control.settimeout(2.0)
        addr = ("127.0.0.1", control_port)
        nonce = secrets.randbits(64)
        control.sendto(gr.encode_create(nonce, 1, BUILD_ID, GAMEPLAY_PORT), addr)
        reply, _ = control.recvfrom(2048)
        header = gr.parse_header(reply)
        if header.kind != gr.KIND_CREATE_ACK or header.nonce != nonce:
            raise RuntimeError("bad CREATE_ACK header")
        status, relay_port, code, _ttl = struct.unpack_from(">IH8sH", reply, 15)
        if status != gr.STATUS_OK:
            raise RuntimeError(f"CREATE failed with status {status}")
        print(f"room: {code.decode()} relay UDP 127.0.0.1:{relay_port}")

        join_nonce = secrets.randbits(64)
        control.sendto(gr.encode_join(join_nonce, code, BUILD_ID), addr)
        reply, _ = control.recvfrom(2048)
        status, relay_ip, joined_port, gameplay_port = struct.unpack_from(">I4sHH", reply, 15)
        if status != gr.STATUS_OK or joined_port != relay_port or gameplay_port != GAMEPLAY_PORT:
            raise RuntimeError(f"JOIN failed: status={status} port={joined_port}")
        if relay_ip != socket.inet_aton("127.0.0.1"):
            raise RuntimeError(f"JOIN advertised {socket.inet_ntoa(relay_ip)}")
        print("control: CREATE + JOIN OK")
        control.close()

        common = [sys.executable, str(Path(__file__).resolve()),
                  "--relay-port", str(relay_port), "--code", code.decode()]
        # Client first: its datagram must be held until the host says hello.
        client = subprocess.Popen(
            common + ["--peer", "client"], env=env, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True)
        peers.append(client)
        # Wait until the client has actually put its datagram on the wire, so
        # the host's GREL cannot win the race this test is meant to lose.
        sent = client.stdout.readline() if client.stdout is not None else ""
        print(sent, end="")
        if sent.strip() != "CLIENT-SENT":
            raise RuntimeError("client peer did not report its first datagram")
        host = subprocess.Popen(
            common + ["--peer", "host", "--nonce", hex(nonce)], env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        peers.append(host)

        failed = False
        for peer in peers:
            output, _ = peer.communicate(timeout=15)
            print(output, end="")
            failed = failed or peer.returncode != 0 or "PASS" not in output
        if failed:
            raise RuntimeError("a peer did not pass")

        print("two_process: PASS (real server process, two fake peers, "
              "client-before-hello flush)")
        return 0
    except Exception as exc:  # noqa: BLE001 - report diagnostics, then fail
        print(f"two_process: FAIL: {exc}", file=sys.stderr)
        return 1
    finally:
        for peer in peers:
            if peer.poll() is None:
                peer.kill()
                peer.wait(timeout=5)
        server.terminate()
        try:
            output, _ = server.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            output, _ = server.communicate(timeout=5)
        if server.returncode not in (0, -15) or (output and "FAIL" in output):
            print("relay output:", file=sys.stderr)
            print(output, file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
