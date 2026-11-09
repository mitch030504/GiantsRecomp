#!/usr/bin/env python3
"""Compare the logical state digests logged by two netplay peers.

The digest is computed every `netplay_state_digest_interval` controller reads (default 300), sent
inside each input frame and compared by lockstep. This reader checks the logged values directly, so
a long rally leaves auditable evidence even when no mismatch occurs:

    python3 tools/netplay_digest_compare.py HOST_LOG CLIENT_LOG --min-common 300

Exit codes: 0 = matches (and enough coverage), 1 = mismatch / short coverage / pause, 2 = usage or
unreadable log.
"""
import argparse
import re
import sys
from pathlib import Path

DIGEST = re.compile(r"Netplay: state digest ([0-9a-fA-F]{16}) at read (\d+)")
READ = re.compile(r"Netplay: synchronized read (\d+)")
PAUSE = re.compile(r"Netplay (?:paused|desync)")


def parse(path):
    digests = {}
    reads = []
    pauses = []
    duplicates = []
    for line in path.read_text(errors="replace").splitlines():
        match = DIGEST.search(line)
        if match:
            read = int(match.group(2))
            value = int(match.group(1), 16)
            if read in digests and digests[read] != value:
                duplicates.append((read, digests[read], value))
            digests[read] = value
        match = READ.search(line)
        if match:
            reads.append(int(match.group(1)))
        if PAUSE.search(line):
            pauses.append(line.strip())
    return {"digests": digests, "reads": reads, "pauses": pauses, "duplicates": duplicates}


def describe(name, data):
    first = min(data["digests"], default=None)
    last = max(data["digests"], default=None)
    print(f"{name}: {len(data['digests'])} digest line(s), reads {first}..{last}, "
          f"{len(data['reads'])} synchronized-read line(s), {len(data['pauses'])} pause line(s)")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host_log", type=Path)
    parser.add_argument("client_log", type=Path)
    parser.add_argument("--min-common", type=int, default=1,
                        help="minimum shared digest checkpoints required (default 1)")
    parser.add_argument("--min-read", type=int, default=0,
                        help="minimum synchronized read both peers must reach (default 0)")
    args = parser.parse_args(argv)
    for path in (args.host_log, args.client_log):
        if not path.is_file():
            print(f"error: {path} does not exist", file=sys.stderr)
            return 2
    host = parse(args.host_log)
    client = parse(args.client_log)
    describe("host", host)
    describe("client", client)

    failed = False
    for name, data in (("host", host), ("client", client)):
        if data["duplicates"]:
            failed = True
            read, first, second = data["duplicates"][0]
            print(f"FAIL: {name} logged conflicting digests at read {read}: "
                  f"{first:016x} then {second:016x}", file=sys.stderr)
        if data["pauses"]:
            failed = True
            print(f"FAIL: {name} reported {data['pauses'][0]}", file=sys.stderr)

    common = sorted(set(host["digests"]) & set(client["digests"]))
    mismatches = [(read, host["digests"][read], client["digests"][read]) for read in common
                  if host["digests"][read] != client["digests"][read]]
    print(f"common checkpoints: {len(common)}"
          + (f" (reads {common[0]}..{common[-1]})" if common else ""))
    if mismatches:
        failed = True
        for read, first, second in mismatches[:10]:
            print(f"FAIL: digest mismatch at read {read}: host {first:016x} client {second:016x}",
                  file=sys.stderr)
        if len(mismatches) > 10:
            print(f"FAIL: ... and {len(mismatches) - 10} more mismatch(es)", file=sys.stderr)
    if len(common) < args.min_common:
        failed = True
        print(f"FAIL: only {len(common)} shared checkpoint(s); need {args.min_common}",
              file=sys.stderr)
    if args.min_read:
        host_max = max(host["reads"], default=0)
        client_max = max(client["reads"], default=0)
        if min(host_max, client_max) < args.min_read:
            failed = True
            print(f"FAIL: peers only reached read {min(host_max, client_max)}; "
                  f"need {args.min_read}", file=sys.stderr)
    if failed:
        return 1
    print(f"PASS: {len(common)} shared state digests match"
          + (f" through read {common[-1]}" if common else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
