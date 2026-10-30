#!/usr/bin/env bash
# Linux counterpart of GiantsRecomp.iss: puts your game into rom/ next to this script and writes
# giantsrecomp.toml. Run it from the unpacked release folder.
#
#   ./linux-setup.sh <game.iso | extracted-folder> [--portal software|usb|none]
#                    [--resolution 1920x1080] [--scale 1]
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
rom="$here/rom"
portal="software"
resolution="1920x1080"
scale="1"
src=""

die() { echo "error: $*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --portal) portal="${2:-}"; shift 2 ;;
    --resolution) resolution="${2:-}"; shift 2 ;;
    --scale) scale="${2:-}"; shift 2 ;;
    -h|--help) sed -n 2,6p "$0"; exit 0 ;;
    *) [ -z "$src" ] || die "unexpected argument: $1"; src="$1"; shift ;;
  esac
done

[ -n "$src" ] || die "give the path to your Skylanders Giants ISO or extracted folder (see --help)"
case "$portal" in software|usb|none) ;; *) die "--portal must be software, usb or none" ;; esac
[[ "$resolution" =~ ^[0-9]+x[0-9]+$ ]] || die "--resolution must look like 1920x1080"
[[ "$scale" =~ ^[1-8]$ ]] || die "--scale must be 1 to 8"
[ -x "$here/giantsrecomp_xexcheck" ] || die "giantsrecomp_xexcheck not found next to this script"
[ ! -e "$rom/default.xex" ] || die "$rom already has a game in it; remove it first to reinstall"

# Same exit codes as src/tools/xex_check_exit_code.h.
check_xex() {
  local code=0
  "$here/giantsrecomp_xexcheck" "$1" || code=$?
  case "$code" in
    0) ;;
    2) echo "This isn't Skylanders Giants v1.0 (USA/EU), the only version that works." >&2; return 1 ;;
    3) echo "Couldn't read $1." >&2; return 1 ;;
    *) echo "Version check failed (exit code $code)." >&2; return 1 ;;
  esac
}

if [ -d "$src" ]; then
  [ -f "$src/default.xex" ] || die "default.xex not found in $src; is this the extracted disc?"
  check_xex "$src/default.xex"
  src_real="$(realpath "$src")"
  case "$here/" in "$src_real"/*) die "pick your original extracted folder, not one containing this install" ;; esac
  echo "Copying game files into $rom ..."
  mkdir -p "$rom"
  # Roll back a partial copy (e.g. a full disk) so a rerun isn't blocked by half a game.
  trap 'rm -rf "$rom"' ERR
  cp -r "$src_real"/. "$rom"/
  trap - ERR
elif [ -f "$src" ]; then
  mkdir -p "$rom"
  echo "Extracting $src into $rom ..."
  # Roll back a partial or wrong-version extraction so rom/ never holds bad game files.
  trap 'rm -rf "$rom"' ERR
  if command -v xdvdfs >/dev/null; then
    xdvdfs unpack "$src" "$rom"
  elif command -v extract-xiso >/dev/null; then
    extract-xiso -s -x "$src" -d "$rom"
  else
    die "install xdvdfs or extract-xiso to unpack an ISO, or pass an already-extracted folder"
  fi
  [ -f "$rom/default.xex" ] || { echo "default.xex not found in the ISO." >&2; false; }
  check_xex "$rom/default.xex"
  trap - ERR
else
  die "$src does not exist"
fi

# Never overwrite: the in-game F4 settings save the user's own changes to this file.
if [ ! -e "$here/giantsrecomp.toml" ]; then
  sed -e "s/__PORTAL_MODE__/$portal/" -e "s/__RESOLUTION__/$resolution/" \
      -e "s/__RESOLUTION_SCALE__/$scale/" "$here/settings_template.toml" > "$here/giantsrecomp.toml"
fi

# The game finds rom/ relative to the working directory, so start it from here.
echo "Done. Start the game with: cd \"$here\" && ./giantsrecompiled"
if [ "$portal" = usb ]; then
  echo "For a real portal without root, install the udev rule once:"
  echo "  sudo cp $here/70-portal-of-power.rules /etc/udev/rules.d/ && sudo udevadm control --reload"
fi
