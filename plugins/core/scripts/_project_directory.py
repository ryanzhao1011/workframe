"""Deterministic directory facts shared by setup and read-only queries."""

import os
from pathlib import Path


# Setup's established --require-empty exemptions remain unchanged.
NEAR_EMPTY_ALLOWLIST = {
    ".git", ".gitignore", ".gitkeep", ".gitattributes",
    "readme.md", "readme", "readme.txt",
    ".ds_store", "thumbs.db", "desktop.ini",
    "params.json", "tree.json",
}
QUERY_NEAR_EMPTY_ALLOWLIST = {".git", "readme.md", "readme", "readme.txt"}
PROJECT_MANIFESTS = {
    "package.json", "pyproject.toml", "Cargo.toml", "go.mod", "pom.xml",
}
HOME_CONTAINER_NAMES = (
    "Desktop", "Downloads", "Documents", "桌面", "下载", "文档",
    "デスクトップ", "ダウンロード", "ドキュメント",
)


def substantive_entries(project_dir, names=None):
    """Return setup's substantive entry names; enumeration errors propagate."""
    if names is None:
        names = [entry.name for entry in Path(project_dir).iterdir()]
    return sorted(name for name in names if name.lower() not in NEAR_EMPTY_ALLOWLIST)


def _system_container(project_dir):
    home = Path.home().resolve()
    containers = {home, *(home / name for name in HOME_CONTAINER_NAMES)}
    # Windows known folders can be redirected, for example into OneDrive.
    if os.name == "nt":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as key:
                for name in ("Desktop", "Personal", "{374DE290-123F-4565-9164-39C4925E467B}"):
                    try:
                        value, _ = winreg.QueryValueEx(key, name)
                        containers.add(Path(os.path.expandvars(value)).resolve())
                    except (OSError, TypeError, ValueError):
                        continue
        except OSError:
            pass
    return (project_dir.parent == project_dir or project_dir in containers
            or project_dir.parent.as_posix() in ("/home", "/Users"))


def classify_directory(project_dir, *, for_setup=False):
    """Query: container, near_empty, project, plain; unreadable: unknown.

    Setup preserves its existing priority: connected, root repository/manifest,
    system container, then its broader near-empty exemptions. A plain/unknown
    setup result still requires the skill's existing contextual judgement.
    """
    try:
        project_dir = Path(project_dir).resolve()
        names = [entry.name for entry in project_dir.iterdir()]
        project = (".git" in names or bool(PROJECT_MANIFESTS.intersection(names))
                   or any(name.lower().endswith(".sln") for name in names))
        if for_setup and ".workframe-config.json" in names:
            return "connected"
        if for_setup and project:
            return "project"
        if _system_container(project_dir):
            return "container"
        near_empty = (not substantive_entries(project_dir, names) if for_setup
                      else all(name.lower() in QUERY_NEAR_EMPTY_ALLOWLIST for name in names))
        if near_empty:
            return "near_empty"
        return "project" if project else "plain"
    except OSError:
        return "unknown"
