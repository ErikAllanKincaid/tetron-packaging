#!/usr/bin/env python3
"""mkdeb: build .deb packages for the tetron suite. Stdlib + dpkg-deb + readelf only.

  mkdeb.py build    --deb-toml FILE --binary FILE --version V [--revision 1] [--arch A]
                    [--maintainer M] [--epoch N] [--out DIR]
  mkdeb.py manifest --manifest FILE [--only NAME] [--out DIR] [--cache DIR]
  mkdeb.py list-tests --manifest FILE

See README.md for the deb.toml format. Spec: spec/mkdeb.py.
"""
import argparse
import hashlib
import os
import re
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import urllib.request
from pathlib import Path

# ELF e_machine -> Debian architecture
MACHINES = {0x3E: "amd64", 0xB7: "arm64"}

# NEEDED soname -> Debian package (version floor added separately for libc)
LIBC_SONAMES = re.compile(r"^(libc\.so\.6|libm\.so\.6|libdl\.so\.2|libpthread\.so\.0|librt\.so\.1|"
                          r"libutil\.so\.1|libresolv\.so\.2|ld-linux.*\.so\.\d+)$")
SONAME_PKGS = {"libgcc_s.so.1": "libgcc-s1", "libstdc++.so.6": "libstdc++6", "libz.so.1": "zlib1g"}

PKG_KEYS = {"name", "description", "long_description", "section", "priority", "license", "homepage",
            "maintainer", "depends", "recommends", "conflicts", "smoke", "soname_map", "files", "scripts"}
MANIFEST_PKG_KEYS = PKG_KEYS | {"version", "revision", "arch", "source"}
SCRIPT_NAMES = {"preinst", "postinst", "prerm", "postrm"}
FILE_KEYS = {"src", "dst", "mode"}
SOURCE_KEYS = {"url", "sha256", "sha256_url", "extract"}

BAD_SCRIPT = re.compile(r"systemctl\s+(--\S+\s+)*(start|enable|restart|reload-or-restart)\b|"
                        r"deb-systemd-invoke|\baddgroup\b|\badduser\b")


class MkdebError(Exception):
    pass


# ---- ELF inspection ---------------------------------------------------------

def elf_arch(path: Path) -> str:
    with path.open("rb") as f:
        h = f.read(20)
    if len(h) < 20 or h[:4] != b"\x7fELF":
        raise MkdebError(f"{path}: not an ELF file")
    if h[5] != 1:
        raise MkdebError(f"{path}: big-endian ELF is not supported")
    machine = struct.unpack("<H", h[18:20])[0]
    if machine not in MACHINES:
        raise MkdebError(f"{path}: unsupported ELF machine 0x{machine:x} (supported: amd64, arm64)")
    return MACHINES[machine]


def readelf(args: list[str], path: Path) -> str:
    if not shutil.which("readelf"):
        raise MkdebError("readelf not found (install binutils)")
    r = subprocess.run(["readelf", *args, str(path)], capture_output=True, text=True)
    if r.returncode:
        raise MkdebError(f"readelf failed on {path}: {r.stderr.strip()}")
    return r.stdout


def parse_needed(text: str) -> list[str]:
    return re.findall(r"\(NEEDED\)\s+Shared library: \[([^\]]+)\]", text)


def parse_glibc_floor(text: str) -> tuple[int, ...] | None:
    vers = [tuple(int(x) for x in m.split(".")) for m in re.findall(r"GLIBC_(\d+(?:\.\d+)*)", text)]
    return max(vers) if vers else None


def compute_depends(binary: Path, soname_map: dict[str, str]) -> list[str]:
    needed = parse_needed(readelf(["-d"], binary))
    deps: list[str] = []
    uses_libc = False
    for so in needed:
        if LIBC_SONAMES.match(so):
            uses_libc = True
        elif so in soname_map:
            deps.append(soname_map[so])
        elif so in SONAME_PKGS:
            deps.append(SONAME_PKGS[so])
        else:
            raise MkdebError(f"{binary.name}: unknown shared library {so}; map it in deb.toml [soname_map]")
    if uses_libc:
        floor = parse_glibc_floor(readelf(["-V"], binary))
        deps.insert(0, "libc6 (>= %s)" % ".".join(map(str, floor)) if floor else "libc6")
    return deps


def merge_depends(computed: list[str], declared: list[str]) -> list[str]:
    """Declared entries win over computed ones for the same package name."""
    name = lambda d: d.split()[0].split(":")[0]
    out = {name(d): d for d in computed}
    out.update({name(d): d for d in declared})
    return list(out.values())


# ---- validation -------------------------------------------------------------

def check_keys(d: dict, allowed: set[str], where: str) -> None:
    extra = set(d) - allowed
    if extra:
        raise MkdebError(f"{where}: unknown key(s): {', '.join(sorted(extra))}")


def validate_pkg(p: dict, where: str, allowed: set[str]) -> None:
    check_keys(p, allowed, where)
    for k in ("name", "description", "license", "files"):
        if not p.get(k):
            raise MkdebError(f"{where}: missing required key '{k}'")
    for f in p["files"]:
        check_keys(f, FILE_KEYS, f"{where} files")
        for k in FILE_KEYS:
            if k not in f:
                raise MkdebError(f"{where}: file entry missing '{k}': {f}")
        if f["dst"].startswith("/") or ".." in Path(f["dst"]).parts:
            raise MkdebError(f"{where}: dst must be relative without '..': {f['dst']}")
    bad = set(p.get("scripts", {})) - SCRIPT_NAMES
    if bad:
        raise MkdebError(f"{where}: unknown script(s): {', '.join(sorted(bad))}")


def scan_script(path: Path) -> None:
    for n, line in enumerate(path.read_text().splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        m = BAD_SCRIPT.search(line)
        if m:
            raise MkdebError(f"{path}:{n}: maintainer script policy violation '{m.group(0)}' "
                             "(no service start/enable, no addgroup/adduser; use groupadd --system)")


# ---- assembling -------------------------------------------------------------

def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def resolve_epoch(explicit: int | None, anchor: Path, fallback: int | None = None) -> int:
    if explicit is not None:
        return explicit
    if os.environ.get("SOURCE_DATE_EPOCH"):
        return int(os.environ["SOURCE_DATE_EPOCH"])
    if fallback is not None:
        return fallback
    r = subprocess.run(["git", "-C", str(anchor), "log", "-1", "--format=%ct"], capture_output=True, text=True)
    if r.returncode == 0 and r.stdout.strip().isdigit():
        return int(r.stdout.strip())
    raise MkdebError("no timestamp: pass --epoch, set SOURCE_DATE_EPOCH, or build from a git checkout")


def expand_version_tokens(items: list[str], version: str) -> list[str]:
    """Replace {minor} (0.14) and {next_minor} (0.15) from the package version (0.14.2)."""
    if not any("{minor}" in i or "{next_minor}" in i for i in items):
        return items
    m = re.match(r"^(\d+)\.(\d+)(?:\.|$)", version)
    if not m:
        raise MkdebError(f"version {version!r} has no X.Y form; cannot expand {{minor}}/{{next_minor}}")
    major, minor = int(m.group(1)), int(m.group(2))
    return [i.replace("{minor}", f"{major}.{minor}").replace("{next_minor}", f"{major}.{minor + 1}") for i in items]


def control_text(p: dict, version: str, arch: str, maintainer: str, size_kib: int, depends: list[str]) -> str:
    longd = "\n".join(" " + (l if l.strip() else ".") for l in p.get("long_description", "").strip().splitlines())
    fields = [("Package", p["name"]), ("Version", version), ("Architecture", arch), ("Maintainer", maintainer),
              ("Installed-Size", str(size_kib)), ("Depends", ", ".join(depends)),
              ("Recommends", ", ".join(p.get("recommends", []))), ("Conflicts", ", ".join(p.get("conflicts", []))),
              ("Section", p.get("section", "misc")), ("Priority", p.get("priority", "optional")),
              ("Homepage", p.get("homepage", ""))]
    out = "".join(f"{k}: {v}\n" for k, v in fields if v)
    return out + f"Description: {p['description']}\n" + (longd + "\n" if longd else "")


def assemble(p: dict, base: Path, binary: Path, version: str, revision: str, arch: str | None,
             maintainer: str, epoch: int, out: Path, extra_depends: list[str] | None = None) -> Path:
    """Build one .deb. `base` is the directory deb.toml paths are relative to."""
    name = p["name"]
    barch = elf_arch(binary)
    if arch and arch != barch:
        raise MkdebError(f"{name}: --arch {arch} does not match the binary ({barch})")
    version = version.lstrip("v")
    depends = merge_depends(compute_depends(binary, p.get("soname_map", {})),
                            expand_version_tokens((extra_depends or []) + p.get("depends", []), version))
    p = dict(p, recommends=expand_version_tokens(p.get("recommends", []), version),
             conflicts=expand_version_tokens(p.get("conflicts", []), version))
    for s in p.get("scripts", {}).values():
        scan_script(base / s)
    with tempfile.TemporaryDirectory() as td:
        stage = Path(td) / "stage"
        for f in p["files"]:
            src = binary if f["src"] in ("binary", "asset") else base / f["src"]
            if not src.is_file():
                raise MkdebError(f"{name}: missing file {src}")
            dst = stage / f["dst"]
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            dst.chmod(int(f["mode"], 8))
        lic = stage / "usr/share/doc" / name / "copyright"
        lic.parent.mkdir(parents=True, exist_ok=True)
        lic.write_text(f"Upstream: {p.get('homepage', '')}\nLicense: {p['license']}\n"
                       f"See /usr/share/common-licenses/ for common license texts.\n")
        lic.chmod(0o644)
        files = sorted(x for x in stage.rglob("*") if x.is_file())
        size_kib = (sum(x.stat().st_size for x in files) + 1023) // 1024
        deb = stage / "DEBIAN"
        deb.mkdir()
        full_version = f"{version}-{revision}"
        (deb / "control").write_text(control_text(p, full_version, barch, maintainer, size_kib, depends))
        (deb / "md5sums").write_text("".join(
            f"{hashlib.md5(x.read_bytes()).hexdigest()}  {x.relative_to(stage)}\n" for x in files))
        for sname, spath in p.get("scripts", {}).items():
            shutil.copyfile(base / spath, deb / sname)
            (deb / sname).chmod(0o755)
        for x in [stage, *stage.rglob("*")]:
            os.utime(x, (epoch, epoch), follow_symlinks=False)
            if x.is_dir():
                x.chmod(0o755)
        out.mkdir(parents=True, exist_ok=True)
        target = out / f"{name}_{full_version}_{barch}.deb"
        r = subprocess.run(["dpkg-deb", "--root-owner-group", "-Zxz", "--build", str(stage), str(target)],
                           env=dict(os.environ, SOURCE_DATE_EPOCH=str(epoch)), capture_output=True, text=True)
        if r.returncode:
            raise MkdebError(f"{name}: dpkg-deb failed: {r.stderr.strip()}")
    sha = sha256_of(target)
    (out / (target.name + ".sha256")).write_text(f"{sha}  {target.name}\n")
    print(f"{target} ({target.stat().st_size // 1024} KiB) sha256 {sha}")
    return target


# ---- build mode -------------------------------------------------------------

def cmd_build(a: argparse.Namespace) -> None:
    toml_path = Path(a.deb_toml).resolve()
    p = tomllib.loads(toml_path.read_text())
    validate_pkg(p, str(toml_path), PKG_KEYS)
    maintainer = a.maintainer or p.get("maintainer") or os.environ.get("MKDEB_MAINTAINER")
    if not maintainer:
        raise MkdebError("maintainer required: deb.toml 'maintainer', --maintainer, or MKDEB_MAINTAINER")
    epoch = resolve_epoch(a.epoch, toml_path.parent)
    assemble(p, toml_path.parent, Path(a.binary).resolve(), a.version, a.revision, a.arch,
             maintainer, epoch, Path(a.out))


# ---- manifest mode ----------------------------------------------------------

def fetch(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    try:
        with urllib.request.urlopen(url, timeout=60) as r, tmp.open("wb") as f:
            shutil.copyfileobj(r, f)
    except OSError as e:
        raise MkdebError(f"cannot download {url}: {e}")
    tmp.replace(dest)


def expected_sha(src: dict) -> str:
    if src.get("sha256"):
        return src["sha256"].lower()
    if not src.get("sha256_url"):
        raise MkdebError(f"source {src.get('url')} has neither sha256 nor sha256_url")
    try:
        with urllib.request.urlopen(src["sha256_url"], timeout=30) as r:
            return r.read().decode().split()[0].lower()
    except (OSError, IndexError) as e:
        raise MkdebError(f"cannot read checksum {src['sha256_url']}: {e}")


def get_asset(p: dict, cache: Path) -> Path:
    src = p["source"]
    check_keys(src, SOURCE_KEYS, f"{p['name']} source")
    dest = cache / f"{p['name']}-{p['version']}-{Path(src['url']).name}"
    want = expected_sha(src)
    if dest.exists() and sha256_of(dest) != want:
        dest.unlink()
    if not dest.exists():
        print(f"  download {src['url']}")
        fetch(src["url"], dest)
    got = sha256_of(dest)
    if got != want:
        dest.unlink()
        raise MkdebError(f"{p['name']}: sha256 mismatch for {src['url']}: expected {want}, got {got}")
    if src.get("extract"):
        with tarfile.open(dest) as t:
            member = t.getmember(src["extract"])
            data = t.extractfile(member).read()
        inner = cache / f"{p['name']}-{p['version']}-{Path(src['extract']).name}"
        inner.write_bytes(data)
        return inner
    return dest


def load_manifest(path: Path) -> dict:
    m = tomllib.loads(path.read_text())
    check_keys(m, {"source_date_epoch", "maintainer", "package"}, str(path))
    for p in m.get("package", []):
        validate_pkg(p, f"{path}: package {p.get('name', '?')}", MANIFEST_PKG_KEYS)
        for k in ("version", "revision", "arch", "source"):
            if k not in p:
                raise MkdebError(f"{path}: package {p['name']} missing '{k}'")
    return m


def cmd_manifest(a: argparse.Namespace) -> None:
    mpath = Path(a.manifest).resolve()
    m = load_manifest(mpath)
    maintainer = a.maintainer or m.get("maintainer") or os.environ.get("MKDEB_MAINTAINER")
    if not maintainer:
        raise MkdebError("maintainer required in the manifest, --maintainer, or MKDEB_MAINTAINER")
    pkgs = [p for p in m["package"] if not a.only or p["name"] == a.only]
    if not pkgs:
        raise MkdebError(f"no such package: {a.only}")
    epoch = resolve_epoch(a.epoch, mpath.parent, m.get("source_date_epoch"))
    cache = Path(a.cache) if a.cache else mpath.parent / "cache"
    for p in pkgs:
        print(f"{p['name']} {p['version']}-{p['revision']}")
        binary = get_asset(p, cache)
        assemble(p, mpath.parent, binary, p["version"], p["revision"], p["arch"], maintainer, epoch, Path(a.out))


def cmd_list_tests(a: argparse.Namespace) -> None:
    m = load_manifest(Path(a.manifest).resolve())
    for p in m["package"]:
        print(f"{p['name']}|{p['version']}-{p['revision']}|{p.get('smoke', '')}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="mkdeb")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="package a local binary using a deb.toml")
    b.add_argument("--deb-toml", required=True)
    b.add_argument("--binary", required=True)
    b.add_argument("--version", required=True)
    b.add_argument("--revision", default="1")
    b.add_argument("--arch")
    b.add_argument("--maintainer")
    b.add_argument("--epoch", type=int)
    b.add_argument("--out", default="out")
    b.set_defaults(fn=cmd_build)
    m = sub.add_parser("manifest", help="build packages from a pinned manifest of release assets")
    m.add_argument("--manifest", required=True)
    m.add_argument("--only")
    m.add_argument("--maintainer")
    m.add_argument("--epoch", type=int)
    m.add_argument("--out", default="out")
    m.add_argument("--cache")
    m.set_defaults(fn=cmd_manifest)
    t = sub.add_parser("list-tests", help="print name|version|smoke lines for the install test")
    t.add_argument("--manifest", required=True)
    t.set_defaults(fn=cmd_list_tests)
    a = ap.parse_args(argv)
    try:
        a.fn(a)
    except MkdebError as e:
        print(f"mkdeb: error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
