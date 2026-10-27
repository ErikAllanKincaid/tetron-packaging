from libspec import Requirement


class CompositeAction(Requirement):
    """action.yml at the repository root is a composite GitHub action named
    build-deb. Inputs: binary, deb-toml, version, revision, arch, out, test,
    maintainer. Outputs: deb and sha256 paths. It runs mkdeb from the
    action's own checkout (github.action_path), so a consumer pinned to a
    commit SHA runs exactly that version of the tool."""


class ActionPinning(Requirement):
    """Documentation tells consumers to pin the action by full commit SHA,
    never by branch. The action pulls nothing from the network except
    through docker pull of debian:trixie for the optional install test."""


class ActionTestGate(Requirement):
    """With test enabled (the default) the action runs the install test and
    fails the job before any asset can be uploaded."""


class ConsumerExample(Requirement):
    """The README contains a complete copy-paste example: a deb.toml for a
    binary with a service, and a release.yml job that builds binaries, calls
    the action for each architecture, and uploads the .deb and .sha256."""
