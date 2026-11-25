#!/usr/bin/env python3
"""Versioned read-only JSON queries; capabilities list implemented topics.

Run with python -B, or use the bin entry point. Direct invocation also disables
bytecode before importing plugin modules. No doctor/scaffold CLI is invoked.
"""

import os
import sys

sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
os.environ["GIT_OPTIONAL_LOCKS"] = "0"

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess

from _harness import ROOT_MARKER, cc_config_dir, find_root
from _project_directory import classify_directory
from _state_io import legacy_rel, state_dir_of
from _codex_trust import codex_home
from workframe_door import _norm_path, _unc_strip
from _marketplace_source import claude_source, codex_source, source_key
import workframe_doctor as doctor

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ("workframe.query.v1",)
TOPICS = ("project-status", "gates", "codex-trust", "board", "modules", "stats", "project-connection")
KNOWN_TOPICS = TOPICS


class QueryError(Exception):
    def __init__(self, code, message, detail=None, exit_code=1):
        super().__init__(message)
        self.code, self.detail, self.exit_code = code, detail, exit_code


class QueryParser(argparse.ArgumentParser):
    def error(self, message):
        raise QueryError("bad_usage", message, exit_code=2)


def read_object(path, rel):
    """Read project JSON/TOML without repair; parse failures retain their path."""
    try:
        text = path.read_text(encoding="utf-8-sig")
        if path.suffix == ".toml":
            try:
                import tomllib
            except ImportError as exc:
                raise QueryError("internal", "Reading TOML configuration requires Python 3.11 or later",
                                 {"file": rel}) from exc
            value = tomllib.loads(text)
        else:
            value = json.loads(text)
    except FileNotFoundError:
        return {}
    except (ValueError, UnicodeError) as exc:
        line = getattr(exc, "lineno", None)
        if isinstance(exc, UnicodeDecodeError):
            from _query_board import decode_error_line
            line = decode_error_line(exc)
        if line is None:
            # Python 3.11 exposes a TOML error's line only in the message.
            import re
            match = re.search(r"\(at line (\d+),", str(exc))
            line = int(match.group(1)) if match else text.count("\n") + 1
        raise QueryError("parse_error", f"Cannot parse {rel}: {exc}",
                         {"file": rel, "line": line}) from exc
    if not isinstance(value, dict):
        raise QueryError("parse_error", f"Expected an object in {rel}", {"file": rel, "line": 1})
    return value


def _mapping(value, rel, field):
    if not isinstance(value, dict):
        raise QueryError("parse_error", f"Expected an object for {field} in {rel}",
                         {"file": rel, "line": 1})
    return value


def project_support(project):
    claude = {}
    for rel in (".claude/settings.json", ".claude/settings.local.json"):
        cfg = read_object(project / rel, rel)
        claude.update(_mapping(cfg.get("enabledPlugins", {}), rel, "enabledPlugins"))
    rel = ".codex/config.toml"
    cfg = read_object(project / rel, rel)
    plugins = _mapping(cfg.get("plugins", {}), rel, "plugins")
    codex = any(name.startswith("core@") and isinstance(value, dict) and value.get("enabled") is True
                for name, value in plugins.items())
    return {"claude": any(name.startswith("core@") and value is True for name, value in claude.items()),
            "codex": codex}


def _guard_git_filters(project, git, *, reference=None):
    """Reject external filters before status, including initialized submodules.

    Disabling a filter and comparing its unconverted bytes could report a clean
    repository as dirty. Refuse instead; this never runs a filter to discover
    whether that filter has side effects. Unreferenced driver names do not
    block. Reserved attribute display values are checked conservatively.
    """
    pending, seen = [project], set()
    while pending:
        root = pending.pop()
        identity = _norm_path(root)
        if identity in seen:
            continue
        seen.add(identity)
        entries = git("ls-files", "--stage", "-z", root=root).stdout.split(bytes([0]))
        paths = set()
        for entry in entries:
            if not entry:
                continue
            metadata, path = entry.split(bytes([9]), 1)
            if metadata.startswith(b"160000 "):
                child = root / os.fsdecode(path)
                if (child / ".git").exists():
                    pending.append(child)
            else:
                paths.add(path)
        if not paths:
            continue
        data = bytes([0]).join(sorted(paths)) + bytes([0])
        attributes = git("check-attr", "-z", "--all", "--stdin", root=root, data=data).stdout.split(bytes([0]))
        drivers = {attributes[i + 1] for i in range(1, len(attributes) - 1, 3)
                   if attributes[i] == b"filter" and attributes[i + 1]}
        if not drivers:
            continue
        configured = git("config", "--includes", "--null", "--get-regexp",
                         r"^filter\..*\.(clean|process)$", root=root, missing_ok=True).stdout
        active = set()
        for item in configured.split(bytes([0])):
            key, separator, value = item.partition(bytes([10]))
            if separator and value:
                driver = key.rsplit(b".", 1)[0][len(b"filter."):]
                if driver in drivers:
                    active.add(driver.decode("utf-8", "replace"))
        if active:
            raise QueryError("internal", "Cannot guarantee a read-only dirty-state check with configured Git clean/process filters: "
                             + ", ".join(sorted(active)),
                             {"reason": "external_git_filter", "filters": sorted(active),
                              "repository": os.path.relpath(root, reference or project).replace(os.sep, "/")})


def git_status(project, warnings, depends_on=None):
    """Suppress optional writes and fsmonitor; refuse active external filters."""
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0")
    command = ["git", "-c", "core.fsmonitor=false"]

    def git(*args, root=project, data=None, missing_ok=False):
        try:
            result = subprocess.run([*command, *args], cwd=root, env=env, input=data,
                                    capture_output=True, timeout=5)
        except FileNotFoundError as exc:
            raise QueryError("internal", f"Git or repository path became unavailable: {exc}") from exc
        if result.returncode and not (missing_ok and result.returncode == 1):
            raise QueryError("internal", "git query failed: " + result.stderr.decode("utf-8", "replace").strip())
        return result

    try:
        repo = subprocess.run([*command, "rev-parse", "--is-inside-work-tree", "--show-toplevel", "--absolute-git-dir"], cwd=project,
                              env=env, capture_output=True, timeout=5)
        location = repo.stdout.splitlines()
        if repo.returncode or not location or location[0] != b"true":
            return {"is_repo": False, "dirty": False}
        if len(location) != 3:
            raise QueryError("internal", "Cannot decode Git working-tree location")
        root, git_dir = (Path(os.fsdecode(value)).resolve() for value in location[1:])
        if depends_on is not None:
            for path in (root, git_dir):
                try:
                    rel = os.path.relpath(path, project).replace(os.sep, "/") + "/"
                except ValueError:
                    warnings.append("Git metadata on another volume cannot be watched using a project-relative path")
                    continue
                if rel not in depends_on:
                    depends_on.append(rel)
        _guard_git_filters(root, git, reference=project)
        status = git("status", "--porcelain=v1", "-z", "--untracked-files=normal", root=root)
        return {"is_repo": True, "dirty": bool(status.stdout)}
    except FileNotFoundError:
        warnings.append("Git executable is unavailable; repository status could not be checked")
        return {"is_repo": False, "dirty": False}


def marketplace_source(warnings):
    """Attribute this plugin using on-disk registrations, never a CLI.

    No source is guessed from project declarations or unrelated registrations.
    Ambiguous registrations and unrecognized cache layouts are unknown.
    """
    matches = []

    def same(path):
        return isinstance(path, str) and bool(path) and _norm_path(path) == _norm_path(PLUGIN_ROOT)

    def source_value(source):
        return claude_source(source)

    try:
        cc = cc_config_dir()
        registry = read_object(cc / "plugins" / "known_marketplaces.json", "marketplace registry")
        installed = read_object(cc / "plugins" / "installed_plugins.json", "installed plugin registry")
        attributed = set()
        for plugin_id, rows in (installed.get("plugins") or {}).items():
            if plugin_id.startswith("core@") and isinstance(rows, list):
                if any(isinstance(row, dict) and same(row.get("installPath")) for row in rows):
                    attributed.add(plugin_id.split("@", 1)[1])
        for name, entry in registry.items():
            if not isinstance(entry, dict):
                continue
            location = entry.get("installLocation")
            direct = (isinstance(location, str) and
                      same(str(Path(_unc_strip(location)) / "plugins" / "core")))
            if direct or name in attributed:
                source = source_value(entry.get("source"))
                if source is not None:
                    matches.append(source)
    except (OSError, ValueError, TypeError, AttributeError, QueryError) as exc:
        warnings.append(f"Claude marketplace source could not be read: {exc}")
    try:
        home = codex_home()
        cfg = read_object(home / "config.toml", "Codex user configuration")
        for name, entry in (cfg.get("marketplaces") or {}).items():
            if not isinstance(entry, dict):
                continue
            raw, kind = entry.get("source"), entry.get("source_type")
            local = (kind == "local" and isinstance(raw, str)
                     and same(str(Path(_unc_strip(raw)) / "plugins" / "core")))
            cached = (PLUGIN_ROOT.parent.name == "core" and
                      same(str(home / "plugins" / "cache" / name / "core" / PLUGIN_ROOT.name)))
            # An upgrade runs the new version from the marketplace snapshot, before it is installed.
            snapshot = (kind == "git" and
                        same(str(home / ".tmp" / "marketplaces" / name / "plugins" / "core")))
            if (local or cached or snapshot) and kind in ("local", "git") and isinstance(raw, str):
                matches.append(codex_source(entry))
    except (OSError, ValueError, TypeError, AttributeError, QueryError) as exc:
        warnings.append(f"Codex marketplace source could not be read: {exc}")
    distinct = {}
    for source in matches:
        # Compare directory identities while retaining the registered raw value.
        identity = source_key(source)
        distinct.setdefault(identity, source)
    if len(distinct) == 1:
        return next(iter(distinct.values()))
    if len(distinct) > 1:
        warnings.append("Executing plugin matches multiple marketplace sources")
    return {"kind": "unknown", "source": None}


def layout_dependencies(paths):
    for kind in ("state", "memory", "archive"):
        rel = legacy_rel(kind) + "/"
        if rel not in paths:
            paths.append(rel)
    return paths


def project_dependencies(project, *, aggregate=True):
    paths = [ROOT_MARKER, (state_dir_of(project) / "setup-state.json").relative_to(project).as_posix(),
             ".claude/settings.json", ".claude/settings.local.json", ".codex/config.toml", "AGENTS.md"]
    if aggregate:
        paths.extend(("./", ".git", ".git/"))
    layout_dependencies(paths)
    if not (project / ROOT_MARKER).is_file():
        for parent in project.parents:
            paths.append(os.path.relpath(parent / ROOT_MARKER, project).replace(os.sep, "/"))
            if (parent / ROOT_MARKER).is_file():
                break
    return paths


def project_connection(project, warnings):
    connected = (project / ROOT_MARKER).is_file()
    cfg = read_object(project / ROOT_MARKER, ROOT_MARKER)
    name = cfg.get("project_name")
    if name is not None and not isinstance(name, str):
        raise QueryError("parse_error", "project_name must be a string or null",
                         {"file": ROOT_MARKER, "line": 1})
    paths = doctor.Paths(project)
    setup = None
    if connected:
        snapshot = doctor.inspect_setup_state(paths)
        setup = snapshot["data"]
        rel = paths.setup_state.relative_to(project).as_posix()
        warnings.extend(f"{rel}: {message}" for message in snapshot["warnings"])
    ancestor = None if connected else find_root(project.parent)
    return {"connected": connected,
            "layout": ("legacy" if doctor._has_legacy(paths) else "current") if connected else None,
            "project_name": name, "setup": setup, "support": project_support(project),
            "ancestor_project": ancestor.as_posix() if ancestor else None}


def project_status(project, warnings, depends_on=None):
    return {**project_connection(project, warnings),
            "directory_kind": classify_directory(project), "git": git_status(project, warnings, depends_on),
            "marketplace_source": marketplace_source(warnings)}


def main(argv=None, *, event_cache=None, emit=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    topic, depends_on = None, []
    try:
        parser = QueryParser(prog="workframe-query", add_help=False, allow_abbrev=False)
        parser.add_argument("topic", nargs="?")
        parser.add_argument("--project")
        parser.add_argument("--capabilities", action="store_true")
        parser.add_argument("--schema", default=SCHEMAS[-1])
        parser.add_argument("--gate", choices=("doctor", "close-check"))
        parser.add_argument("--days", type=int, choices=(7, 30, 90))
        parser.add_argument("--weeks")
        from _query_codex_trust import add_context_arguments, CONTEXT_FIELDS, TrustUsageError
        add_context_arguments(parser)
        args, unknown_args = parser.parse_known_args(argv)
        topic = "capabilities" if args.capabilities else args.topic
        project = None
        if args.project is not None and not args.capabilities:
            project = Path(_unc_strip(args.project)).resolve()
            if not project.is_dir():
                raise QueryError("bad_usage", "Project must be an existing directory", exit_code=2)
            depends_on = project_dependencies(project, aggregate=topic != "project-connection")
            if topic == "gates":
                depends_on = []
            elif topic == "board":
                from _query_board import dependencies
                depends_on = dependencies(project)
            elif topic == "modules":
                from _query_modules import dependencies
                depends_on = dependencies(project, {})
            elif topic == "stats":
                from _query_stats import dependencies
                depends_on = dependencies(project)
            if topic != "gates":
                layout_dependencies(depends_on)
        if unknown_args:
            raise QueryError("bad_usage", "Unrecognized arguments: " + " ".join(unknown_args), exit_code=2)
        if args.schema not in SCHEMAS:
            raise QueryError("schema_unsupported", f"Unsupported query schema: {args.schema}",
                             {"supported": list(SCHEMAS)})
        warnings = []
        now = datetime.now(timezone.utc).replace(microsecond=0)
        valid_until = None
        side_effects = []
        external_depends_on = []
        context_args = any(getattr(args, key) is not None for key in CONTEXT_FIELDS)
        if args.gate and args.topic != "gates":
            raise QueryError("bad_usage", "--gate is only valid for gates", exit_code=2)
        if context_args and not (args.topic == "codex-trust" or args.topic == "gates" and args.gate == "doctor"):
            raise QueryError("bad_usage", "Codex context arguments require codex-trust or gates --gate doctor", exit_code=2)
        if args.topic == "gates" and not args.gate:
            raise QueryError("bad_usage", "gates requires --gate", exit_code=2)
        if args.topic != "stats" and (args.days is not None or args.weeks is not None):
            raise QueryError("bad_usage", "--days and --weeks are only valid for stats", exit_code=2)
        if args.topic == "stats":
            from _query_stats import parse_weeks
            weeks = parse_weeks(args.weeks, error=QueryError)
        if args.capabilities:
            if args.topic is not None or args.project is not None:
                raise QueryError("bad_usage", "--capabilities takes no topic or project", exit_code=2)
            data = {"schemas": list(SCHEMAS), "topics": list(TOPICS)}
            from _query_stats import STATS_STREAM
            data["stats_stream"] = STATS_STREAM
            from _module_nav import SCHEMA as NAV_SCHEMA
            data["nav_refresh"] = {"schema": NAV_SCHEMA, "command": "module_init.py",
                                   "operations": ["refresh-index", "check", "convert-layout"]}
            from _query_codex_trust import STATES, RESTORE_SCHEMA
            from _query_codex_runtime import VERSION as trust_runtime_version
            data["codex_trust"] = {"states": list(STATES), "recovery": {
                "schema": RESTORE_SCHEMA, "command": "workframe-codex-trust", "operations": ["restore"],
                "automatic_write_supported": True, "supported_codex_versions": [trust_runtime_version]}}
        else:
            if args.topic not in KNOWN_TOPICS or not args.project:
                raise QueryError("bad_usage", "A known topic and --project are required", exit_code=2)
            if args.topic not in ("project-status", "project-connection"):
                if not (project / ROOT_MARKER).is_file():
                    raise QueryError("not_connected", "Directory is not connected to Workframe")
                if doctor._has_legacy(doctor.Paths(project)):
                    raise QueryError("legacy_layout", "Runtime data needs migration")
                if args.topic not in TOPICS:
                    raise QueryError("not_found", f"Topic is not implemented in this build: {args.topic}")
                read_object(project / ROOT_MARKER, ROOT_MARKER)
            def trust_result():
                from _query_codex_trust import query
                try:
                    result, metadata = query(project, vars(args), reader=read_object)
                except TrustUsageError as exc:
                    raise QueryError("bad_usage", str(exc), exit_code=2) from exc
                side_effects.extend(metadata.get("side_effects", []))
                external_depends_on.extend(metadata.get("external_depends_on", []))
                return result
            if args.topic == "gates":
                if not args.gate:
                    raise QueryError("bad_usage", "gates requires --gate", exit_code=2)
                from _query_gates import run
                cfg = read_object(project / ROOT_MARKER, ROOT_MARKER)
                def codex_check(ctx):
                    if not (project / ".codex/config.toml").is_file():
                        return [("info", "Codex support is not configured")]
                    result = trust_result()
                    level = {"ready": "ok", "needs_trust": "error", "not_installed": "error",
                             "disabled_by_user": "info", "version_unsupported": "warn", "unverified": "warn"}[result["state"]]
                    return [(level, item["message"]) for item in result["problems"]] or [(level, "Codex hooks are currently trusted")]
                data = run(project, args.gate, cfg,
                           guard_git=lambda path: git_status(path, warnings), codex_check=codex_check)
            elif args.topic == "codex-trust":
                data = trust_result()
            elif args.topic == "board":
                from _query_board import run
                data = run(project, warnings, depends_on, error=QueryError)
            elif args.topic == "modules":
                from _query_modules import dependencies, run
                cfg = read_object(project / ROOT_MARKER, ROOT_MARKER)
                depends_on = layout_dependencies(dependencies(project, cfg))
                data = run(project, cfg, warnings, depends_on, error=QueryError)
            elif args.topic == "stats":
                from _query_stats import run
                data, valid_until = run(project, warnings, depends_on, now, args.days or 30, weeks,
                                        error=QueryError, event_cache=event_cache)
            elif args.topic == "project-connection":
                data = project_connection(project, warnings)
            else:
                if args.gate:
                    raise QueryError("bad_usage", "--gate is only valid for gates", exit_code=2)
                data = project_status(project, warnings, depends_on)
        manifest = read_object(PLUGIN_ROOT / ".claude-plugin" / "plugin.json", "executing plugin manifest")
        version = manifest.get("version")
        if not isinstance(version, str) or not version:
            raise QueryError("internal", "Executing plugin manifest has no version")
        value = {"schema": args.schema, "topic": topic,
                 "generated_at": now.isoformat(timespec="seconds"),
                 "framework_version": version, "project": project.as_posix() if project else None,
                 "depends_on": depends_on, "warnings": warnings, "data": data}
        if side_effects:
            value["side_effects"] = sorted(set(side_effects))
        if valid_until is not None:
            value["valid_until"] = valid_until
        if external_depends_on:
            value["external_depends_on"] = external_depends_on
        code = 0
    except Exception as exc:
        if not isinstance(exc, QueryError):
            exc = QueryError("internal", f"{type(exc).__name__}: {exc}")
        error = {"code": exc.code, "message": str(exc)}
        if exc.detail is not None:
            error["detail"] = exc.detail
        value = {"schema": SCHEMAS[-1], "topic": topic, "depends_on": depends_on, "error": error}
        code = exc.exit_code
    if emit is None:
        print(json.dumps(value, ensure_ascii=False))
    else:
        emit(code, value)
    return code


if __name__ == "__main__":
    if sys.argv[1:] == ["--stats-stream"]:
        from _query_stats import serve_stats
        sys.exit(serve_stats(main, sys.stdin.buffer, sys.stdout))
    sys.exit(main())
