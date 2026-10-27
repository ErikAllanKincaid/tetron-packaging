#!/bin/sh
# Runs inside a fresh debian:trixie container. /debs holds the debs and tests.list
# (lines: name|version|debfile|smoke). Fails closed: a package that did not install is
# never reported as cleanly removed.
export DEBIAN_FRONTEND=noninteractive
fail=0
installed=""
ok()   { echo "  ok   $*"; }
bad()  { echo "  FAIL $*"; fail=1; }
check() { desc="$1"; shift; if "$@" >/dev/null 2>&1; then ok "$desc"; else bad "$desc"; fi; }

apt-get update -qq >/dev/null 2>&1
apt-get install -y -qq --no-install-recommends systemd >/dev/null 2>&1

while IFS='|' read -r name version file smoke; do
	echo "== $name $version"
	if apt-get install -y -qq --no-install-recommends "/debs/$file" >/tmp/apt.log 2>&1; then
		ok "apt install resolves Depends"; installed="$installed $name"
	else bad "apt install"; tail -5 /tmp/apt.log; continue; fi
	check "dpkg status ii" sh -c "dpkg -s $name | grep -q 'Status: install ok installed'"
	missing=$(dpkg -L "$name" | while read -r f; do [ -e "$f" ] || echo "$f"; done)
	[ -z "$missing" ] && ok "listed files exist" || bad "missing files: $missing"
	check "md5sums verify" dpkg --verify "$name"
	if [ -n "$smoke" ]; then
		out=$(sh -c "$smoke" 2>&1)
		echo "$out" | grep -q "${version%-*}" && ok "smoke: $smoke -> $out" || bad "smoke: $smoke -> $out"
	fi
	for u in $(dpkg -L "$name" | grep '^/usr/lib/systemd/system/.*\.service$'); do
		check "systemd-analyze verify $u" systemd-analyze verify "$u"
		check "unit not enabled by package" sh -c "! [ -e /etc/systemd/system/multi-user.target.wants/$(basename "$u") ]"
	done
done < /debs/tests.list

# Remove and purge in reverse order, only what actually installed.
for name in $(for n in $installed; do echo "$n"; done | tac); do
	echo "== remove/purge $name"
	files=$(dpkg -L "$name" | while read -r f; do [ -f "$f" ] && echo "$f"; done)
	check "dpkg -r" dpkg -r "$name"
	check "dpkg -P" dpkg -P "$name"
	left=$(for f in $files; do [ -e "$f" ] && echo "$f"; done)
	[ -z "$left" ] && ok "files gone" || bad "files left: $left"
done
[ $fail = 0 ] && echo "ALL PASS" || echo "FAILURES"
exit $fail
