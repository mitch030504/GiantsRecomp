#!/usr/bin/env python3
"""Compare two state_hash_log files from runs fed the same input_replay recording.

Prints, for every checkpoint both runs reached, which 1 MB guest memory buckets differ. A bucket
that differs at every checkpoint from the start is usually host-written (GPU, audio, timers) and
not game state; the first checkpoint where a bucket that used to match starts differing is where
the runs actually diverged. See docs/netplay.md.

Usage: python3 compare_state_hashes.py <run_a.log> <run_b.log>
"""

import sys


def load(path):
    """Returns {read_index: {bucket_address: hash}} in file order."""
    checkpoints = {}
    with open(path, encoding="ascii") as f:
        for line_no, line in enumerate(f, 1):
            parts = line.split()
            if not parts:
                continue
            try:
                buckets = dict(p.split(":", 1) for p in parts[1:])
                checkpoints[int(parts[0])] = buckets
            except ValueError:
                sys.exit(f"{path}:{line_no}: not a state_hash_log line")
    return checkpoints


def compare(a, b):
    """Yields (read_index, differing_buckets, newly_differing_buckets) per shared checkpoint."""
    seen_differ = set()
    for index in sorted(set(a) & set(b)):
        buckets = set(a[index]) | set(b[index])
        differ = {k for k in buckets if a[index].get(k) != b[index].get(k)}
        yield index, differ, differ - seen_differ
        seen_differ |= differ


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    a, b = load(sys.argv[1]), load(sys.argv[2])
    shared = 0
    for index, differ, new in compare(a, b):
        shared += 1
        if not differ:
            print(f"read {index}: identical")
            continue
        new_list = " ".join(sorted(new)) if new else "-"
        print(f"read {index}: {len(differ)} buckets differ; newly differing: {new_list}")
    if not shared:
        sys.exit("The two logs share no checkpoints (different state_hash_interval?)")


if __name__ == "__main__":
    main()
