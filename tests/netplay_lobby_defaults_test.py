#!/usr/bin/env python3
"""Public relay defaults stay neutral; package-time injection adds the owner's.

Run directly or through ctest (`netplay_lobby_defaults`). Uses only the standard library.
"""
import ipaddress
import re
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = [ROOT / "giantsrecomp.toml.example", ROOT / "installer/settings_template.toml"]
INJECTOR = ROOT / "tools/apply_netplay_lobby.py"
# Documentation and test fixtures may use the RFC 5737 ranges; anything else public is a leak.
DOCUMENTATION_NETS = tuple(ipaddress.ip_network(net) for net in
                           ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24"))
PUBLIC_FILES = TEMPLATES + [ROOT / "docs/netplay.md", ROOT / "tests/netplay_lobby_test.cpp"]

failures = 0


def ipv4_addresses(text):
    """Yield IPv4 literals that are neither private/loopback nor documentation addresses."""
    for match in re.finditer(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", text):
        try:
            address = ipaddress.ip_address(match.group())
        except ValueError:
            continue
        if address.is_private or address.is_loopback or address.is_link_local \
                or address.is_unspecified:
            continue
        if any(address in network for network in DOCUMENTATION_NETS):
            continue
        yield str(address)


def check(condition, message):
    global failures
    if condition:
        print(f"ok: {message}")
    else:
        failures += 1
        print(f"FAIL: {message}", file=sys.stderr)


def run_injector(path, lobby):
    return subprocess.run(
        [sys.executable, str(INJECTOR), str(path), lobby],
        capture_output=True, text=True, check=False)


def load(path):
    return tomllib.loads(path.read_text(encoding="utf-8"))


def lookup(document, key, default=None):
    """The cvar loader flattens [tables]; find a bare key the same way."""
    if key in document:
        return document[key]
    for child in document.values():
        if isinstance(child, dict):
            found = lookup(child, key, None)
            if found is not None:
                return found
    return default


def main():
    for template in TEMPLATES:
        check(template.is_file(), f"{template.relative_to(ROOT)} exists")
        if not template.is_file():
            continue
        text = template.read_text(encoding="utf-8")
        # The installer template carries wizard placeholders; substitute them the way
        # installer/linux-setup.sh and GiantsRecomp.iss do, so the result must still parse as TOML.
        for placeholder, value in (("__PORTAL_MODE__", "software"),
                                   ("__RESOLUTION__", "1920x1080"),
                                   ("__RESOLUTION_SCALE__", "1")):
            text = text.replace(placeholder, value)
        document = tomllib.loads(text)
        check(lookup(document, "netplay_lobby", "") == "",
              f"{template.relative_to(ROOT)} has an empty netplay_lobby")
        check(not list(ipv4_addresses(text)),
              f"{template.relative_to(ROOT)} contains no public relay literal")

    for path in PUBLIC_FILES[2:]:
        text = path.read_text(encoding="utf-8")
        check(not list(ipv4_addresses(text)),
              f"{path.relative_to(ROOT)} contains no public relay literal")

    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        target = work / "giantsrecomp.toml"
        target.write_text(TEMPLATES[0].read_text(encoding="utf-8"), encoding="utf-8")
        before = load(target)

        result = run_injector(target, "192.0.2.10:32200;198.51.100.20:32200")
        check(result.returncode == 0, "injector accepts an ordered candidate list")
        injected = load(target)
        check(lookup(injected, "netplay_lobby") == "192.0.2.10:32200;198.51.100.20:32200",
              "injector writes the candidate list")
        check(lookup(injected, "netplay_relay") == "auto", "injector enables relay auto")
        check(lookup(injected, "resolution") == lookup(before, "resolution"), "injector preserves other keys")
        active = [line for line in target.read_text(encoding="utf-8").splitlines()
                  if line.strip().startswith("netplay_lobby")]
        check(len(active) == 1, "injector leaves exactly one active netplay_lobby")

        snapshot = target.read_text(encoding="utf-8")
        result = run_injector(target, "192.0.2.10:32200;198.51.100.20:32200")
        check(result.returncode == 0 and target.read_text(encoding="utf-8") == snapshot,
              "injector is idempotent")

        for bad in (":32200", "host:0", "host:65536", "bad host:32200", ""):
            unchanged = target.read_text(encoding="utf-8")
            result = run_injector(target, bad)
            check(result.returncode != 0, f"injector rejects {bad!r}")
            check(target.read_text(encoding="utf-8") == unchanged,
                  f"injector leaves the file unchanged for {bad!r}")

        commented = work / "commented.toml"
        commented.write_text(
            '["Runtime"]\nuser_data_root = ""\n# netplay_lobby = "old:1"\n', encoding="utf-8")
        result = run_injector(commented, "relay.example.com:32200")
        check(result.returncode == 0, "injector accepts a file without active relay keys")
        appended = load(commented)
        check(lookup(appended, "netplay_lobby") == "relay.example.com:32200",
              "injector appends a missing netplay_lobby")
        check(lookup(appended, "netplay_relay") == "auto", "injector appends a missing netplay_relay")

        single = work / "single.toml"
        single.write_text('["Runtime"]\nnetplay_lobby = "old:32200" # keep me\n',
                          encoding="utf-8")
        result = run_injector(single, "relay.example.com")
        text = single.read_text(encoding="utf-8")
        check(result.returncode == 0, "injector replaces an unquoted-port candidate")
        check(text.count("netplay_lobby =") == 1 and "keep me" in text,
              "injector replaces in place and preserves the trailing comment")

    if failures:
        print(f"{failures} check(s) failed", file=sys.stderr)
        return 1
    print("PASS: public relay defaults are neutral and injection is safe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
