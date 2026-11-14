"""The measured 0.167.2 read/config transport and its file-effect boundary.

No login, plugin refresh, thread or turn operation is used. Registration is
read from the frozen server's user layer and reconciled with the loaded root;
project declarations alone are not installation or source evidence.
"""

import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import time

from _harness import is_dir_link
from workframe_door import _norm_path, _unc_strip

VERSION = "0.167.2"
CONTROL_DIRS = ("tmp", ".tmp", ".sandbox")


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
    # The first start in a home holding only config.toml creates these (a
    # second start creates none), and none is within the registered allowance.
    # The sync lock appears only while the plugins feature is enabled.
    initialised_files = ("installation_id", "skills/.system/.codex-system-skills.marker",
                         "state_5.sqlite", "goals_1.sqlite", "memories_1.sqlite", "queue_1.sqlite")
    plugin_sync_lock = ".tmp/plugins.sync.lock"

    def __init__(self):
        self.user_layer = None
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

    def uninitialised(self, snapshot, context, env, overrides, config):
        """Names this build would create on its first start in the home.

        `snapshot` is the pre-start tree snapshot, `config` the user config bytes;
        a missing or non-file entry counts as absent.
        """
        required = self.initialised_files
        if not self.plugins_known_off(context, env, overrides, config):
            required += (self.plugin_sync_lock,)
        return [name for name in required if snapshot.get(name, (None,))[0] != "file"]

    def observe(self, server, context, project):
        import _codex_appserver as cas
        import _codex_trust as ct
        import _query_codex_trust as trust
        config = server.config_read(True, cwd=context["cwd"])
        listing = server.hooks_list([context["cwd"]])
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
