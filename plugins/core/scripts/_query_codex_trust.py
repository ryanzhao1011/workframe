"""Versioned, read-only Codex trust observations and frozen launch contexts.

The transport and manifest verdicts are shared with the door. Only measured
runtime versions can start a server or perform narrow conditional writes.
"""

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

import _codex_appserver as cas
import _codex_trust as ct
from _state_io import state_dir_of
from _harness import is_dir_link
from workframe_door import _norm_path, _unc_strip

STATES = ("ready", "needs_trust", "disabled_by_user", "not_installed", "version_unsupported", "unverified")
RESTORE_SCHEMA = "workframe.codex-trust-restore.v1"
PLUGIN_ROOT = Path(__file__).resolve().parents[1]
CONTEXT_FIELDS = ("codex_exe", "codex_home", "codex_cwd", "codex_profile", "codex_config", "timeout_ms")


def add_context_arguments(parser):
    parser.add_argument("--codex-exe")
    parser.add_argument("--codex-home")
    parser.add_argument("--codex-cwd")
    parser.add_argument("--codex-profile")
    parser.add_argument("--codex-config", action="append")
    parser.add_argument("--timeout-ms", type=int)


class TrustUsageError(ValueError):
    """Invalid public launch arguments; callers return a JSON usage error."""


def digest(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def problem(code, message):
    return {"code": code, "message": message}


def empty(context):
    return {"state": "unverified", "context": dict(context), "plugin": None, "hooks": [],
            "effective": {"project_trusted": None, "plugin_enabled": None, "hooks_enabled": None,
                          "disabled_hooks": [], "disabled_origin": "unknown"},
            "problems": [], "snapshot_id": None, "change_token": None,
            "recovery": {"eligible": False, "reason": "Current target is not verified", "required_action": "recheck"}}


def reject(data, code, message, state="unverified", action="recheck"):
    data["state"] = state
    data["problems"].append(problem(code, message))
    data["recovery"] = {"eligible": False, "reason": message, "required_action": action}
    return data


def _inside(path, root):
    path, root = _norm_path(path), _norm_path(root)
    return path == root or path.startswith(root.rstrip("\\/") + os.sep)


def protected_overlap(project, home, plugin_root=PLUGIN_ROOT, control_dirs=(), deadline=None):
    """Resolve aliases before *any* external process, including --version.

    A normal home can contain the installed plugin. Writable log/control
    subtrees cannot contain or lie inside protected trees. Existing linked log
    files and directories are checked individually without following the walk.
    """
    from _query_codex_runtime import REMOTE_PLUGIN_DIRS, _real_launch_path, protected_install_roots

    home = Path(home)
    protected = [Path(path).resolve() for path in
                 (project, state_dir_of(project), *protected_install_roots(project, home, plugin_root))]
    if any(_inside(home, path) for path in protected):
        return True
    storage = home / ".hook-trust"
    if storage.is_symlink() or is_dir_link(storage):
        return True
    if storage.is_dir() and any(path.is_symlink() or is_dir_link(path) for path in storage.iterdir()):
        return True
    targets = [home / "config.toml", home / "log", *[home / name for name in control_dirs]]
    targets += list(home.glob("*.sqlite*"))
    for directory in [home / "log", *[home / name for name in control_dirs]]:
        if directory.is_dir():
            for root, dirs, files in os.walk(directory, followlinks=False):
                if deadline is not None and time.monotonic() >= deadline:
                    raise TimeoutError("Codex path preflight deadline expired")
                linked = [Path(root) / name for name in dirs + files
                          if (Path(root) / name).is_symlink() or is_dir_link(Path(root) / name)]
                targets.extend(linked)
                dirs[:] = [name for name in dirs if Path(root) / name not in linked]
    for target in targets:
        if any(_inside(target, path) or (target.is_dir() and _inside(path, target)) for path in protected):
            return True
    for target in REMOTE_PLUGIN_DIRS:
        directory = _real_launch_path(home / target, directory=True)
        if any(_inside(directory, path) or _inside(path, directory) for path in protected):
            return True
    model_cache = _real_launch_path(home / "models_cache.json", directory=False)
    if any(_inside(model_cache, path) for path in protected):
        return True
    return False


def resolve_entry(requested, env, *, unambiguous=False):
    """Accept native executables or resolve a known npm entry without a shell."""
    raw = requested if requested is not None else cas.find_codex_exe(env)
    if not isinstance(raw, str) or not raw:
        return None
    path = Path(_unc_strip(raw))
    if not path.is_absolute() or not path.is_file():
        return None
    if path.name.lower() in ("codex", "codex.cmd", "codex.ps1"):
        base = path.parent.joinpath(*cas._NPM_VENDOR_REL)
        candidates = sorted(base.glob("codex-*/vendor/*/bin/codex.exe"))
        candidates += sorted(base.glob("codex-*/vendor/*/bin/codex"))
        if candidates:
            if unambiguous and len(candidates) != 1:
                return None
            path = candidates[0]
    with path.open("rb") as stream:
        magic = stream.read(4)
    native = magic[:2] == b"MZ" if os.name == "nt" else magic in (
        b"\x7fELF", b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xca\xfe\xba\xbe")
    return str(path.resolve()) if native else None


def launch_context(project, options, env=None):
    """Freeze arguments and inherited environment; only a digest exposes values."""
    env = dict(os.environ if env is None else env)
    timeout_ms = options.get("timeout_ms") if options.get("timeout_ms") is not None else 30000
    if not isinstance(timeout_ms, int) or isinstance(timeout_ms, bool) or not 0 < timeout_ms <= 30000:
        raise TrustUsageError("--timeout-ms must be between 1 and 30000")
    raw_home = options.get("codex_home")
    home = Path(_unc_strip(raw_home)) if raw_home is not None else ct.codex_home()
    if not home.is_absolute() or not home.is_dir():
        raise TrustUsageError("Codex home must be an existing absolute directory; it will not be created")
    cwd = Path(_unc_strip(options.get("codex_cwd") or str(project)))
    if not cwd.is_absolute() or not cwd.is_dir():
        raise TrustUsageError("Codex cwd must be an existing absolute directory")
    overrides = tuple(options.get("codex_config") or ())
    if any(not isinstance(value, str) or "=" not in value or not value.split("=", 1)[0].strip()
           or "\x00" in value for value in overrides):
        raise TrustUsageError("Each --codex-config must contain a nonempty key=value")
    context = {"requested_exe": options.get("codex_exe"), "resolved_exe": None, "codex_version": None,
               "codex_home": home.resolve().as_posix(), "cwd": cwd.resolve().as_posix(),
               "profile": options.get("codex_profile"), "context_id": None}
    # Values and environment can contain credentials. They are never returned.
    context["context_id"] = digest({"context": context, "config": overrides, "env": env})
    return context, env, overrides, timeout_ms


def runtime_adapter(context, *, active_launch=False):
    """Select only the exact measured build; there is no enabling override."""
    from _query_codex_runtime import Codex01672, Codex01690, VERSION, LAUNCH_OBSERVATION_VERSION
    version = context.get("codex_version")
    if version == VERSION:
        return Codex01672()
    if active_launch and os.name == "nt" and version == LAUNCH_OBSERVATION_VERSION:
        return Codex01690()
    return None


def _read_version(context, env, deadline):
    result = subprocess.run([context["resolved_exe"], "--version"], cwd=context["cwd"],
                            env=dict(env, CODEX_HOME=context["codex_home"]), stdin=subprocess.DEVNULL,
                            capture_output=True, timeout=max(0.001, deadline - time.monotonic()))
    match = re.fullmatch(rb"codex-cli ([0-9]+\.[0-9]+\.[0-9]+)\s*", result.stdout)
    return match[1].decode("ascii") if result.returncode == 0 and match else None


def _entry_identity(executable):
    """Metadata invalidates a discovery. It never certifies hook trust."""
    with open(executable, "rb") as stream:
        info = os.fstat(stream.fileno())
        changed = _windows_change_time(stream) if os.name == "nt" else info.st_ctime_ns
    if not info.st_ino or not changed:
        return None
    return digest([executable, info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, changed])


def _windows_change_time(stream):
    # Python's Windows st_ctime currently reports creation time. FileBasicInfo
    # also reports an NTFS metadata change, including a restored last-write time.
    import ctypes
    from ctypes import wintypes
    import msvcrt

    class FileBasicInfo(ctypes.Structure):
        _fields_ = [(field, ctypes.c_int64) for field in ("creation", "access", "write", "change")] + [
            ("attributes", wintypes.DWORD)]

    query = ctypes.WinDLL("kernel32", use_last_error=True).GetFileInformationByHandleEx
    query.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    query.restype = wintypes.BOOL
    result = FileBasicInfo()
    if query(msvcrt.get_osfhandle(stream.fileno()), 0, ctypes.byref(result), ctypes.sizeof(result)):
        return result.change
    return None


def _launch_version(context, env, deadline, hint, metadata):
    executable = context["resolved_exe"]
    identity = _entry_identity(executable)
    version = hint.get("version") if identity and isinstance(hint, dict) and hint.get("identity") == identity else None
    if not isinstance(version, str) or re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version) is None:
        version = _read_version(context, env, deadline)
    if identity and identity != _entry_identity(executable):
        raise OSError("Codex executable changed during discovery")
    if identity and version is not None:
        metadata["_version_hint"] = {"identity": identity, "version": version}
    return version


def _plugin_enable_paths(plugin_id):
    return ("plugins." + plugin_id + ".enabled", "plugins." + json.dumps(plugin_id) + ".enabled")


def _origin_kinds(config, paths):
    origins = config.get("origins") or {}
    return [(origins.get(candidate) or {}).get("name", {}).get("type")
            for path in paths for candidate in ((path,) if isinstance(path, str) else path)]


def _disabled_origin(config, paths):
    kinds = _origin_kinds(config, paths)
    if any(kind in ("system", "mdm", "enterpriseManaged", "cloudManagedConfig",
                    "legacyManagedConfigTomlFromFile", "legacyManagedConfigTomlFromMdm") for kind in kinds):
        return "policy"
    return "user" if any(kind in ("user", "project", "sessionFlags") for kind in kinds) else "unknown"


def inspect(context, plugin_id, listing, config, source, *, project_trusted,
            installed=None, python_supported=True, project_isolation=False):
    """Classify one complete observation; no transport or configuration writes.

    `installed=False` is accepted only from the adapter's installation registry
    proof; an empty hooks response by itself is never that proof. Raw effective
    config, rather than the user layer's isolation flag, determines enablement.
    """
    data = empty(context)
    effective = data["effective"]
    if not python_supported:
        return reject(data, "protocol_unsupported", "Codex trust requires Python 3.11 or later", "version_unsupported")
    try:
        cfg = config["config"]
        plugin_cfg = (cfg.get("plugins") or {}).get(plugin_id, {})
        hook_cfg = cfg.get("hooks") or {}
        features = cfg.get("features") or {}
        effective["project_trusted"] = project_trusted
        effective["plugin_enabled"] = plugin_cfg.get("enabled")
        effective["hooks_enabled"] = hook_cfg.get("enabled", features.get("hooks", True))
        hooks = cas.hooks_of(listing)
        if any(not isinstance(hook, dict) for hook in hooks):
            raise ValueError("Hook entries must be objects")
        mine = [hook for hook in hooks if hook.get("pluginId") == plugin_id]
        effective["disabled_hooks"] = [hook.get("key") for hook in mine if hook.get("enabled") is False
            and (not project_isolation or ((hook_cfg.get("state") or {}).get(hook.get("key")) or {}).get("enabled") is False)]
        disabled = []
        if effective["plugin_enabled"] is False and not project_isolation:
            disabled.append(_plugin_enable_paths(plugin_id))
        if effective["hooks_enabled"] is False:
            disabled += ["hooks.enabled", "features.hooks"]
        disabled += [("hooks.state." + key + ".enabled", "hooks.state." + json.dumps(key) + ".enabled")
                     for key in effective["disabled_hooks"] if isinstance(key, str)]
        if disabled:
            origin = _disabled_origin(config, disabled)
            effective["disabled_origin"] = origin
            return reject(data, "policy_disabled" if origin == "policy" else "disabled_by_user",
                          "Effective Codex configuration explicitly disables plugin hooks", "disabled_by_user", "enable_in_codex")
        effective["disabled_origin"] = "none"
        if installed is False and not mine:
            return reject(data, "not_installed", "Installation and marketplace registry confirm the plugin is absent", "not_installed", "repair_install")
        # An untrusted project cannot activate its managed enablement. A verified
        # registration/definition can justify a door repair, never a writable
        # target or invented runtime hooks when discovery is empty.
        if not mine and project_isolation and project_trusted is False and isinstance(source, dict) and source.get("assurance") == "registry_binding":
            return reject(data, "project_not_trusted", "Use workframe-door to establish project trust", "needs_trust", "repair_install")
        data["hooks"] = [{"key": hook.get("key"), "status": hook.get("trustStatus") if hook.get("trustStatus") in ("trusted", "modified", "untrusted") else "unknown",
                          "current_hash": hook.get("currentHash"), "enabled": hook.get("enabled"),
                          "event": hook.get("eventName"), "source_path": hook.get("sourcePath")} for hook in mine]
        keys = [hook.get("key") for hook in mine]
        if any(not isinstance(key, str) or not key for key in keys):
            return reject(data, "manifest_mismatch", "Hook entry lacks a complete key")
        if len(set(keys)) != len(keys):
            return reject(data, "duplicate_key", "Duplicate full hook key")
        if any(hook.get("trustStatus") not in ("trusted", "modified", "untrusted")
               or not isinstance(hook.get("currentHash"), str) or not hook.get("currentHash") for hook in mine):
            return reject(data, "hook_status_unknown", "Hook trust status or opaque current hash is unknown")
        roots = set()
        for hook in mine:
            path = Path(_unc_strip(hook.get("sourcePath") or ""))
            if not path.is_absolute() or path.name != "hooks.codex.json" or path.parent.name != "hooks":
                return reject(data, "manifest_mismatch", "Hook source is not an absolute plugin manifest path")
            roots.add(_norm_path(path.parent.parent))
        if len(roots) > 1:
            return reject(data, "multiple_roots", "Plugin hooks come from multiple installation roots")
        if not roots:
            return reject(data, "manifest_mismatch", "Empty hook list cannot verify an installation")
        root = Path(next(iter(roots)))
        baseline = root / "hooks/hooks.codex.manifest.json"
        installed_manifest = root / "hooks/hooks.codex.json"
        before = (baseline.read_bytes(), installed_manifest.read_bytes())
        diagnosis = ct.inspect_listing(listing, plugin_id)
        if diagnosis.state not in (ct.TRUSTED, ct.NEEDS_TRUST):
            return reject(data, "manifest_mismatch", "Shared manifest reconciliation did not verify the complete hook list")
        if before != (baseline.read_bytes(), installed_manifest.read_bytes()):
            return reject(data, "manifest_mismatch", "Hook manifests changed during reconciliation")
        if not isinstance(source, dict) or source.get("assurance") != "registry_binding":
            return reject(data, "source_unverified", "Loaded plugin could not be bound to a marketplace registration", action="confirm_source")
        version = json.loads((root / ".codex-plugin/plugin.json").read_text(encoding="utf-8-sig")).get("version")
        if not isinstance(version, str) or not version:
            return reject(data, "manifest_mismatch", "Loaded plugin has no version")
        if listing["data"][0].get("errors"):
            return reject(data, "manifest_mismatch", "Codex reported hook discovery errors")
        if not project_isolation and (any(hook.get("enabled") is not True for hook in mine) or effective["plugin_enabled"] is not True or effective["hooks_enabled"] is not True):
            return reject(data, "context_unverifiable", "Effective plugin or hook enablement is unknown")
        data["plugin"] = {"id": plugin_id, "version": version, "source": source, "install_root": root.as_posix()}
        definitions = json.loads(before[0].decode("utf-8-sig"))["entries"]
        observed = [{key: value for key, value in hook.items() if key not in (
            "trustStatus", "statusMessage", "enabled", "isManaged")} for hook in mine]
        target = {"plugin": data["plugin"], "definitions": definitions,
                  "hooks": sorted(observed, key=lambda hook: hook["key"])}
        data["change_token"] = digest(target)
        data["snapshot_id"] = digest({"context_id": context["context_id"], "target": target})
        if project_trusted is False:
            return reject(data, "project_not_trusted", "Use workframe-door to establish project trust", "needs_trust", "repair_install")
        if project_trusted is not True:
            data["snapshot_id"] = None
            return reject(data, "context_unverifiable", "Project trust could not be verified")
        if any(hook.get("trustStatus") != "trusted" for hook in mine):
            reject(data, "hooks_untrusted", "Current plugin hooks require authorization", "needs_trust", "authorize_current")
            if runtime_adapter(context) is not None:
                data["recovery"].update(eligible=True, reason="Current hooks can be restored with a conditional user-layer write")
            return data
        data["state"] = "ready"
        data["recovery"] = {"eligible": False, "reason": "Current target is already trusted", "required_action": "none"}
        return data
    except (cas.AppServerError, OSError, ValueError, TypeError, KeyError, AttributeError):
        # Exceptions can embed overridden credentials or raw RPC configuration.
        return reject(data, "protocol_unsupported", "Codex observation does not match the supported protocol shape")


def query(project, options, *, reader=None, operation=None):
    return _query(project, options, reader=reader, operation=operation)


def launch_preflight(project, options, *, reader=None, version_hint=None, require_sandbox=True):
    """Private active-session observation. Only the guarded query accepts writes."""
    return _query(project, options, reader=reader, launch=True, version_hint=version_hint,
                  require_sandbox=require_sandbox)


def _observe_launch(server, context, project, adapter, guard, require_sandbox, metadata):
    data = empty(context)
    try:
        config = server.config_read(True, cwd=context["cwd"])
        effective = config["config"]
        windows = effective.get("windows")
        sandbox = windows.get("sandbox") if windows is not None else None
        if sandbox is not None and sandbox not in ("unelevated", "elevated"):
            raise ValueError("Unknown effective Windows sandbox")
        guard.effective(effective)
        if guard.overlaps():
            return reject(data, "protected_path_overlap", "Effective Codex paths overlap a protected project, state or plugin directory")
        metadata["_launch_can_authorize"] = adapter.supports_recovery
        if require_sandbox and sandbox is None:
            metadata["_sandbox"] = None
            return data
        listing = server.hooks_list([context["cwd"]])
        # The observed installation can differ from the copy running this query.
        from _query_codex_runtime import _launch_path
        roots = {_launch_path(hook["sourcePath"]).parent.parent for hook in cas.hooks_of(listing)
                 if isinstance(hook.get("pluginId"), str) and hook["pluginId"].startswith("core@")}
        if guard.overlaps(roots):
            return reject(data, "protected_path_overlap", "Effective Codex paths overlap a loaded plugin directory")
        metadata["_sandbox"] = sandbox
        return adapter.observe(server, context, project, config=config, listing=listing)
    except cas.AppServerError:
        raise
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError):
        return reject(data, "side_effect_scope_unknown", "Effective Codex side-effect paths or sandbox could not be verified")


def _query(project, options, *, reader=None, operation=None, launch=False,
           version_hint=None, require_sandbox=True):
    """Preflight, version probe, owned server, optional operation, then audit.

    The internal operation runs on the same frozen connection. Its result is
    retained in metadata even if the final file audit fails after a write.
    Unknown builds never start an app-server.
    """
    from _query_codex_runtime import CONTROL_DIRS, LaunchPathGuard, runtime_paths_known, trust_input_snapshot, presence_rows, config_bytes, side_effect_violations, trust_only_config_change, trust_config_readback
    try:
        context, env, overrides, timeout_ms = launch_context(project, options)
    except (OSError, RuntimeError):
        if not launch:
            raise
        data = empty({})
        return reject(data, "side_effect_scope_unknown", "Codex home or working directory could not be resolved"), {}
    deadline = time.monotonic() + timeout_ms / 1000.0
    data = empty(context)
    meta = {"external_depends_on": [{"kind": "codex_config", "path": (Path(context["codex_home"]) / "config.toml").as_posix()}]}
    if launch and sys.version_info < (3, 11):
        return reject(data, "protocol_unsupported", "Codex trust requires Python 3.11 or later", "version_unsupported"), meta
    try:
        guard = LaunchPathGuard(project, PLUGIN_ROOT, context, env, overrides) if launch else None
        overlap = guard.overlaps() if guard else protected_overlap(
            project, context["codex_home"], control_dirs=CONTROL_DIRS, deadline=deadline)
    except (OSError, RuntimeError, ValueError, TypeError, AttributeError):
        return reject(data, "side_effect_scope_unknown", "Codex side-effect paths could not be resolved"), meta
    if overlap:
        return reject(data, "protected_path_overlap", "Codex paths overlap a protected project, state or plugin directory"), meta
    if sys.version_info < (3, 11):
        return reject(data, "protocol_unsupported", "Codex trust requires Python 3.11 or later", "version_unsupported"), meta
    # Parse project files even when no runtime adapter is available, so file
    # errors keep their public parse_error response and dependencies.
    if reader is not None:
        reader(Path(project) / ".codex/config.toml", ".codex/config.toml")
    if not _inside(context["cwd"], project):
        return reject(data, "context_unverifiable", "Codex cwd is outside the requested project"), meta
    try:
        resolved = (resolve_entry(options.get("codex_exe"), env, unambiguous=True) if launch
                    else resolve_entry(options.get("codex_exe"), env))
    except OSError:
        resolved = None
    if resolved is None:
        return reject(data, "entry_unresolved", "Codex entry cannot be resolved to a native executable"), meta
    context["resolved_exe"] = resolved
    if not launch and not runtime_paths_known(context, env, overrides):
        return reject(data, "side_effect_scope_unknown", "Custom Codex log/database locations have no measured side-effect boundary"), meta
    home, adapter = Path(context["codex_home"]), None
    try:
        before = trust_input_snapshot(home, deadline)
        config_before = config_bytes(home)
    except (OSError, RuntimeError):
        return reject(data, "side_effect_scope_unknown", "Codex side-effect baseline could not be read"), meta
    try:
        context["codex_version"] = (_launch_version(context, env, deadline, version_hint, meta) if launch
                                    else _read_version(context, env, deadline))
        context["context_id"] = digest({"context": {key: value for key, value in context.items() if key != "context_id"},
                                        "config": overrides, "env": env})
        data["context"] = dict(context)
        adapter = runtime_adapter(context, active_launch=True) if launch else runtime_adapter(context)
        if adapter is None:
            return reject(data, "side_effect_scope_unknown", "Codex version has no registered runtime adapter"), meta
        presence = presence_rows(home, adapter.initialised_files + (adapter.plugin_sync_lock,))
        uninitialised = (adapter.uninitialised(presence, context, env, overrides, config_before, launch=True)
                         if launch else adapter.uninitialised(presence, context, env, overrides, config_before))
        if uninitialised:
            # Its own first start would create files outside the registered allowance.
            return reject(data, "side_effect_scope_unknown",
                          "Codex home is not initialised by this Codex build; starting the service there would write outside the registered allowance"), meta
        meta["side_effects"] = ["codex_app_server_logs"]
        with cas.AppServer(exe=resolved, codex_home=context["codex_home"], cwd=context["cwd"],
                           env=env, profile=context["profile"], config_overrides=overrides,
                           timeout=timeout_ms / 1000.0, deadline=deadline) as server:
            data = (_observe_launch(server, context, project, adapter, guard, require_sandbox, meta)
                    if launch else adapter.observe(server, context, project))
            if operation is not None:
                meta["_operation_result"] = operation(data, adapter, server)
    except cas.AppServerCloseError:
        meta["_audit_error"] = "Codex app-server exit could not be confirmed before the deadline"
    except cas.AppServerRpcError:
        reject(data, "protocol_unsupported", "Codex does not support the required observation protocol", "version_unsupported")
    except (cas.AppServerError, OSError, subprocess.TimeoutExpired):
        code = "timeout" if time.monotonic() >= deadline else "app_server_unavailable"
        reject(data, code, "Codex app-server could not complete the observation")
    finally:
        try:
            after = trust_input_snapshot(home, deadline)
            config_after = config_bytes(home)
            allowed_config = bool(adapter and adapter.attempted and trust_only_config_change(
                config_before, config_after, adapter.attempted))
            if config_before != config_after and not allowed_config:
                meta["_audit_error"] = "Codex user configuration changed outside the exact trust-write allowance"
            storage = adapter.attempted_storage if adapter else None
            violations = side_effect_violations(before, after, context["codex_version"], allow_config=allowed_config,
                                                 trust_storage=storage)
            if violations:
                meta["_audit_error"] = "Codex changed files outside the registered side-effect allowance"
            operation_result = meta.get("_operation_result")
            if adapter and adapter.attempted and operation_result and operation_result[1].get("write_outcome") == "confirmed":
                import tomllib
                persisted = tomllib.loads(config_after.decode("utf-8-sig"))
                if not trust_config_readback(persisted, adapter.attempted):
                    meta["_audit_error"] = "Codex trust hash fields are missing or mismatched after server shutdown"
            if storage and operation_result and operation_result[1].get("write_outcome") == "confirmed":
                expected = ("file", hashlib.sha256(storage[1].encode("utf-8")).hexdigest())
                if after.get(storage[0]) != expected or after.get(".hook-trust/") != ("directory",):
                    meta["_audit_error"] = "Codex trust baseline is missing or mismatched after server shutdown"
        except (OSError, RuntimeError, UnicodeError, ValueError, TypeError, AttributeError):
            meta["_audit_error"] = "Codex side effects could not be verified before the deadline"
        if meta.get("_audit_error"):
            reject(data, "side_effect_scope_unknown", meta["_audit_error"])
            data["snapshot_id"] = None
    return data, meta
