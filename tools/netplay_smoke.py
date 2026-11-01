#!/usr/bin/env python3
"""Run two headless native peers with isolated saves and compare their guest outputs."""
import argparse
import hashlib
import os
from pathlib import Path
import signal
import socket
import subprocess
import tempfile
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=float, default=180)
    drive = parser.add_mutually_exclusive_group()
    drive.add_argument('--replay', type=Path, help='Host local input recording; client stays neutral')
    drive.add_argument('--script', type=Path, help='Host frame-based input script')
    parser.add_argument('--client-script', type=Path, help='Optional local input script for player 2')
    parser.add_argument('--client-figure', default='',
                        help='Test: client places a blank figure of this Skylander in slot 1 at '
                             '--client-figure-read (after boot; early placement is rejected by '
                             'the game with "toy has a problem")')
    parser.add_argument('--client-figure-read', type=int, default=300,
                        help='Read index for --client-figure (default 300)')
    parser.add_argument('--hash-interval', type=int, default=0)
    parser.add_argument('--audible', action='store_true', help='Play test audio through the default device')
    args = parser.parse_args()
    if args.seconds <= 0 or args.hash_interval < 0:
        parser.error('seconds must be positive and hash-interval nonnegative')
    root = Path(__file__).resolve().parent.parent
    output_root = root / 'out/tmp'
    output_root.mkdir(parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix='netplay-smoke-', dir=output_root))
    for name in ('host', 'client'):
        (output / name / 'frames').mkdir(parents=True)
        (output / name / 'saves').mkdir()
    # An existing client save must survive, and must never become the active session save.
    sentinel = output / 'client/saves/DEADBEEF/profile/User/preserve.bin'
    sentinel.parent.mkdir(parents=True)
    sentinel.write_bytes(b'client save stays untouched')
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    env = os.environ.copy()
    env.pop('WAYLAND_DISPLAY', None)
    # Keep the normal SDL callback/clock while unattended peers stay inaudible.
    if not args.audible:
        env['SDL_AUDIO_DRIVER'] = 'dummy'
    env['LD_LIBRARY_PATH'] = str(root / 'thirdparty/rexglue-sdk/out/linux-amd64')
    processes = []
    files = []
    try:
        for name in ('host', 'client'):
            folder = output / name
            command = ['gamescope', '--backend', 'headless', '-W', '1280', '-H', '720', '--',
                       str(root / 'out/build/linux-amd64-release/giantsrecompiled'),
                       '--resolution=1280x720', '--resolution_scale=1',
                       '--frame_rate_limit=60', '--netplay_timeout_seconds=15',
                       f'--user_data_root={folder / "saves"}', f'--log_file={folder / "game.log"}',
                       f'--input_record={folder / "input.bin"}',
                       f'--guest_output_dump_prefix={folder / "frames/f"}',
                       '--guest_output_dump_interval=120']
            if name == 'host':
                command += ['--netplay_host=true', f'--netplay_port={port}',
                            '--portal_bot_figure=Spyro', '--bot_skip_movies=true',
                            f'--netplay_hash_interval={args.hash_interval}']
                if args.replay:
                    command += [f'--input_replay={args.replay.resolve()}']
                if args.script:
                    command += [f'--input_script={args.script.resolve()}']
            else:
                command += [f'--netplay_connect=127.0.0.1:{port}']
                if args.client_script:
                    command += [f'--input_script={args.client_script.resolve()}']
                if args.client_figure:
                    command += [f'--netplay_test_client_figure={args.client_figure}',
                                f'--netplay_test_client_figure_read={args.client_figure_read}']
            log = (folder / 'stdout.log').open('wb')
            files.append(log)
            processes.append(subprocess.Popen(command, cwd=root, env=env, stdout=log,
                                              stderr=subprocess.STDOUT, start_new_session=True))
            if name == 'host':
                time.sleep(0.5)
        print(f'Peers running; output: {output}', flush=True)
        deadline = time.monotonic() + args.seconds
        while time.monotonic() < deadline:
            if any(process.poll() is not None for process in processes):
                raise RuntimeError('A peer exited early; inspect game.log/stdout.log')
            for name in ('host', 'client'):
                log = output / name / 'game.log'
                if log.exists() and 'Netplay paused:' in log.read_text(errors='replace'):
                    raise RuntimeError(f'{name} paused; inspect game.log')
            time.sleep(1)
    finally:
        # Signal only processes owned by this run; never kill existing game/gamescope sessions.
        for process in processes:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGINT)
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        for log in files:
            log.close()
        print(f'Logs retained: {output}', flush=True)
    assert sentinel.read_bytes() == b'client save stays untouched'
    logs = [(output / name / 'game.log').read_text(errors='replace') for name in ('host', 'client')]
    assert all('Netplay: session ready' in log for log in logs), 'Handshake did not finish'
    assert 'client saves isolated in' in logs[1], 'Client isolation did not run'
    assert all('Netplay: synchronized read 600' in log for log in logs), 'Too short: no 600-read checkpoint'
    if args.client_figure:
        assert all('Netplay: player 1 placed figure in slot 1' in log for log in logs), \
            'Client figure placement was not applied on both peers'
    recordings = [(output / name / 'input.bin').read_bytes() for name in ('host', 'client')]
    shared = min(map(len, recordings))
    assert recordings[0][:shared] == recordings[1][:shared], 'Guest input recordings differ'
    host_frames = {p.name: p for p in (output / 'host/frames').glob('*.ppm')}
    client_frames = {p.name: p for p in (output / 'client/frames').glob('*.ppm')}
    common = sorted(host_frames.keys() & client_frames.keys())
    assert common, 'No guest frames captured'
    differences = [name for name in common if hashlib.sha256(host_frames[name].read_bytes()).digest()
                   != hashlib.sha256(client_frames[name].read_bytes()).digest()]
    assert not differences, f'{len(differences)}/{len(common)} common frames differ, first {differences[:5]}'
    print(f'PASS: {len(common)} identical guest frames; {shared} shared input bytes; client save preserved')


if __name__ == '__main__':
    main()
