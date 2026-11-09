#!/usr/bin/env python3
"""Regression checks for tools/netplay_digest_compare.py (standard library only)."""
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools/netplay_digest_compare.py"
failures = 0


def check(condition, message):
    global failures
    if condition:
        print(f"ok: {message}")
    else:
        failures += 1
        print(f"FAIL: {message}", file=sys.stderr)


def digest_line(read, value):
    return f"Netplay: state digest {value:016x} at read {read}\n"


def read_line(read):
    return f"Netplay: synchronized read {read}, ping 1 ms\n"


def run(host_text, client_text, *args):
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        host = work / "host.log"
        client = work / "client.log"
        host.write_text(host_text, encoding="utf-8")
        client.write_text(client_text, encoding="utf-8")
        return subprocess.run([sys.executable, str(TOOL), str(host), str(client), *args],
                              capture_output=True, text=True, check=False)


def main():
    base = "".join(digest_line(read, 0x1234) for read in (600, 900, 1200))
    reads = "".join(read_line(read) for read in (600, 1200))
    result = run(base + reads, base + reads, "--min-common", "3", "--min-read", "1200")
    check(result.returncode == 0, "identical peer logs pass")
    check("3 shared state digests match" in result.stdout, "pass line reports the checkpoint count")

    mismatch = base.replace(digest_line(900, 0x1234), digest_line(900, 0x9999))
    result = run(base, mismatch)
    check(result.returncode != 0, "a digest mismatch fails")
    check("mismatch at read 900" in result.stderr, "mismatch line names the read")

    result = run(base, base, "--min-common", "4")
    check(result.returncode != 0, "short coverage fails")
    check("need 4" in result.stderr, "short-coverage message states the requirement")

    result = run(base + reads, base + reads, "--min-read", "5000")
    check(result.returncode != 0, "an unmet read target fails")

    paused = base + "Netplay paused: Netplay desync at controller read 900. Close the game.\n"
    result = run(paused, base)
    check(result.returncode != 0, "a pause line fails even when the logged digests match")

    result = run("Netplay: state digest 0000000000000001 at read 600\n",
                 "Netplay: state digest 0000000000000001 at read 600\n"
                 "Netplay: state digest 0000000000000002 at read 600\n")
    check(result.returncode != 0, "conflicting lines inside one log fail")

    result = run(base, base)
    check(result.returncode == 0, "single shared checkpoint passes by default")

    if failures:
        print(f"{failures} check(s) failed", file=sys.stderr)
        return 1
    print("PASS: digest comparison catches mismatches, pauses and short coverage")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
