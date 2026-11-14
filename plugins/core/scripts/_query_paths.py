"""Project containment for independently resolved query inputs."""

from pathlib import Path

from workframe_door import _unc_strip


def project_relative(path, project):
    """Normalize both identities, retaining filename case in navigation output.

    Windows Path comparisons ignore case; resolving both sides expands 8.3
    aliases. Strip extended-length prefixes consistently before resolution.
    """
    return Path(_unc_strip(path)).resolve().relative_to(Path(_unc_strip(project)).resolve())
