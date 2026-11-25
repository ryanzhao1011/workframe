"""The measured 0.167.2 read/config transport and its file-effect boundary.

No login, plugin refresh, thread or turn operation is used. Registration is
read from the frozen server's user layer and reconciled with the loaded root;
project declarations alone are not installation or source evidence.
"""

import copy
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import time

from _harness import is_dir_link
from workframe_door import _norm_path, _unc_strip

VERSION = "0.167.2"
LAUNCH_OBSERVATION_VERSION = "0.169.0"
CONTROL_DIRS = ("tmp", ".tmp", ".sandbox")
BOOTSTRAP_FILES = ("installation_id", "skills/.system/.codex-system-skills.marker")
STATE_DATABASES = ("state_5.sqlite", "goals_1.sqlite", "memories_1.sqlite", "queue_1.sqlite")
LOG_DATABASE = "logs_2.sqlite"
PLUGIN_SYNC_LOCK = ".tmp/plugins.sync.lock"
REMOTE_PLUGIN_DIRS = ("plugins/cache/openai-curated-remote", "plugins/.remote-plugin-install-staging")


def tree_snapshot(root, deadline=None):
    """Bytes, directory entries and links, without traversing linked trees."""
    root, rows = Path(root), {}
    def walk(directory):
        for path in sorted(directory.iterdir()):
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("Side-effect snapshot deadline expired")
            name = path.relative_to(root).as_posix()
            if path.is_symlink() or is_dir_link(path):
                rows[name] = ("link", os.readlink(path))
            elif path.is_dir():
                rows[name + "/"] = ("directory",)
                walk(path)
            else:
                sha = hashlib.sha256()
                with path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        if deadline is not None and time.monotonic() >= deadline:
                            raise TimeoutError("Side-effect snapshot deadline expired")
                        sha.update(chunk)
                rows[name] = ("file", sha.hexdigest())
    walk(root)
    return rows


def trust_input_roots(home):
    """The CODEX_HOME entries a trust observation reads, relative to the home.

    The user config (trust, enablement, registrations), the hook trust store and
    every marketplace's installed `core` plugin under `plugins/cache/`. The rest
    of a home (sessions, databases, caches, logs, other plugins) is written by
    whichever Codex process is running, so a before/after comparison there
    cannot attribute a change to one query; the service's own writes there are
    bounded by the per-version isolated measurement
    (tools/probe_codex_trust_runtime.py), not per query. A plugin root the
    service loads from anywhere else (a local marketplace directory) is not
    among these roots.
    """
    home = Path(home)
    roots = ["config.toml", ".hook-trust"]
    cache = home / "plugins/cache"
    if cache.is_dir():
        for market in sorted(cache.iterdir()):
            core = market / "core"
            if core.exists() or core.is_symlink():
                roots.append(f"plugins/cache/{market.name}/core")
    return roots


def _first_link(home, name):
    """The shortest prefix of `name` (relative to the home) that is a link, if any."""
    parts = PurePosixPath(name).parts
    for count in range(1, len(parts) + 1):
        prefix = "/".join(parts[:count])
        path = Path(home) / prefix
        if path.is_symlink() or is_dir_link(path):
            return prefix
    return None


def _file_digest(path, deadline):
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("Side-effect snapshot deadline expired")
            sha.update(chunk)
    return sha.hexdigest()


def _follow(path, name, rows, deadline, seen):
    """Rows for one trust input, entering links: a link keeps its own row (so
    retargeting it is a change) and its target is compared by content too."""
    if deadline is not None and time.monotonic() >= deadline:
        raise TimeoutError("Side-effect snapshot deadline expired")
    linked = path.is_symlink() or is_dir_link(path)
    if linked and not path.exists():
        rows[name] = ("link", os.readlink(path))
        return
    if path.is_dir():
        if linked:
            rows[name] = ("link", os.readlink(path))
        real = os.path.realpath(path)
        rows[name + "/"] = ("directory",)
        if real in seen:
            return
        for child in sorted(path.iterdir()):
            _follow(child, f"{name}/{child.name}", rows, deadline, seen | {real})
    elif path.exists():
        digest = _file_digest(path, deadline)
        rows[name] = ("link", os.readlink(path), digest) if linked else ("file", digest)


def trust_input_snapshot(home, deadline=None):
    """Rows for the trust inputs only, named from the home like `tree_snapshot`.

    Rows keep the whole-home naming (`config.toml`, `.hook-trust/`, ...) so
    `side_effect_violations` applies its allowance unchanged; an absent root
    has no row, so its creation shows as a change. Unlike `tree_snapshot`,
    links on the way to or inside a trust input are followed: each link keeps
    a row and the bytes behind it are compared as well.
    """
    home, rows = Path(home), {}
    for prefix in ("plugins", "plugins/cache"):
        if (home / prefix).is_symlink() or is_dir_link(home / prefix):
            rows[prefix] = ("link", os.readlink(home / prefix))
    for name in trust_input_roots(home):
        parent = PurePosixPath(name).parent
        if parent.parent.as_posix() == "plugins/cache":
            market = home / parent.as_posix()
            if market.is_symlink() or is_dir_link(market):
                rows[parent.as_posix()] = ("link", os.readlink(market))
        _follow(home / name, name, rows, deadline, frozenset())
    return rows


def presence_rows(home, names):
    """Entry kinds for named home paths, without reading content (cold-home gate input)."""
    rows = {}
    for name in names:
        path = Path(home) / name
        linked = _first_link(home, name)
        if linked:
            # A link anywhere on the way (the entry or a parent directory) is not
            # an initialised file, as the whole-home snapshot never traversed links.
            rows[name] = ("link", os.readlink(Path(home) / linked))
        elif path.is_dir():
            rows[name] = ("directory",)
        elif path.is_file():
            rows[name] = ("file", "present")
    return rows


def config_bytes(home):
    """Read the actual user-config target, including a file symlink's bytes."""
    try:
        return (Path(home) / "config.toml").read_bytes()
    except FileNotFoundError:
        return b""


def hook_trust_filename(config_file, plugin_id):
    """0.167.2 legacy_trust_path: native config-path bytes, then plugin ID.

    Each UTF-8 byte string has a u64 little-endian length prefix. The caller
    supplies the observed native user-layer path, without resolving the file
    link, substituting an 8.3 alias or retaining a Windows extended prefix.
    """
    identity = hashlib.sha256()
    for part in (str(config_file).encode("utf-8"), plugin_id.encode("utf-8")):
        identity.update(len(part).to_bytes(8, "little"))
        identity.update(part)
    return identity.hexdigest()


def trust_storage_status(target):
    """Read exact baseline bytes; never follow a storage-file/directory link."""
    path, content_hash = target
    if any(part.is_symlink() or is_dir_link(part) for part in (path.parent, path)):
        return "link"
    if not path.is_file():
        return "missing"
    return "verified" if path.read_bytes() == content_hash.encode("utf-8") else "content_mismatch"


def side_effect_violations(before, after, version=VERSION, allow_config=False, trust_storage=None):
    """Return every changed entry outside the measured allowance.

    A shared-memory exemption cannot exempt its database or WAL. Marketplace
    clones under .tmp are durable sources, not disposable git-* directories.
    Link changes are never a log/temp-file exemption. A recovery may add or
    update only its exact plugin/config-bound baseline to exact content bytes.
    Deletions and replacements of non-file entries remain outside the allowance.
    """
    storage_name, content_hash = trust_storage if trust_storage is not None else ("", "")
    storage_row = ("file", hashlib.sha256(content_hash.encode("utf-8")).hexdigest())
    storage_allowed = (bool(re.fullmatch(r"\.hook-trust/[0-9a-f]{64}", storage_name)) and
                     bool(re.fullmatch(r"sha256:[0-9a-f]{64}", content_hash)) and
                     (storage_name not in before or before[storage_name][0] == "file") and
                     after.get(storage_name) == storage_row and
                     before.get(".hook-trust/") in (None, ("directory",)) and
                     after.get(".hook-trust/") == ("directory",))
    bad = []
    for name in sorted(set(before) | set(after)):
        if before.get(name) == after.get(name):
            continue
        old, new = before.get(name), after.get(name)
        if version != VERSION or any(row and row[0] == "link" for row in (old, new)):
            bad.append(name)
            continue
        path = PurePosixPath(name)
        allowed = (path.parts[0] == "log" or
                   bool(re.fullmatch(r"logs_[^/]+\.sqlite(?:-wal|-shm)?", name)) or
                   name in ("tmp/", "tmp/arg0-windows/", "tmp/arg0/", ".tmp/", ".sandbox/") or
                   bool(re.match(r"tmp/arg0(?:-windows)?/codex-arg0[^/]+(?:/|$)", name)) or
                   bool(re.match(r"\.tmp/git-[^/]+(?:/|$)", name)) or
                   bool(re.fullmatch(r"\.sandbox/sandbox\.[^/]+\.log", name)))
        if name.endswith(".sqlite-shm"):
            database = name[:-4]
            allowed = (allowed or
                       before.get(database, (None,))[0] == "file" and
                       before.get(database) == after.get(database) and
                       before.get(database + "-wal") == after.get(database + "-wal"))
        if allow_config and name == "config.toml":
            allowed = True
        if storage_allowed and name in (storage_name, ".hook-trust/"):
            allowed = True
        if not allowed:
            bad.append(name)
    return bad


def trust_hash_fields(current_hash):
    """0.167.2 stores the declaration/content pair in two separate fields.

    PluginHookTrust.matches_hash compares trusted_hash to declaration_hash,
    and trusted_content_hash to the plugin-content fingerprint. Keep the RPC
    value opaque outside this exact runtime adapter.
    """
    if not isinstance(current_hash, str) or current_hash.count("/") != 1:
        return None
    declaration, content = current_hash.split("/")
    if not declaration or not re.fullmatch(r"sha256:[0-9a-f]{64}", content):
        return None
    return {"trusted_hash": declaration, "trusted_content_hash": content}


def trust_config_readback(config, attempted):
    """Require both persisted fields for every attempted complete key."""
    try:
        states = (config.get("hooks") or {}).get("state") or {}
        for key, current_hash in attempted.items():
            fields = trust_hash_fields(current_hash)
            observed = states.get(key, {})
            if fields is None or any(observed.get(field) != value for field, value in fields.items()):
                return False
        return bool(attempted)
    except (TypeError, AttributeError):
        return False


def _same_toml_value(left, right):
    """Preserve TOML types recursively: Python equates True, 1 and 1.0.

    The manifest's flat hash-tuple comparison cannot compare entire TOML
    tables, arrays and nested values, so it is not reused for this audit.
    """
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_same_toml_value(value, right[key]) for key, value in left.items())
    if isinstance(left, list):
        return len(left) == len(right) and all(_same_toml_value(a, b) for a, b in zip(left, right))
    return left == right


def trust_only_config_change(before, after, attempted):
    """Permit only exact declaration/content edits of attempted keys.

    Failed or partial batches may leave either field unchanged; readback must
    independently confirm both fields before a recovery can be certified.
    """
    import tomllib
    try:
        old, new = (tomllib.loads(raw.decode("utf-8-sig")) for raw in (before, after))
        expected = copy.deepcopy(old)
        for key, current_hash in attempted.items():
            fields = trust_hash_fields(current_hash)
            if fields is None:
                return False
            observed = ((new.get("hooks") or {}).get("state") or {}).get(key, {})
            previous = ((old.get("hooks") or {}).get("state") or {}).get(key, {})
            for field, value in fields.items():
                if observed.get(field) == previous.get(field):
                    continue
                if observed.get(field) != value:
                    return False
                expected.setdefault("hooks", {}).setdefault("state", {}).setdefault(key, {})[field] = value
        return _same_toml_value(expected, new)
    except (UnicodeError, ValueError, TypeError, AttributeError):
        return False


def config_layer_paths(context, env):
    """User, ancestor-project and system configuration files, in the order read."""
    home, cwd = Path(context["codex_home"]), Path(context["cwd"])
    paths = [home / "config.toml"] + [parent / ".codex/config.toml" for parent in (cwd, *cwd.parents)]
    paths.append(Path(env.get("PROGRAMDATA", "C:/ProgramData")) / "OpenAI/Codex/config.toml"
                 if os.name == "nt" else Path("/etc/codex/config.toml"))
    return paths


def runtime_paths_known(context, env, overrides):
    """Refuse state/log redirects before even the version probe.

    Custom database/log locations have no measured allowance. Check existing
    ancestor, user and system layers plus profiles and ordered CLI overrides.
    This intentionally rejects unsupported layouts rather than guessing them.
    """
    import tomllib
    home, cwd = Path(context["codex_home"]), Path(context["cwd"])
    defaults = {"sqlite_home": home, "log_dir": home / "log"}
    def known(cfg):
        for key, default in defaults.items():
            value = cfg.get(key)
            if value is not None and (not isinstance(value, str) or _norm_path(_unc_strip(value)) != _norm_path(default)):
                return False
        profiles = cfg.get("profiles") or {}
        return all(known(profile) for profile in profiles.values()) if profiles else True
    try:
        sqlite = env.get("CODEX_SQLITE_HOME")
        if sqlite and _norm_path(sqlite) != _norm_path(home):
            return False
        for path in config_layer_paths(context, env):
            if path.is_file() and not known(tomllib.loads(path.read_text(encoding="utf-8-sig"))):
                return False
        for override in overrides:
            key, value = override.split("=", 1)
            try:
                cfg = tomllib.loads(override)
            except ValueError:
                cfg = {key.strip().strip('"'): value.strip().strip('"')}
            if not known(cfg):
                return False
        return True
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def _launch_path(value, base=None):
    if not isinstance(value, (str, Path)) or not str(value) or "\x00" in str(value):
        raise ValueError("Invalid Codex runtime path")
    raw = str(value)
    # The normal local-drive prefix is removable. Preserve the UNC server/share.
    path = Path("\\\\" + raw[8:] if raw.lower().startswith("\\\\?\\unc\\") else _unc_strip(raw))
    if not path.is_absolute():
        if base is None or path.anchor:
            raise ValueError("Codex runtime path has no absolute base")
        path = base / path
    return path


def _real_launch_path(path, *, directory):
    """Resolve existing prefixes strictly. A missing suffix is safe to create."""
    path = _launch_path(path)
    try:
        resolved = path.resolve(strict=True)
    except FileNotFoundError:
        if path.parent == path or os.path.lexists(path):
            raise
        return _real_launch_path(path.parent, directory=True) / path.name
    mode = resolved.stat().st_mode
    if not (stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode)):
        raise ValueError("Codex runtime path has an unexpected entry type")
    return _launch_path(resolved)


def _configured_launch_dirs(config, base=None):
    if not isinstance(config, dict):
        raise ValueError("Unexpected Codex configuration shape")
    return {(name, _launch_path(config[name], base)) for name in ("log_dir", "sqlite_home")
            if config.get(name) is not None}


def _candidate_launch_dirs(context, env, overrides):
    """Check local path candidates before starting any native process.

    Files resolve relative values against their layer; session overrides and
    CODEX_SQLITE_HOME use cwd. Candidate layers are checked conservatively
    before Codex can report which ones actually took effect.
    """
    import tomllib

    home, cwd = Path(context["codex_home"]), Path(context["cwd"])
    layer_paths = config_layer_paths(context, env)
    system = layer_paths[-1].parent
    paths = [*layer_paths, cwd / "config.toml", system / "requirements.toml"]
    if os.name == "nt":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Policies\OpenAI\Codex", 0,
                                winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
                required, _ = winreg.QueryValueEx(key, "RequirementsPath")
            paths.append(_launch_path(required))
        except FileNotFoundError:
            pass
    if context["profile"]:
        profile = context["profile"]
        if not re.fullmatch(r"[A-Za-z0-9_-]+", profile):
            raise ValueError("Unrecognised Codex profile name")
        paths.append(home / f"{profile}.config.toml")

    found = {("log_dir", home / "log"), ("sqlite_home", home)}
    sqlite = env.get("CODEX_SQLITE_HOME", "").strip()
    if sqlite:
        found.add(("sqlite_home", _launch_path(sqlite, cwd)))
    layers = []
    for path in dict.fromkeys(paths):
        try:
            if not stat.S_ISREG(path.stat().st_mode):
                raise ValueError("Codex configuration path is not a regular file")
            layers.append((tomllib.loads(path.read_text(encoding="utf-8-sig")), path.parent))
        except FileNotFoundError:
            if os.path.lexists(path):
                raise
    for override in overrides:
        key, value = (part.strip() for part in override.split("=", 1))
        try:
            cfg = tomllib.loads(override)
        except ValueError:
            # Codex treats a non-TOML CLI right-hand side as a literal string.
            cfg = {key: value}
        layers.append((cfg, cwd))
    profiles = {context["profile"], *(layer.get("profile") for layer, _ in layers)}
    for cfg, base in layers:
        found.update(_configured_launch_dirs(cfg, base))
        for name, profile in (cfg.get("profiles") or {}).items():
            if name in profiles:
                found.update(_configured_launch_dirs(profile, base))
    return found


def protected_install_roots(project, home, executing_plugin):
    """The executing plugin and actual installed versions of the configured target."""
    from _codex_trust import project_plugin

    yield executing_plugin
    _, plugin_id, _ = project_plugin(project)
    market = plugin_id.partition("@")[2] if plugin_id else ""
    if market and market not in (".", "..") and not Path(market).anchor and not any(char in market for char in "/\\"):
        core = Path(home) / "plugins/cache" / market / "core"
        if core.is_dir():
            yield from (path for path in core.iterdir() if path.is_dir() or is_dir_link(path))


class LaunchPathGuard:
    """Resolve only known startup targets and their existing parents."""

    def __init__(self, project, plugin_root, context, env, overrides):
        from _state_io import state_dir_of

        home = self.home = Path(context["codex_home"])
        plugins = protected_install_roots(project, home, plugin_root)
        self.protected = {_real_launch_path(path, directory=True)
                          for path in (project, state_dir_of(project), *plugins)}

        storage = home / ".hook-trust"
        if is_dir_link(storage) or storage.is_dir() and any(is_dir_link(path) for path in storage.iterdir()):
            raise ValueError("Cannot verify redirected Codex trust storage")
        self.targets = {(home, False)}
        self.targets.update((_real_launch_path(home / name, directory=True), True)
                            for name in (*CONTROL_DIRS, *REMOTE_PLUGIN_DIRS,
                                         "skills", "skills/.system", ".hook-trust"))
        dates = {date.today(), datetime.now(timezone.utc).date()}
        recent = {d + timedelta(days=offset) for d in dates for offset in (-1, 0, 1)}
        files = ("config.toml", *BOOTSTRAP_FILES, PLUGIN_SYNC_LOCK, "models_cache.json",
                 *(f".sandbox/sandbox.{d.isoformat()}.log" for d in recent))
        self.targets.update((_real_launch_path(home / name, directory=False), False) for name in files)
        self.add_directories(_candidate_launch_dirs(context, env, overrides))

    def add_directories(self, targets):
        for kind, path in targets:
            directory = _real_launch_path(path, directory=True)
            # The default SQLite directory is the home itself, which can contain
            # the protected plugin in an unrelated subtree.
            self.targets.add((directory, kind == "log_dir" or directory != self.home))
            files = (("codex-tui.log", LOG_DATABASE) if kind == "log_dir"
                     else (*STATE_DATABASES, LOG_DATABASE))
            for name in files:
                suffixes = ("", "-wal", "-shm", "-journal") if name.endswith(".sqlite") else ("",)
                self.targets.update((_real_launch_path(directory / (name + suffix), directory=False), False)
                                    for suffix in suffixes)

    def effective(self, config):
        self.add_directories(_configured_launch_dirs(config))

    def overlaps(self, loaded_roots=()):
        protected = self.protected | {_real_launch_path(path, directory=True) for path in loaded_roots}
        return any(path.is_relative_to(root) or covers and root.is_relative_to(path)
                   for path, covers in self.targets for root in protected)


def source_binding(context, plugin_id, root, user, effective):
    """Bind a loaded root, or verify available definitions for a door repair.

    `root=None` does not establish any loaded installation or writable target;
    it verifies source definitions when an untrusted project cannot load core.
    """
    name, separator, market = plugin_id.partition("@")
    if not separator or name != "core" or not market or market in (".", "..") or any(c in market for c in "/\\"):
        return None
    registration = (user.get("marketplaces") or {}).get(market)
    active = (effective.get("marketplaces") or {}).get(market)
    if not isinstance(registration, dict) or not isinstance(active, dict):
        return None
    kind, raw = registration.get("source_type"), registration.get("source")
    if kind not in ("local", "git") or not isinstance(raw, str) or not raw:
        return None
    if active.get("source_type") != kind:
        return None
    if kind == "local":
        if not isinstance(active.get("source"), str) or _norm_path(_unc_strip(active["source"])) != _norm_path(_unc_strip(raw)):
            return None
        market_root = Path(_unc_strip(raw))
        if not market_root.is_absolute():
            return None
    else:
        if active.get("source") != raw or active.get("ref") != registration.get("ref"):
            return None
        market_root = Path(context["codex_home"]) / ".tmp/marketplaces" / market
    files = [market_root / ".agents/plugins/marketplace.json", market_root / ".claude-plugin/marketplace.json"]
    manifest_path = next((path for path in files if path.is_file()), None)
    if manifest_path is None:
        return None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    entries = [row for row in manifest.get("plugins", []) if row.get("name") == name]
    if manifest.get("name") != market or len(entries) != 1:
        return None
    source = entries[0].get("source")
    subpath = source if isinstance(source, str) else source.get("path") if isinstance(source, dict) and source.get("source") == "local" else None
    if not isinstance(subpath, str) or Path(subpath).is_absolute() or ".." in PurePosixPath(subpath.replace("\\", "/")).parts:
        return None
    subpath = PurePosixPath(subpath.replace("\\", "/")).as_posix()
    candidate = root if root is not None else market_root / subpath
    metadata = json.loads((candidate / ".codex-plugin/plugin.json").read_text(encoding="utf-8-sig"))
    version = metadata.get("version")
    if metadata.get("name") != name or not isinstance(version, str) or not version:
        return None
    if root is None:
        import _codex_trust as ct
        ct.read_manifest(candidate)
    cached = Path(context["codex_home"]) / "plugins/cache" / market / name / str(version)
    if root is not None and _norm_path(cached) != _norm_path(root) and not (kind == "local" and _norm_path(market_root / subpath) == _norm_path(root)):
        return None
    return {"kind": "directory" if kind == "local" else "git", "marketplace_id": market,
            "plugin_name": name, "plugin_subpath": subpath,
            **({"real_path": market_root.resolve().as_posix()} if kind == "local" else {"repository": raw}),
            "observed_ref": registration.get("ref"), "observed_revision": None, "assurance": "registry_binding"}


class Codex01672:
    control_dirs = CONTROL_DIRS
    supports_recovery = True
    # The first start in a home holding only config.toml creates these (a
    # second start creates none), and none is within the registered allowance.
    # The sync lock appears only while the plugins feature is enabled.
    initialised_files = BOOTSTRAP_FILES + STATE_DATABASES
    plugin_sync_lock = PLUGIN_SYNC_LOCK

    def __init__(self):
        self.user_layer = None
        self.configuration = None
        self.attempted = {}
        self.attempted_storage = None

    def plugins_known_off(self, context, env, overrides, config):
        """True only when every source of the plugins switch is known and the result is false.

        The user config, any other configuration layer, a selected profile and the
        ordered --codex-config overrides all reach the service. Anything that
        cannot be read or interpreted counts as not known, so the lock is required.
        """
        import tomllib
        try:
            user = tomllib.loads(config.decode("utf-8-sig"))
            if context.get("profile") is not None or "profile" in user:
                return False
            for path in config_layer_paths(context, env)[1:]:
                if path.is_file() and "plugins" in (tomllib.loads(path.read_text(encoding="utf-8-sig")).get("features") or {}):
                    return False
            features = user.get("features")
            value = features.get("plugins") if isinstance(features, dict) else None
            for override in overrides:
                key = override.split("=", 1)[0].strip().strip('"')
                if key == "profile":
                    return False
                if key == "features" or key.startswith("features."):
                    value = (tomllib.loads(override).get("features") or {}).get("plugins", value)
            return value is False
        except (OSError, ValueError, TypeError, AttributeError):
            return False

    def uninitialised(self, snapshot, context, env, overrides, config, *, launch=False):
        """Names this build would create on its first start in the home.

        `snapshot` is the pre-start tree snapshot, `config` the user config bytes;
        a missing or non-file entry counts as absent.
        """
        required = BOOTSTRAP_FILES if launch else self.initialised_files
        if not self.plugins_known_off(context, env, overrides, config):
            required += (self.plugin_sync_lock,)
        return [name for name in required if snapshot.get(name, (None,))[0] != "file"]

    def observe(self, server, context, project, *, config=None, listing=None):
        import _codex_appserver as cas
        import _codex_trust as ct
        import _query_codex_trust as trust
        config = server.config_read(True, cwd=context["cwd"]) if config is None else config
        self.configuration = config
        listing = server.hooks_list([context["cwd"]]) if listing is None else listing
        data = trust.empty(context)
        try:
            hooks = cas.hooks_of(listing)
            if len(listing["data"]) != 1 or _norm_path(listing["data"][0]["cwd"]) != _norm_path(context["cwd"]):
                return trust.reject(data, "context_unverifiable", "Codex returned a different working directory")
            layers = [layer for layer in config.get("layers", []) if layer.get("name", {}).get("type") == "user"
                      and layer["name"].get("profile") is None
                      and _norm_path(layer["name"].get("file", "")) == _norm_path(Path(context["codex_home"]) / "config.toml")]
            self.user_layer = layers[0] if len(layers) == 1 else None
            user = self.user_layer.get("config", {}) if self.user_layer else {}
            cfg = config["config"]
            declared_state, declared, _ = ct.project_plugin(project)
            ids = {key for key in (cfg.get("plugins") or {}) if key.startswith("core@")}
            ids |= {hook["pluginId"] for hook in hooks if isinstance(hook.get("pluginId"), str) and hook["pluginId"].startswith("core@")}
            if declared:
                ids = {declared}
            if not ids:
                cache = Path(context["codex_home"]) / "plugins/cache"
                if self.user_layer is not None and not list(cache.glob("*/core/*/.codex-plugin/plugin.json")):
                    return trust.reject(data, "not_installed", "User registration and installation cache contain no core plugin", "not_installed", "repair_install")
            if len(ids) != 1:
                return trust.reject(data, "source_unverified", "Cannot identify one registered core plugin", action="confirm_source")
            plugin_id = next(iter(ids))
            market = plugin_id.partition("@")[2]
            if not market or market in (".", "..") or any(char in market for char in "/\\"):
                return trust.reject(data, "source_unverified", "Core marketplace identity is invalid", action="confirm_source")
            projects = cfg.get("projects") or {}
            trusted = [(len(_norm_path(path)), value.get("trust_level")) for path, value in projects.items()
                       if trust._inside(context["cwd"], _unc_strip(path))]
            project_trusted = max(trusted)[1] == "trusted" if trusted else False
            user_enabled = ((user.get("plugins") or {}).get(plugin_id) or {}).get("enabled")
            origin_kinds = trust._origin_kinds(config, [trust._plugin_enable_paths(plugin_id)])
            isolation = (declared_state == ct.TRUSTED and project_trusted is False and user_enabled is not True
                         and all(kind in (None, "user") for kind in origin_kinds))
            roots = {Path(_unc_strip(hook["sourcePath"])).parent.parent for hook in hooks if hook.get("pluginId") == plugin_id}
            source = source_binding(context, plugin_id, next(iter(roots)), user, cfg) if len(roots) == 1 else None
            if isolation and not roots:
                source = source_binding(context, plugin_id, None, user, cfg)
            # Managed declarations identify a target; they cannot make absent
            # installation/market records count as an installed plugin.
            registrations = user.get("marketplaces", {})
            active_registrations = cfg.get("marketplaces") or {}
            cache = Path(context["codex_home"]) / "plugins/cache"
            absent = (self.user_layer is not None and isinstance(registrations, dict)
                      and isinstance(active_registrations, dict)
                      and plugin_id.partition("@")[2] not in registrations
                      and plugin_id.partition("@")[2] not in active_registrations
                      and not list((cache / market / "core").glob("*/.codex-plugin/plugin.json")))
            value = trust.inspect(context, plugin_id, listing, config, source, project_trusted=project_trusted,
                                  installed=False if absent else None, project_isolation=isolation)
            if value["recovery"]["eligible"] and self.write_target(context) is None:
                value["recovery"].update(eligible=False, reason="A unique user-layer version is required for recovery")
            return value
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            return trust.reject(data, "protocol_unsupported", "Codex registration/configuration shape could not be verified")

    def write_target(self, context):
        layer = self.user_layer
        if not layer or not isinstance(layer.get("version"), str) or not layer["version"]:
            return None
        target = layer.get("name", {}).get("file")
        if not isinstance(target, str) or _norm_path(target) != _norm_path(Path(context["codex_home"]) / "config.toml"):
            return None
        return target, layer["version"]

    def storage_target(self, context, observation):
        """Bind one plugin-content baseline to the actual user-layer path."""
        target = self.write_target(context)
        plugin_id = (observation.get("plugin") or {}).get("id")
        hashes = [hook.get("current_hash") for hook in observation.get("hooks", [])]
        if target is None or not isinstance(plugin_id, str) or not plugin_id or not hashes:
            return None
        contents = {value.partition("/")[2] for value in hashes if isinstance(value, str)}
        if (any(not isinstance(value, str) or value.count("/") != 1 or not value.partition("/")[0] for value in hashes)
                or len(contents) != 1 or not re.fullmatch(r"sha256:[0-9a-f]{64}", next(iter(contents), ""))):
            return None
        config_file = Path(_unc_strip(target[0]))
        path = config_file.parent / ".hook-trust" / hook_trust_filename(str(config_file), plugin_id)
        return path, next(iter(contents))


class Codex01690(Codex01672):
    """The Windows 0.169.0 launch observation shares the measured read protocol."""

    supports_recovery = False

    def write_target(self, context):
        return None
