#!/usr/bin/env python3
"""Client for the portal IPC server that giantsrecompiled serves on 127.0.0.1:28014.

The protocol is the one Cemu's SkylanderPortalIPC (and the Omoio/skyportal menu built on it)
speaks: one command line per TCP connection, reply whose last line starts with "ok" or "error".
With the server running, an external portal menu such as skyportal/Omoio can place, remove and
create figures in the game. Adapted from skyportal's tools/portal_client.py (MIT).

Usage:
    python3 tools/portal_ipc_client.py hello
    python3 tools/portal_ipc_client.py status [--json]
    python3 tools/portal_ipc_client.py load <file> [--slot N]   # -1 = first free slot (default)
    python3 tools/portal_ipc_client.py remove <slot>
    python3 tools/portal_ipc_client.py clear
    python3 tools/portal_ipc_client.py create <id> <variant> <file>
    python3 tools/portal_ipc_client.py hush on|off
"""

import argparse
import json
import socket
import sys

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 28014
MAX_ANSWER = 8192


class PortalError(Exception):
    """The server answered with an error, or not as it should."""


def ask(command: str, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
        timeout: float = 2.0) -> str:
    """Sends one command and returns the whole reply."""
    line = command.encode("utf-8") + b"\n"
    if len(line) > 4096:
        raise PortalError("command is longer than the 4096 bytes the server reads")
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.sendall(line)
            sock.settimeout(timeout)
            chunks = []
            size = 0
            while True:
                piece = sock.recv(MAX_ANSWER)
                if not piece:
                    break
                chunks.append(piece)
                size += len(piece)
                if size > MAX_ANSWER:
                    raise PortalError("answer is longer than the server can send")
    except ConnectionRefusedError:
        raise PortalError(f"nothing is listening on {host}:{port} — is the game running?") from None
    except socket.timeout:
        raise PortalError(f"{host}:{port} did not answer within {timeout}s") from None
    reply = b"".join(chunks).decode("utf-8", "replace")
    last = reply.rstrip("\n").rsplit("\n", 1)[-1]
    if last.startswith("error"):
        raise PortalError(last.removeprefix("error").strip() or "the server refused")
    if not last.startswith("ok"):
        raise PortalError(f"unexpected answer: {reply!r}")
    return reply


def slots(host: str, port: int) -> list:
    """What is on the portal, as a list of (slot, id, variant) or (slot, None, None)."""
    reply = ask("status", host, port)
    found = []
    for line in reply.splitlines():
        if not line.startswith("slot") or line.startswith("ok"):
            continue
        parts = line.split()
        slot = int(parts[0].removeprefix("slot"))
        if parts[1] == "loaded":
            found.append((slot, int(parts[2]), int(parts[3])))
        else:
            found.append((slot, None, None))
    return found


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("hello", help="ask who is listening")
    status = sub.add_parser("status", help="show what is on the portal")
    status.add_argument("--json", action="store_true")

    load = sub.add_parser("load", help="place a figure file on the portal")
    load.add_argument("file")
    load.add_argument("--slot", type=int, default=-1, help="-1 is the first free slot (default)")

    remove = sub.add_parser("remove", help="remove the figure from a slot")
    remove.add_argument("slot", type=int)

    sub.add_parser("clear", help="remove everything from the portal")

    create = sub.add_parser("create", help="have the game create a new blank figure file")
    create.add_argument("id", type=int)
    create.add_argument("variant", type=int, default=0)
    create.add_argument("file")

    hush = sub.add_parser("hush", help="mute the game's pad input while a menu is open")
    hush.add_argument("state", choices=["on", "off"])

    args = parser.parse_args(argv)

    try:
        if args.command == "hello":
            print(ask("hello", args.host, args.port).strip())
        elif args.command == "status":
            found = slots(args.host, args.port)
            if args.json:
                print(json.dumps([{"slot": s, "id": i, "variant": v} for s, i, v in found],
                                 indent=2))
            else:
                for slot, figure_id, variant in found:
                    if figure_id is None:
                        print(f"  {slot:>2}: empty")
                    else:
                        print(f"  {slot:>2}: figure {figure_id} variant {variant}")
        elif args.command == "load":
            print(ask(f"load {args.slot} {args.file}", args.host, args.port).strip())
        elif args.command == "remove":
            print(ask(f"remove {args.slot}", args.host, args.port).strip())
        elif args.command == "clear":
            print(ask("clear", args.host, args.port).strip())
        elif args.command == "create":
            print(ask(f"create {args.id} {args.variant} {args.file}", args.host, args.port).strip())
        elif args.command == "hush":
            print(ask(f"hush {args.state}", args.host, args.port).strip())
    except PortalError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
