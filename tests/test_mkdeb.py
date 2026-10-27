import hashlib
import io
import os
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import mkdeb  # noqa: E402

HOST_ELF = Path("/bin/true")  # real dynamic binary linking only libc, on any dev host or CI runner
EPOCH = 1761436800


def elf_header(machine: int, data: int = 1) -> bytes:
    h = bytearray(64)
    h[:4] = b"\x7fELF"
    h[4], h[5] = 2, data
    h[18:20] = struct.pack("<H", machine)
    return bytes(h)


READELF_D = """
 0x0000000000000001 (NEEDED)             Shared library: [libgcc_s.so.1]
 0x0000000000000001 (NEEDED)             Shared library: [libm.so.6]
 0x0000000000000001 (NEEDED)             Shared library: [libc.so.6]
 0x0000000000000001 (NEEDED)             Shared library: [ld-linux-x86-64.so.2]
"""
READELF_V = """
  000000: Version: 1  File: libc.so.6  Cnt: 3
  0x0010:   Name: GLIBC_2.2.5  Flags: none  Version: 5
  0x0020:   Name: GLIBC_2.34  Flags: none  Version: 4
  0x0030:   Name: GLIBC_2.9  Flags: none  Version: 3
"""

TOML = """
name = "demo"
description = "Demo tool"
long_description = "Line one.\\n\\nLine three."
license = "MPL-2.0"
homepage = "https://example.invalid/demo"
section = "net"
smoke = "demo --version"

[[files]]
src = "binary"
dst = "usr/bin/demo"
mode = "0755"

[[files]]
src = "demo.service"
dst = "usr/lib/systemd/system/demo.service"
mode = "0644"

[scripts]
postinst = "postinst"
"""


class ElfTests(unittest.TestCase):
    def write(self, data: bytes) -> Path:
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        p = Path(d) / "bin"
        p.write_bytes(data)
        return p

    def test_arch_amd64_arm64(self):
        self.assertEqual(mkdeb.elf_arch(self.write(elf_header(0x3E))), "amd64")
        self.assertEqual(mkdeb.elf_arch(self.write(elf_header(0xB7))), "arm64")

    def test_arch_rejects_other(self):
        with self.assertRaises(mkdeb.MkdebError):
            mkdeb.elf_arch(self.write(elf_header(0x28)))
        with self.assertRaises(mkdeb.MkdebError):
            mkdeb.elf_arch(self.write(b"not an elf at all, just text.."))
        with self.assertRaises(mkdeb.MkdebError):
            mkdeb.elf_arch(self.write(elf_header(0x3E, data=2)))

    def test_parse_needed_and_floor(self):
        self.assertEqual(mkdeb.parse_needed(READELF_D),
                         ["libgcc_s.so.1", "libm.so.6", "libc.so.6", "ld-linux-x86-64.so.2"])
        self.assertEqual(mkdeb.parse_glibc_floor(READELF_V), (2, 34))
        self.assertIsNone(mkdeb.parse_glibc_floor("nothing"))

    def test_merge_depends_declared_wins(self):
        out = mkdeb.merge_depends(["libc6 (>= 2.34)", "libgcc-s1"], ["libc6 (>= 2.36)", "iproute2"])
        self.assertEqual(out, ["libc6 (>= 2.36)", "libgcc-s1", "iproute2"])

    def test_compute_depends_with_fake_readelf(self):
        real = mkdeb.readelf
        mkdeb.readelf = lambda args, path: READELF_D if args == ["-d"] else READELF_V
        try:
            self.assertEqual(mkdeb.compute_depends(Path("x"), {}), ["libc6 (>= 2.34)", "libgcc-s1"])
        finally:
            mkdeb.readelf = real

    def test_unknown_soname_is_error_unless_mapped(self):
        text = READELF_D + " 0x0000000000000001 (NEEDED)             Shared library: [libfoo.so.3]\n"
        real = mkdeb.readelf
        mkdeb.readelf = lambda args, path: text if args == ["-d"] else READELF_V
        try:
            with self.assertRaises(mkdeb.MkdebError):
                mkdeb.compute_depends(Path("x"), {})
            self.assertIn("libfoo3", mkdeb.compute_depends(Path("x"), {"libfoo.so.3": "libfoo3"}))
        finally:
            mkdeb.readelf = real

    def test_static_binary_has_no_libc_depends(self):
        real = mkdeb.readelf
        mkdeb.readelf = lambda args, path: "There is no dynamic section in this file.\n"
        try:
            self.assertEqual(mkdeb.compute_depends(Path("x"), {}), [])
        finally:
            mkdeb.readelf = real


class VersionTests(unittest.TestCase):
    def test_debian_version(self):
        for up, want in (("v1.2.3", "1.2.3"), ("1.2.3", "1.2.3"), ("v0.14.3-rc1", "0.14.3~rc1"),
                         ("0.14.3-beta.2", "0.14.3~beta.2"), ("v2.0", "2.0"), ("1.2.3~rc1", "1.2.3~rc1")):
            self.assertEqual(mkdeb.debian_version(up), want, up)

    @unittest.skipUnless(shutil.which("dpkg"), "needs dpkg")
    def test_prerelease_sorts_before_release(self):
        pre, rel = mkdeb.debian_version("v0.14.3-rc1") + "-1", mkdeb.debian_version("v0.14.3") + "-1"
        self.assertEqual(subprocess.run(["dpkg", "--compare-versions", pre, "lt", rel]).returncode, 0)


class PolicyAndValidation(unittest.TestCase):
    def script(self, body: str) -> Path:
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        p = Path(d) / "s"
        p.write_text("#!/bin/sh\n" + body)
        return p

    def test_script_policy(self):
        for bad in ("systemctl start foo\n", "systemctl enable --now foo\n", "systemctl --no-block restart x\n",
                    "addgroup --system x\n", "adduser x\n", "deb-systemd-invoke start x\n"):
            with self.assertRaises(mkdeb.MkdebError, msg=bad):
                mkdeb.scan_script(self.script(bad))
        mkdeb.scan_script(self.script("# systemctl start in a comment\ngroupadd --system x\nsystemctl daemon-reload\n"))

    def test_unknown_key_and_missing_required(self):
        base = {"name": "a", "description": "d", "license": "MIT", "files": []}
        with self.assertRaises(mkdeb.MkdebError):
            mkdeb.validate_pkg(base, "t", mkdeb.PKG_KEYS)  # empty files
        ok = dict(base, files=[{"src": "binary", "dst": "usr/bin/a", "mode": "0755"}])
        mkdeb.validate_pkg(ok, "t", mkdeb.PKG_KEYS)
        with self.assertRaises(mkdeb.MkdebError):
            mkdeb.validate_pkg(dict(ok, descripton="typo"), "t", mkdeb.PKG_KEYS)
        with self.assertRaises(mkdeb.MkdebError):
            mkdeb.validate_pkg(dict(ok, files=[{"src": "binary", "dst": "/abs", "mode": "0755"}]), "t", mkdeb.PKG_KEYS)
        with self.assertRaises(mkdeb.MkdebError):
            mkdeb.validate_pkg(dict(ok, scripts={"postint": "x"}), "t", mkdeb.PKG_KEYS)


@unittest.skipUnless(shutil.which("dpkg-deb") and shutil.which("readelf") and HOST_ELF.exists(),
                     "needs dpkg-deb, readelf and /bin/true")
class BuildTests(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.d)
        (self.d / "deb.toml").write_text(TOML)
        (self.d / "demo.service").write_text("[Service]\nExecStart=/usr/bin/demo\n")
        (self.d / "postinst").write_text("#!/bin/sh\nset -e\nexit 0\n")
        shutil.copyfile(HOST_ELF, self.d / "demo-bin")

    def build(self, out: str, **kw) -> Path:
        argv = ["build", "--deb-toml", str(self.d / "deb.toml"), "--binary", str(self.d / "demo-bin"),
                "--version", kw.get("version", "v1.2.3"), "--maintainer", "T <t@example.invalid>",
                "--epoch", str(EPOCH), "--out", str(self.d / out)]
        if "arch" in kw:
            argv += ["--arch", kw["arch"]]
        mkdeb.main(argv)
        return next((self.d / out).glob("*.deb"))

    def field(self, deb: Path, f: str) -> str:
        return subprocess.run(["dpkg-deb", "-f", str(deb), f], capture_output=True, text=True).stdout.strip()

    def test_build_fields_and_listing(self):
        deb = self.build("o1")
        host_arch = mkdeb.elf_arch(self.d / "demo-bin")
        self.assertEqual(deb.name, f"demo_1.2.3-1_{host_arch}.deb")
        self.assertEqual(self.field(deb, "Package"), "demo")
        self.assertEqual(self.field(deb, "Version"), "1.2.3-1")
        self.assertEqual(self.field(deb, "Architecture"), host_arch)
        self.assertEqual(self.field(deb, "Maintainer"), "T <t@example.invalid>")
        self.assertIn("libc6 (>= 2.", self.field(deb, "Depends"))
        listing = subprocess.run(["dpkg-deb", "-c", str(deb)], capture_output=True, text=True).stdout
        self.assertIn("root/root", listing)
        self.assertIn("./usr/bin/demo", listing)
        self.assertIn("./usr/lib/systemd/system/demo.service", listing)
        self.assertIn("./usr/share/doc/demo/copyright", listing)
        self.assertNotIn(str(os.getuid()) + "/", listing.split()[1] if os.getuid() else "")
        sha = (deb.parent / (deb.name + ".sha256")).read_text()
        self.assertEqual(sha.split()[0], hashlib.sha256(deb.read_bytes()).hexdigest())

    def test_reproducible(self):
        a = self.build("o1")
        b = self.build("o2")
        self.assertEqual(a.read_bytes(), b.read_bytes())

    def test_arch_mismatch_is_error(self):
        other = "arm64" if mkdeb.elf_arch(self.d / "demo-bin") == "amd64" else "amd64"
        with self.assertRaises(SystemExit):
            self.build("o3", arch=other)

    def test_script_policy_blocks_build(self):
        (self.d / "postinst").write_text("#!/bin/sh\nsystemctl enable --now demo\n")
        with self.assertRaises(SystemExit):
            self.build("o4")

    def test_missing_maintainer_is_error(self):
        os.environ.pop("MKDEB_MAINTAINER", None)
        argv = ["build", "--deb-toml", str(self.d / "deb.toml"), "--binary", str(self.d / "demo-bin"),
                "--version", "1", "--epoch", str(EPOCH), "--out", str(self.d / "o5")]
        with self.assertRaises(SystemExit):
            mkdeb.main(argv)

    def test_manifest_mode_with_checksum_and_tar_extract(self):
        assets = self.d / "assets"
        assets.mkdir()
        raw = assets / "demo-linux"
        shutil.copyfile(HOST_ELF, raw)
        (assets / "demo-linux.sha256").write_text(f"{mkdeb.sha256_of(raw)}  demo-linux\n")
        tgz = assets / "demo.tar.gz"
        with tarfile.open(tgz, "w:gz") as t:
            t.add(HOST_ELF, arcname="demo-2/demo")
        arch = mkdeb.elf_arch(raw)
        man = self.d / "manifest.toml"
        man.write_text(f"""
source_date_epoch = {EPOCH}
maintainer = "T <t@example.invalid>"

[[package]]
name = "rawpkg"
version = "1.0"
revision = "1"
arch = "{arch}"
license = "MIT"
description = "raw"
smoke = "rawpkg --version"
[package.source]
url = "{raw.as_uri()}"
sha256_url = "{(assets / 'demo-linux.sha256').as_uri()}"
[[package.files]]
src = "asset"
dst = "usr/bin/rawpkg"
mode = "0755"

[[package]]
name = "tarpkg"
version = "2.0"
revision = "1"
arch = "{arch}"
license = "MIT"
description = "tar"
[package.source]
url = "{tgz.as_uri()}"
sha256 = "{mkdeb.sha256_of(tgz)}"
extract = "demo-2/demo"
[[package.files]]
src = "asset"
dst = "usr/bin/tarpkg"
mode = "0755"
""")
        mkdeb.main(["manifest", "--manifest", str(man), "--out", str(self.d / "mo"), "--cache", str(self.d / "cache")])
        debs = sorted(p.name for p in (self.d / "mo").glob("*.deb"))
        self.assertEqual(debs, [f"rawpkg_1.0-1_{arch}.deb", f"tarpkg_2.0-1_{arch}.deb"])
        # tamper: wrong checksum must abort and delete the cached file
        man.write_text(man.read_text().replace(mkdeb.sha256_of(tgz), "0" * 64))
        with self.assertRaises(SystemExit):
            mkdeb.main(["manifest", "--manifest", str(man), "--only", "tarpkg", "--out", str(self.d / "mo2"),
                        "--cache", str(self.d / "cache")])

    def test_list_tests(self):
        man = self.d / "m.toml"
        man.write_text("""
[[package]]
name = "x"
version = "1"
revision = "2"
arch = "amd64"
license = "MIT"
description = "d"
smoke = "x --version"
[package.source]
url = "file:///dev/null"
sha256 = "00"
[[package.files]]
src = "binary"
dst = "usr/bin/x"
mode = "0755"
""")
        buf = io.StringIO()
        old, sys.stdout = sys.stdout, buf
        try:
            mkdeb.main(["list-tests", "--manifest", str(man)])
        finally:
            sys.stdout = old
        self.assertEqual(buf.getvalue().strip(), "x|1-2|x --version")


if __name__ == "__main__":
    unittest.main()
