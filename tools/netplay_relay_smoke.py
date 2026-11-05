#!/usr/bin/env python3
"""Run a headless host and client through the self-hosted relay and compare guest outputs.

Starts server/giants_relay.py on loopback, launches the host, reads the room code from the
server's JSON log, launches the client with it, then applies the same checks as netplay_smoke.py.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time


def pick_ports():
    """A free control port plus a small relay range on loopback."""
    for base in range(43000, 45000, 8):
        socks = []
        try:
            for port in range(base, base + 5):
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                socks.append(sock)
                sock.bind(("127.0.0.1", port))
            return base, base + 1, base + 3
        except OSError:
            continue
        finally:
            for sock in socks:
                sock.close()
    raise RuntimeError("no free relay port block")


def wait_for(predicate, timeout, description):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.2)
    raise RuntimeError(f"timed out waiting for {description}")


def read_log(path):
    try:
        return path.read_text(errors="replace")
    except FileNotFoundError:
        return ""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=30)
    parser.add_argument("--client-figure", default="")
    parser.add_argument("--client-figure-read", type=int, default=300)
    parser.add_argument("--hash-interval", type=int, default=0)
    parser.add_argument("--lobby", default="",
                        help="Use an external relay host:port instead of starting one")
    parser.add_argument("--server-log-cmd", default="",
                        help="Shell command that prints the external relay's log lines "
                             "(for example an ssh docker logs command)")
    args = parser.parse_args()
    if args.seconds <= 0:
        parser.error("seconds must be positive")
    if args.lobby and not args.server_log_cmd:
        parser.error("--lobby requires --server-log-cmd to discover the room code")

    root = Path(__file__).resolve().parent.parent
    output_root = root / "out/tmp"
    output_root.mkdir(parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix="netplay-relay-smoke-", dir=output_root))
    for name in ("host", "client"):
        (output / name / "frames").mkdir(parents=True)
        (output / name / "saves").mkdir()

    env = os.environ.copy()
    env.pop("WAYLAND_DISPLAY", None)
    env["SDL_AUDIO_DRIVER"] = "dummy"
    env["LD_LIBRARY_PATH"] = str(root / "thirdparty/rexglue-sdk/out/linux-amd64")
    server = None
    server_log = None
    if args.lobby:
        lobby = args.lobby
        def read_server_log():
            return subprocess.check_output(args.server_log_cmd, shell=True, text=True,
                                           stderr=subprocess.STDOUT)
    else:
        control, relay_min, relay_max = pick_ports()
        lobby = f"127.0.0.1:{control}"
        server_env = env.copy()
        server_env.update({
            "GIANTS_CONTROL_PORT": str(control),
            "GIANTS_RELAY_MIN": str(relay_min),
            "GIANTS_RELAY_MAX": str(relay_max),
            "GIANTS_PUBLIC_IP": "127.0.0.1",
            "GIANTS_ADVERTISE_IP": "127.0.0.1",
            "GIANTS_LOG_LEVEL": "INFO",
        })
        server_log_path = output / "server.log"
        server_log = server_log_path.open("wb")
        server = subprocess.Popen([sys.executable, str(root / "server/giants_relay.py")],
                                  cwd=root, env=server_env, stdout=server_log,
                                  stderr=subprocess.STDOUT, start_new_session=True)
        def read_server_log():
            return read_log(server_log_path)

    def room_from_server():
        for line in read_server_log().splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("event") == "room created" and record.get("room"):
                return record["room"]
        return None

    processes = []
    files = []
    try:
        if server is not None:
            wait_for(lambda: "relay listening" in read_server_log(), 10,
                     "the relay to listen")
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.bind(("127.0.0.1", 0))
            host_port = sock.getsockname()[1]
        for name in ("host", "client"):
            folder = output / name
            command = ["gamescope", "--backend", "headless", "-W", "1280", "-H", "720", "--",
                       str(root / "out/build/linux-amd64-release/giantsrecompiled"),
                       "--resolution=1280x720", "--resolution_scale=1",
                       "--frame_rate_limit=60", "--netplay_timeout_seconds=30",
                       f"--user_data_root={folder / 'saves'}",
                       f"--log_file={folder / 'game.log'}",
                       f"--input_record={folder / 'input.bin'}",
                       f"--guest_output_dump_prefix={folder / 'frames/f'}",
                       "--guest_output_dump_interval=120",
                       f"--netplay_lobby={lobby}"]
            if name == "host":
                command += ["--netplay_host=true", f"--netplay_port={host_port}",
                            "--portal_bot_figure=Spyro", "--bot_skip_movies=true",
                            f"--netplay_hash_interval={args.hash_interval}"]
            else:
                command += [f"--netplay_room={room}"]
                if args.client_figure:
                    command += [f"--netplay_test_client_figure={args.client_figure}",
                                f"--netplay_test_client_figure_read={args.client_figure_read}"]
            log = (folder / "stdout.log").open("wb")
            files.append(log)
            processes.append(subprocess.Popen(command, cwd=root, env=env, stdout=log,
                                              stderr=subprocess.STDOUT, start_new_session=True))
            if name == "host":
                room = wait_for(room_from_server, 40, "the host to create a room")
                print(f"room {room} on {lobby}", flush=True)
                # Give the host's gamescope instance time to settle before the second one starts.
                time.sleep(1.0)
        print(f"Peers running; output: {output}", flush=True)
        deadline = time.monotonic() + args.seconds
        while time.monotonic() < deadline:
            if any(process.poll() is not None for process in processes):
                raise RuntimeError("A peer exited early; inspect game.log/stdout.log")
            if server is not None and server.poll() is not None:
                raise RuntimeError("The relay exited early; inspect server.log")
            for name in ("host", "client"):
                log = read_log(output / name / "game.log")
                if "Netplay paused:" in log:
                    raise RuntimeError(f"{name} paused; inspect game.log")
            time.sleep(1)
    finally:
        for process in processes:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGINT)
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        if server is not None:
            if server.poll() is None:
                os.killpg(server.pid, signal.SIGINT)
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(server.pid, signal.SIGKILL)
                server.wait()
        for log in files:
            log.close()
        if server_log is not None:
            server_log.close()
        print(f"Logs retained: {output}", flush=True)

    logs = [(output / name / "game.log").read_text(errors="replace")
            for name in ("host", "client")]
    assert all("Netplay: session ready" in log for log in logs), "Handshake did not finish"
    assert "client saves isolated in" in logs[1], "Client isolation did not run"
    assert all("Netplay: synchronized read 600" in log for log in logs), \
        "Too short: no 600-read checkpoint"
    assert "room created" in read_server_log(), "The relay did not create a room"
    recordings = [(output / name / "input.bin").read_bytes() for name in ("host", "client")]
    shared = min(map(len, recordings))
    assert recordings[0][:shared] == recordings[1][:shared], "Guest input recordings differ"
    host_frames = {p.name: p for p in (output / "host/frames").glob("*.ppm")}
    client_frames = {p.name: p for p in (output / "client/frames").glob("*.ppm")}
    common = sorted(host_frames.keys() & client_frames.keys())
    assert common, "No guest frames captured"
    differences = [name for name in common
                   if hashlib.sha256(host_frames[name].read_bytes()).digest()
                   != hashlib.sha256(client_frames[name].read_bytes()).digest()]
    assert not differences, f"{len(differences)}/{len(common)} frames differ, first {differences[:5]}"
    print(f"PASS: relayed handshake, {len(common)} identical guest frames, "
          f"{shared} shared input bytes")


if __name__ == "__main__":
    main()
