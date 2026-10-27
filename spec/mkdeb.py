from libspec import Requirement


class NoRuntimeDependencies(Requirement):
    """mkdeb is a single Python file using only the standard library, plus
    dpkg-deb and readelf from the host. No pip packages, no fpm, no Ruby."""


class TwoModes(Requirement):
    """mkdeb has two subcommands. build packages a local binary using a
    deb.toml (CI use). manifest builds every package listed in a manifest
    file by downloading pinned release assets and verifying their sha256
    (image builder use). Both share one code path for assembling the
    package."""


class DebTomlFormat(Requirement):
    """A deb.toml describes exactly one package: name, description,
    long_description, section, priority, license, homepage, depends,
    smoke, files (src or the word binary, dst, mode), and scripts. Paths in
    it are relative to the file. Unknown keys are an error, so typos do not
    pass silently."""


class ArchFromElf(Requirement):
    """The package architecture is read from the binary ELF header
    (x86-64 gives amd64, aarch64 gives arm64). An explicit --arch that
    disagrees with the binary is an error. Unsupported machines are an
    error."""


class DependsFromBinary(Requirement):
    """Runtime Depends are computed from the binary. A static binary needs no
    libc dependency. A dynamic binary gets libc6 (>= X) where X is the
    highest GLIBC_ symbol version it requires, and known sonames map to
    Debian packages (libgcc_s.so.1 to libgcc-s1, libstdc++.so.6 to
    libstdc++6). An unknown NEEDED soname is an error unless deb.toml maps it
    in soname_map. Depends from deb.toml are added."""


class Deterministic(Requirement):
    """The same inputs produce a byte-identical .deb: files owned by root,
    mtimes clamped to SOURCE_DATE_EPOCH (or --epoch, or the committer time of
    the deb.toml directory's git HEAD), sorted file order, fixed compression
    (xz). Missing epoch is an error."""


class ChecksumsPublished(Requirement):
    """Every built .deb gets a sibling .sha256 file in sha256sum format, and
    mkdeb prints the path and size."""


class MaintainerScriptPolicy(Requirement):
    """Debian policy for suite packages: maintainer scripts never start
    services, never enable units, and never create keys or identities.
    Units are installed disabled under /usr/lib/systemd/system. System
    groups are created with groupadd --system, not addgroup. mkdeb scans
    scripts and refuses systemctl start, enable, or restart and refuses
    addgroup."""


class MetadataComplete(Requirement):
    """control carries Package, Version (upstream version plus Debian
    revision), Architecture, Maintainer, Installed-Size, Depends, Section,
    Priority, Homepage and a short and long Description. Maintainer comes
    from deb.toml, --maintainer or the environment, and is required. A
    copyright file naming the license is generated. md5sums is generated."""


class InstallTest(Requirement):
    """scripts/test-install.sh installs the built deb in a fresh
    debian:trixie container through apt so Depends resolve, checks the file
    list and md5sums, runs the smoke command and the unit file check, and
    verifies remove and purge leave nothing behind. The test fails closed:
    a package that did not install is never reported as cleanly removed."""


class VersionTemplates(Requirement):
    """Dependency strings in deb.toml may use the tokens {minor} and
    {next_minor}, replaced by the X.Y and X.(Y+1) of the package's own
    upstream version (0.14.2 gives 0.14 and 0.15). This lets an addon whose
    version tracks the tetron core minor (tetron-tui 0.14.x targets tetron
    0.14) declare tetron (>= {minor}), tetron (<< {next_minor}) without a
    hand edit at each minor bump. A leading v on the version is removed.
    Versions that are not X.Y.Z style make the tokens an error."""
