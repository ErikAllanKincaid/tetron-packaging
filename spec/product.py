from libspec import UserStory


class ComponentCiDeb(UserStory):
    """USER-STORY: COMPONENT-CI-DEB

    A maintainer of a tetron suite repo adds one small packaging/deb.toml and
    one workflow step, and every release publishes tested amd64 and arm64
    .deb files next to the raw binaries.

    Priority: high.
    Acceptance: a component release.yml uses the build-deb action, pinned by
    commit SHA, and the release shows name_version-1_arch.deb and .sha256
    assets that install cleanly in a clean debian:trixie container.
    """
    brief_title = "Publish tested .debs from release CI"
    priority = "high"


class CentralManifestBuild(UserStory):
    """USER-STORY: CENTRAL-MANIFEST-BUILD

    The TetronOS image builder wraps already-released raw binaries into .debs
    from a pinned manifest, using the same tool as the CI action.

    Priority: medium.
    Acceptance: mkdeb manifest mode reproduces the packages that
    tetron-os/packaging/build.py builds today.
    """
    brief_title = "Wrap released binaries from a pinned manifest"
    priority = "medium"
