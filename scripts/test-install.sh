#!/bin/sh
# Install built debs in a fresh debian:trixie container and check them.
# Usage: scripts/test-install.sh PATH.deb[::SMOKE_COMMAND] ...
# Debs are installed together in the order given (so Depends between them resolve).
# Debs of a foreign architecture are skipped with a notice (no emulation).
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
[ $# -gt 0 ] || { echo "usage: $0 PATH.deb[::SMOKE] ..." >&2; exit 2; }
T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT
host_arch="$(dpkg --print-architecture 2>/dev/null || uname -m)"
: > "$T/tests.list"
for spec in "$@"; do
	deb="${spec%%::*}"
	smoke=""
	case "$spec" in *::*) smoke="${spec#*::}" ;; esac
	[ -f "$deb" ] || { echo "no such deb: $deb" >&2; exit 2; }
	arch="$(dpkg-deb -f "$deb" Architecture)"
	if [ "$arch" != "$host_arch" ] && [ "$arch" != all ]; then
		echo "SKIP $deb: architecture $arch differs from host $host_arch (no emulation)"
		continue
	fi
	cp "$deb" "$T/"
	printf '%s|%s|%s|%s\n' "$(dpkg-deb -f "$deb" Package)" "$(dpkg-deb -f "$deb" Version)" "$(basename "$deb")" "$smoke" >> "$T/tests.list"
done
[ -s "$T/tests.list" ] || { echo "nothing to test"; exit 0; }
docker run --rm -v "$T:/debs:ro" -v "$HERE/in-container.sh:/in-container.sh:ro" debian:trixie sh /in-container.sh
