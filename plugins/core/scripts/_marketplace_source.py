"""Marketplace identities shared by queries and opt-in installation guards.

Directory comparison is the door's existing extended-prefix/resolve/normcase
rule; Git identities retain the registered raw value, as project-status does.
These checks do not lock a registry against another process changing it later.
"""

import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import uuid

import _settings_io


class SourceError(ValueError):
    def __init__(self, code, message):
        super().__init__(code + ": " + message)
        self.code = code


def strip_extended(value):
    prefix = "\\\\?\\"
    value = str(value or "")
    return value[len(prefix):] if value.startswith(prefix) else value


def resolved_path(value):
    return str(Path(strip_extended(value)).resolve())


def norm_path(value):
    return os.path.normcase(resolved_path(value))


def claude_source(source):
    """The source-object conversion used by project-status.marketplace_source."""
    if not isinstance(source, dict):
        return None
    kind = source.get("source")
    if kind == "directory" and isinstance(source.get("path"), str):
        return {"kind": "directory", "source": source["path"]}
    if kind in ("git", "github"):
        raw = source.get("url") if kind == "git" else source.get("repo")
        if isinstance(raw, str):
            return {"kind": "git", "source": raw}
    return None


def codex_source(entry):
    if not isinstance(entry, dict):
        return None
    raw, kind = entry.get("source"), entry.get("source_type")
    if kind in ("local", "git") and isinstance(raw, str):
        return {"kind": "directory" if kind == "local" else "git", "source": raw}
    return None


def source_key(source):
    return (source["kind"], norm_path(source["source"]) if source["kind"] == "directory"
            else source["source"])


def claude_write_source(source):
    """Use one checked directory for Claude's write and preview representations."""
    if source.get("source") == "directory" and not Path(strip_extended(source["path"])).is_absolute():
        return dict(source, path=resolved_path(source["path"]))
    return source


def unique_pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate key: " + key)
        result[key] = value
    return result


def parse_expected(raw):
    """Accept the marketplace identity returned by project-status, not unknown."""
    try:
        value = json.loads(raw, object_pairs_hook=unique_pairs,
                           parse_constant=lambda value: (_ for _ in ()).throw(ValueError()))
        if not isinstance(value, dict) or value.get("kind") not in ("directory", "git"):
            raise ValueError("expected directory or git")
        if not isinstance(value.get("source"), str) or not value["source"].strip():
            raise ValueError("source must be a nonempty string")
        return {"kind": value["kind"], "source": value["source"]}
    except (ValueError, TypeError) as exc:
        raise SourceError("bad_usage", "--expect-source requires {kind: directory|git, source: nonempty string}") from exc


def require_same(actual, expected, label):
    if actual is None or not isinstance(actual.get("source"), str) or not actual["source"].strip():
        raise SourceError("source_unverified", label + " has no verifiable marketplace source; nothing was registered")
    try:
        matches = source_key(actual) == source_key(expected)
    except (OSError, ValueError, RuntimeError) as exc:
        raise SourceError("source_unverified", label + " cannot be compared: " + str(exc)) from exc
    if not matches:
        raise SourceError("source_mismatch", label + " differs from --expect-source; registration was not changed")


def _read_claude(path):
    try:
        return _settings_io.read_settings(path, object_pairs_hook=unique_pairs)
    except (OSError, ValueError, _settings_io.SettingsError) as exc:
        raise SourceError("source_unverified", "Cannot verify " + str(path) + ": " + str(exc)) from exc


def require_claude(expected, market, candidate, project):
    """Check the exact user registry written by write_subscription before writes.

    That writer uses ~/.claude, independently of CLAUDE_CONFIG_DIR. This opt-in
    guard follows its write target; it does not change the legacy target.
    """
    require_same(claude_source(candidate), expected, "Requested subscription")
    path = Path.home() / ".claude/plugins/known_marketplaces.json"
    registry = _read_claude(path)
    if market in registry:
        entry = registry[market]
        require_same(claude_source(entry.get("source")) if isinstance(entry, dict) else None,
                     expected, "Claude marketplace " + market)
    # A retained project declaration must not silently subscribe to another copy.
    settings = _read_claude(Path(project) / ".claude/settings.json")
    declarations = settings.get("extraKnownMarketplaces", {})
    if not isinstance(declarations, dict):
        raise SourceError("source_unverified", "Project extraKnownMarketplaces is not an object")
    if market in declarations:
        entry = declarations[market]
        actual = claude_source(entry.get("source")) if isinstance(entry, dict) else None
        if (actual is not None and actual["kind"] == "directory" and actual["source"].strip()
                and not Path(strip_extended(actual["source"])).is_absolute()):
            # Claude resolves an existing project declaration from the project,
            # rather than from the installer process's working directory.
            actual = dict(actual, source=str(Path(project) / actual["source"]))
        require_same(actual, expected, "Project marketplace " + market)


def require_codex(expected, market, candidate, home, *, require_present=False):
    require_same({"kind": expected["kind"], "source": candidate}, expected, "Requested marketplace")
    path = Path(home) / "config.toml"
    try:
        try:
            text = path.read_text(encoding="utf-8-sig")
        except FileNotFoundError:
            if require_present:
                raise SourceError("source_unverified", "Codex did not write an isolated marketplace registration")
            return
        try:
            import tomllib
        except ImportError as exc:
            raise SourceError("source_unverified", "Verifying existing Codex TOML requires Python 3.11 or later") from exc
        cfg = tomllib.loads(text)
        entries = cfg.get("marketplaces", {})
        if not isinstance(entries, dict):
            raise ValueError("marketplaces must be a table")
        if market in entries:
            require_same(codex_source(entries[market]), expected, "Codex marketplace " + market)
        elif require_present:
            raise SourceError("source_unverified", "Codex did not register the reported marketplace " + market)
    except (OSError, ValueError) as exc:
        if isinstance(exc, SourceError):
            raise
        raise SourceError("source_unverified", "Cannot verify " + str(path) + ": " + str(exc)) from exc


def probe_codex_market(exe, candidate, expected, home, run):
    """Resolve the CLI's actual market name without touching its real registry.

    A supplied --marketplace-name is not proof of the name in a local or remote
    manifest. Use Codex's own resolver in a private home, then compare the real
    registration under the returned name before the real add. Git sources may
    be fetched twice. This is a preflight, not a cross-process registry lock.
    """
    parent = (Path(home) / ".tmp").resolve()
    probe = parent / ("workframe-source-" + uuid.uuid4().hex)
    probe_home = probe / "home"

    def remove_readonly(function, path, _exc):
        os.chmod(path, stat.S_IWRITE)
        function(path)

    try:
        os.makedirs(str(probe_home), exist_ok=False)
        env = dict(os.environ, CODEX_HOME=str(probe_home))
        rc, out, err = run(exe, ["plugin", "marketplace", "add", candidate, "--json"], cwd=probe, env=env)
        if rc != 0:
            raise SourceError("source_unverified", "Isolated marketplace resolution failed; real registration was not changed: "
                              + (err or out)[:200])
        data = json.loads(out, object_pairs_hook=unique_pairs)
        name = data.get("marketplaceName") if isinstance(data, dict) else None
        if not isinstance(name, str) or not name.strip():
            raise SourceError("source_unverified", "Codex did not report the actual marketplace name")
        require_codex(expected, name, candidate, probe_home, require_present=True)
        require_codex(expected, name, candidate, home)
        return name
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        if isinstance(exc, SourceError):
            raise
        raise SourceError("source_unverified", "Cannot resolve the actual marketplace; real registration was not changed: "
                          + str(exc)) from exc
    finally:
        # Only this call's newly created directory is recursively removed.
        try:
            if probe.resolve().parent != parent:
                raise SourceError("source_unverified", "Isolated marketplace directory left in place: " + str(probe))
            if probe.exists():
                shutil.rmtree(str(probe), onerror=remove_readonly)
        except OSError as exc:
            raise SourceError("source_unverified", "Cannot clean isolated marketplace directory " + str(probe)) from exc
