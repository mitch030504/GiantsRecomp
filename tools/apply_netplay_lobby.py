#!/usr/bin/env python3
"""Write the netplay relay defaults into a giantsrecomp.toml at package time.

The public templates never contain a relay address. The owner's private, game-included packages are
staged with this script so the addresses come from the build environment and stay out of git:

    GIANTSRECOMP_NETPLAY_LOBBY='lan-host:32200;wan-host:32200' just package-release

It replaces an existing `netplay_lobby` (and enables `netplay_relay = "auto"`) in place, or appends
both keys when the file has no active `netplay_lobby`. Everything else in the file is preserved.
"""
import argparse
import os
import re
import sys
import tempfile
from pathlib import Path

DEFAULT_PORT = 32200
_KEYS = ("netplay_relay", "netplay_lobby")


def parse_lobby(text):
    """Validate candidates the same way src/netplay/lobby.cpp does (minus DNS resolution)."""
    endpoints = []
    rendered = []
    for token in re.split(r"[;,]", text):
        entry = token.strip()
        if not entry:
            continue
        host = entry
        port = DEFAULT_PORT
        if ":" in entry:
            host, _, tail = entry.rpartition(":")
            if not host or not tail.isdigit():
                raise ValueError(f"invalid relay candidate {entry!r}")
            port = int(tail)
            if not 1 <= port <= 65535:
                raise ValueError(f"relay port out of range in {entry!r}")
        if len(host) > 255 or any(c in host for c in " \t/"):
            raise ValueError(f"invalid relay host {host!r}")
        if (host, port) not in endpoints:
            endpoints.append((host, port))
            rendered.append(entry)
    if not endpoints:
        raise ValueError("no relay candidate given")
    return endpoints, ";".join(rendered)


def _active_value(line):
    """Return (key, value) for a bare, non-comment assignment line, else None."""
    stripped = line.lstrip()
    if stripped.startswith("#"):
        return None
    match = re.match(r"^(?P<key>[A-Za-z0-9_]+)\s*=\s*(?P<value>.*)$", stripped)
    if not match or match.group("key") not in _KEYS:
        return None
    return match.group("key"), match.group("value")


def _replace_key(lines, key, value):
    """Replace every active assignment of `key`, keeping indentation and a trailing comment."""
    replaced = 0
    out = []
    pattern = re.compile(rf'^(?P<indent>\s*){re.escape(key)}\s*=\s*"(?P<old>[^"]*)"'
                         rf'(?P<comment>\s*#.*)?$')
    for line in lines:
        body = line.rstrip("\r\n")
        match = pattern.match(body)
        if match:
            out.append(f'{match.group("indent")}{key} = "{value}"'
                       f'{match.group("comment") or ""}\n')
            replaced += 1
            continue
        # A non-string value is still a valid TOML assignment; replace it wholesale.
        active = _active_value(body)
        if active and active[0] == key:
            out.append(f'{body[:len(body) - len(body.lstrip())]}{key} = "{value}"\n')
            replaced += 1
            continue
        out.append(line)
    return out, replaced


def apply_lobby(path, lobby):
    endpoints, rendered = parse_lobby(lobby)
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith(("\n", "\r")):
        lines[-1] += "\n"

    lines, relay_count = _replace_key(lines, "netplay_relay", "auto")
    lines, lobby_count = _replace_key(lines, "netplay_lobby", rendered)
    missing = []
    if relay_count == 0:
        missing.append('netplay_relay = "auto"')
    if lobby_count == 0:
        missing.append(f'netplay_lobby = "{rendered}"')
    if missing:
        if lines and lines[-1].strip():
            lines.append("\n")
        lines.append("# Injected at package time by tools/apply_netplay_lobby.py; not part of the\n")
        lines.append("# public template. Point this at your own relay, never at a stranger's.\n")
        lines.extend(f"{line}\n" for line in missing)

    path.write_text("".join(lines), encoding="utf-8")
    return endpoints, rendered


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("toml", type=Path, help="giantsrecomp.toml to update in place")
    parser.add_argument("lobby",
                        help="relay host[:port], separated by ';' (LAN first, then external)")
    args = parser.parse_args(argv)
    if not args.toml.is_file():
        print(f"error: {args.toml} does not exist", file=sys.stderr)
        return 2
    try:
        endpoints, rendered = parse_lobby(args.lobby)
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    before = args.toml.stat()
    fd, temporary = tempfile.mkstemp(prefix=args.toml.name + ".", dir=args.toml.parent)
    os.close(fd)
    staged = Path(temporary)
    try:
        staged.write_bytes(args.toml.read_bytes())
        os.chmod(staged, before.st_mode & 0o777)
        apply_lobby(staged, rendered)
        os.replace(staged, args.toml)
    finally:
        staged.unlink(missing_ok=True)
    print(f"applied netplay_relay=\"auto\" and netplay_lobby=\"{rendered}\" "
          f"({len(endpoints)} candidate(s)) to {args.toml}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
