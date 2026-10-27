# tetron-packaging

Shared `.deb` builder and GitHub Action for the tetron suite. One small tool, used by every component repo's release CI and by the TetronOS image builder.

- `mkdeb.py`: single file, Python standard library plus `dpkg-deb` and `readelf`. No pip packages, no fpm.
- `action.yml`: composite GitHub Action `build-deb`. Builds one package, then installs it in a clean `debian:trixie` container as a test gate before anything is uploaded.
- `scripts/test-install.sh`: the container install test, usable on its own.

Status: developed locally, not yet published. Spec in `spec/` (libspec), checked by `python3 reconcile.py`.

## What mkdeb does

- Reads the architecture from the binary (amd64 or arm64). A mismatching `--arch` is an error.
- Computes `Depends` from the binary: none for a static binary, `libc6 (>= X)` from the highest `GLIBC_` symbol for a dynamic one, `libgcc-s1` and `libstdc++6` for those sonames. An unknown shared library is an error until it is mapped in `[soname_map]`.
- Produces a byte-identical package for identical inputs: root ownership, timestamps clamped to an epoch, xz compression.
- Writes `name_version-revision_arch.deb` and a `.sha256` next to it.
- Refuses maintainer scripts that start or enable services, or use `addgroup` or `adduser`. Services are installed disabled. Create groups with `groupadd --system`. Never create keys or identities in a package script.

## deb.toml

One file per package, next to the code. Paths are relative to the file. Unknown keys are an error.

```toml
name = "tetron"
description = "P2P mesh VPN powered by iroh"
long_description = """
Paragraphs of text. A blank line becomes a Debian paragraph break."""
section = "net"
license = "MPL-2.0"
homepage = "https://github.com/ErikAllanKincaid/tetron"
maintainer = "Name <address>"          # or --maintainer, or MKDEB_MAINTAINER
depends = ["iproute2"]                  # added to the computed Depends; a name given here wins
recommends = []
conflicts = []
smoke = "tetron --version"              # used by manifest tests
# {minor} and {next_minor} expand from the package version: 0.14.2 gives 0.14 and 0.15.
# Suite addons track the tetron core minor (tetron-tui 0.14.x targets tetron 0.14), so:
# recommends = ["tetron (>= {minor}), tetron (<< {next_minor})"]
soname_map = { "libfoo.so.3" = "libfoo3" }

[[files]]
src = "binary"                          # the binary passed to mkdeb (alias: asset)
dst = "usr/bin/tetron"
mode = "0755"

[[files]]
src = "packaging/tetron.service"
dst = "usr/lib/systemd/system/tetron.service"
mode = "0644"

[scripts]                               # preinst, postinst, prerm, postrm
postinst = "packaging/postinst"
```

## Use from a release workflow

Pin the action by full commit SHA, never by branch or tag.

```yaml
  build-deb:
    needs: build            # the job that uploads binaries as artifacts
    runs-on: ubuntu-latest
    permissions:
      contents: write
    strategy:
      matrix:
        include:
          - { target: x86_64-unknown-linux-musl,  arch: amd64, artifact: tetron-linux-x86_64-musl }
          - { target: aarch64-unknown-linux-musl, arch: arm64, artifact: tetron-linux-aarch64-musl }
    steps:
      - uses: actions/checkout@v4
      - uses: actions/download-artifact@v4
        with: { name: "${{ matrix.artifact }}", path: bin }
      - id: deb
        uses: ErikAllanKincaid/tetron-packaging@<FULL_COMMIT_SHA>
        with:
          binary: bin/${{ matrix.artifact }}
          deb-toml: packaging/deb.toml
          version: ${{ github.ref_name }}
          arch: ${{ matrix.arch }}
          smoke: tetron --version
      - uses: softprops/action-gh-release@v2
        with:
          files: |
            ${{ steps.deb.outputs.deb }}
            ${{ steps.deb.outputs.sha256 }}
```

The install test runs only for the runner's native architecture. A foreign architecture package is built and checksummed but the test prints a SKIP notice. Set `test: "false"` to turn the test off.

## Use from the command line

Build one package from a local binary:

    python3 mkdeb.py build --deb-toml packaging/deb.toml --binary target/release/tetron --version 0.14.3

Build every package in a manifest of pinned release assets (used by the TetronOS image builder). Each package in the manifest has the deb.toml keys plus `version`, `revision`, `arch` and `source` with a `url`, a `sha256` or `sha256_url`, and an optional `extract` for a file inside a `.tar.gz`:

    python3 mkdeb.py manifest --manifest packaging/manifest.toml --out out

Timestamps come from `--epoch`, then `SOURCE_DATE_EPOCH`, then the manifest `source_date_epoch`, then the last git commit time.

## Tests

    python3 -m unittest discover tests
    sh scripts/test-install.sh path/to/pkg.deb::"pkg --version"
    python3 reconcile.py

`scripts/test-install.sh` needs docker. It installs through apt so dependencies resolve, then checks the file list, md5sums, the smoke command, `systemd-analyze verify` for units, and that remove and purge leave nothing behind. A package that fails to install is reported as a failure, never as cleanly removed.

## License

Not chosen yet.
