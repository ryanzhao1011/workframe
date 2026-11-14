#!/usr/bin/env python3
"""Offline real-0.167.2 acceptance probe, for an unrestricted review terminal.

Creates and retains a normal tempfile fixture, including separate user, Claude
and Codex homes, a local marketplace and a Git project. No credentials, login,
thread, resume, inference or ConPTY operation is used. Permission failures are
reported; the fixture location and sandbox/approval settings are not changed.

Recovery permits only a .hook-trust baseline bound to the plugin ID and
actual user configuration path by the pinned 0.167.2 formula, with exact plugin
content hash bytes, and the exact full keys' declaration/content fields. This probe
independently checks the filename, file effects and content readback.
Read-only queries never permit .hook-trust changes.

Run: python -B tools/probe_codex_trust_runtime.py --codex-exe <native executable>
     --out <JSON report>
"""

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "plugins/core"
SCRIPTS = CORE / "scripts"
sys.path.insert(0, str(SCRIPTS))


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
    with path.open("w", encoding="utf-8", newline="") as stream:
        stream.write(text)


def manifest(root):
    """Independent observation: all entries and file bytes, including .git."""
    from _harness import is_dir_link
    out = {}
    def walk(directory):
        for path in sorted(directory.iterdir()):
            name = path.relative_to(root).as_posix()
            if path.is_symlink() or is_dir_link(path):
                out[name] = ("link", os.readlink(path))
            elif path.is_dir():
                out[name + "/"] = ("directory",)
                walk(path)
            else:
                out[name] = ("file", hashlib.sha256(path.read_bytes()).hexdigest())
    walk(root)
    return out


def home_violations(before, after, trust_config=False, trust_storage=None):
    """Review-side allowance; deliberately does not import the adapter verdict."""
    name, content = trust_storage if trust_storage is not None else ("", "")
    allowed_storage = (re.fullmatch(r"\.hook-trust/[0-9a-f]{64}", name) is not None and
             re.fullmatch(r"sha256:[0-9a-f]{64}", content) is not None and
             (name not in before or before[name][0] == "file") and
             after.get(name) == ("file", hashlib.sha256(content.encode()).hexdigest()) and
             before.get(".hook-trust/") in (None, ("directory",)) and after.get(".hook-trust/") == ("directory",))
    violations = []
    for path in sorted(set(before) | set(after)):
        if before.get(path) == after.get(path):
            continue
        entries = (before.get(path), after.get(path))
        if any(entry and entry[0] == "link" for entry in entries):
            violations.append(path)
            continue
        if trust_config and path == "config.toml":
            continue
        if allowed_storage and path in (name, ".hook-trust/"):
            continue
        if path == "log/" or path.startswith("log/") or re.fullmatch(r"logs_[^/]+\.sqlite(?:-wal|-shm)?", path):
            continue
        if path in ("tmp/", "tmp/arg0-windows/", "tmp/arg0/", ".tmp/", ".sandbox/"):
            continue
        if re.match(r"(?:tmp/arg0(?:-windows)?/codex-arg0[^/]+|\.tmp/git-[^/]+)(?:/|$)", path):
            continue
        if re.fullmatch(r"\.sandbox/sandbox\.[^/]+\.log", path):
            continue
        if path.endswith(".sqlite-shm"):
            database = path[:-4]
            if (before.get(database, (None,))[0] == "file" and before.get(database) == after.get(database)
                    and before.get(database + "-wal") == after.get(database + "-wal")):
                continue
        violations.append(path)
    return violations


def storage_expectation(config_file, plugin_id, pending):
    """Independent formula from rust-v0.167.2 legacy_trust_path."""
    parts = (str(config_file).encode("utf-8"), plugin_id.encode("utf-8"))
    filename = hashlib.sha256(b"".join(struct.pack("<Q", len(part)) + part for part in parts)).hexdigest()
    contents = {value.partition("/")[2] for value in pending.values()}
    assert len(contents) == 1 and re.fullmatch(r"sha256:[0-9a-f]{64}", next(iter(contents)))
    return ".hook-trust/" + filename, next(iter(contents))


def config_matches(before, after, pending):
    """Independent complete-write oracle: RPC pair becomes two TOML values."""
    import tomllib
    def typed(value):
        if isinstance(value, dict):
            return ("table", tuple((key, typed(value[key])) for key in sorted(value)))
        if isinstance(value, list):
            return ("array", tuple(typed(item) for item in value))
        return (type(value).__name__, value)
    try:
        expected = copy.deepcopy(tomllib.loads(before.decode("utf-8-sig")))
        for key, current_hash in pending.items():
            declaration, content = current_hash.split("/")
            if not declaration or not re.fullmatch(r"sha256:[0-9a-f]{64}", content):
                return False
            state = expected.setdefault("hooks", {}).setdefault("state", {}).setdefault(key, {})
            state.update(trusted_hash=declaration, trusted_content_hash=content)
        return typed(expected) == typed(tomllib.loads(after.decode("utf-8-sig")))
    except (UnicodeError, ValueError, TypeError, AttributeError):
        return False


def probe(args, result):
    import _codex_appserver as cas
    import codex_roles
    from _query_codex_trust import resolve_entry
    from _state_io import state_dir_of
    base = Path(tempfile.mkdtemp(prefix="wf-codex-trust-e2e-"))
    result["fixture_root"] = str(base)
    base = base.resolve()
    result["resolved_fixture_root"] = str(base)
    home, user, project, market = (base / name for name in ("codex-home", "user-home", "project", "market"))
    claude = user / ".claude"
    for directory in (home, user, project, claude):
        directory.mkdir(parents=True)
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("CODEX_", "OPENAI_", "CHATGPT_", "GIT_"))
           and key not in ("ACCESS_TOKEN", "WF_CODEX_EXE")}
    env.update(CODEX_HOME=str(home), HOME=str(user), USERPROFILE=str(user),
               CLAUDE_CONFIG_DIR=str(claude), XDG_CONFIG_HOME=str(user / ".config"),
               PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1", GIT_OPTIONAL_LOCKS="0",
               GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", GIT_ATTR_NOSYSTEM="1")
    exe = resolve_entry(args.codex_exe, env)
    if not exe:
        raise RuntimeError("Cannot resolve the explicitly requested native Codex executable")
    def run(label, command, cwd=project, expected=0):
        response = subprocess.run(list(map(str, command)), cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                  capture_output=True, timeout=45)
        record = {"label": label, "argv": list(map(str, command)), "exit": response.returncode,
                  "stdout": response.stdout.decode("utf-8", "replace"), "stderr": response.stderr.decode("utf-8", "replace")}
        result.setdefault("commands", []).append(record)
        if expected is not None and response.returncode != expected:
            raise AssertionError("Unexpected exit for " + label + ": " + str(response.returncode))
        return record
    result["version"] = run("version", [exe, "--version"])["stdout"].strip()
    if result["version"] != "codex-cli 0.167.2":
        raise RuntimeError("This probe requires exactly codex-cli 0.167.2")
    source = market / "plugins/core"
    shutil.copytree(CORE, source, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    put(market / ".agents/plugins/marketplace.json", {"name": "runtime-probe", "plugins": [
        {"name": "core", "source": {"source": "local", "path": "./plugins/core"},
         "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"}, "category": "Productivity"}]})
    put(home / "config.toml", 'cli_auth_credentials_store="file"\n[features]\nhooks=true\nplugins=true\n'
        + "[projects." + json.dumps(str(project)) + ']\ntrust_level="trusted"\n')
    run("register-fixture-market", [exe, "plugin", "marketplace", "add", market, "--json"])
    installed = json.loads(run("install-fixture-core", [exe, "plugin", "add", "core@runtime-probe", "--json"])["stdout"])
    plugin = Path(installed["installedPath"])
    with cas.AppServer(exe=exe, codex_home=home, cwd=project, env=env, timeout=20) as server:
        server.config_batch_write([{"keyPath": 'plugins."core@runtime-probe".enabled', "mergeStrategy": "upsert", "value": False}])
        from workframe_door import _unc_strip
        config = server.config_read(True, cwd=str(project))
        layers = [row for row in config["layers"] if row["name"].get("type") == "user" and row["name"].get("profile") is None]
        assert len(layers) == 1
        user_config_file = Path(_unc_strip(layers[0]["name"]["file"]))
        assert user_config_file == home / "config.toml"
        result["user_config_file"] = str(user_config_file)
    put(project / ".workframe-config.json", {"project_name": "Runtime fixture"})
    put(project / ".codex/config.toml", codex_roles.BLOCK_BEGIN + '\n[plugins."core@runtime-probe"]\nenabled=true\n' + codex_roles.BLOCK_END + "\n")
    put(project / "README.md", "Runtime fixture\n")
    put(claude / "settings.json", {})
    state_dir_of(project).mkdir(parents=True)
    run("git-init", ["git", "init", "-q"])
    run("git-add", ["git", "add", "--", ".workframe-config.json", ".codex/config.toml", "README.md"])
    run("git-commit", ["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                       "-c", "commit.gpgsign=false", "commit", "-qm", "fixture"])
    stamp = (project / "README.md").stat().st_mtime_ns + 2_000_000_000
    os.utime(project / "README.md", ns=(stamp, stamp))
    trees = {"project": project, "state": state_dir_of(project), "plugin": plugin, "claude": claude, "codex": home}
    options = ["--project", project, "--codex-exe", exe, "--codex-home", home, "--codex-cwd", project]
    def invoke(label, restore=False, snapshot_id=None, source_identity=None, expected=0, pending=None):
        before = {key: manifest(path) for key, path in trees.items()}
        config_before = (home / "config.toml").read_bytes()
        storage = storage_expectation(user_config_file, "core@runtime-probe", pending) if pending else None
        if restore:
            command = [sys.executable, "-B", plugin / "scripts/workframe_codex_trust.py", "restore", *options,
                       "--expect-snapshot", snapshot_id, "--expect-source", json.dumps(source_identity)]
        else:
            command = [sys.executable, "-B", plugin / "scripts/workframe_query.py", "codex-trust", *options]
        raw = run(label, command, expected=None)
        after = {key: manifest(path) for key, path in trees.items()}
        checks = []
        for key in trees:
            changes = [{"path": path, "before": before[key].get(path), "after": after[key].get(path)}
                       for path in sorted(set(before[key]) | set(after[key])) if before[key].get(path) != after[key].get(path)]
            forbidden = (home_violations(before[key], after[key], trust_config=bool(pending), trust_storage=storage) if key == "codex"
                         else [row["path"] for row in changes])
            checks.append({"tree": key, "changes": changes, "violations": forbidden})
        config_ok = (config_matches(config_before, (home / "config.toml").read_bytes(), pending) if pending else
                     config_before == (home / "config.toml").read_bytes())
        storage_readback = None
        if storage:
            path = home / storage[0]
            from _harness import is_dir_link
            matches = (not any(part.is_symlink() or is_dir_link(part) for part in (path.parent, path)) and
                       path.is_file() and path.read_bytes() == storage[1].encode("utf-8"))
            storage_readback = {"path": storage[0], "expected_content_hash": storage[1],
                                "existed_before": storage[0] in before["codex"],
                                "existed_after": storage[0] in after["codex"], "content_matches": matches}
        result.setdefault("stages", []).append({"label": label, "files": checks, "config_scope_ok": config_ok,
                                               "trust_storage": storage_readback})
        assert not any(row["violations"] for row in checks), label + " changed a protected file"
        assert config_ok, label + " changed configuration beyond the exact target hashes"
        assert storage_readback is None or storage_readback["content_matches"], label + " failed trust-baseline readback"
        assert raw["exit"] == expected, label + " has an unexpected exit; inspect the recorded raw response"
        value = json.loads(raw["stdout"])
        assert raw["stderr"] == "", label + " emitted stderr"
        assert value["project"] == project.resolve().as_posix()
        assert value["side_effects"] == ["codex_app_server_logs"]
        return value["data"]
    initial = invoke("needs-trust")
    assert initial["state"] == "needs_trust" and initial["recovery"]["eligible"], initial
    assert initial["effective"]["project_trusted"] is True and initial["effective"]["plugin_enabled"] is True
    sid, identity = initial["snapshot_id"], initial["plugin"]["source"]
    refused = invoke("reject-snapshot", True, "sha256:" + "f" * 64, identity, expected=1)
    assert refused["error"]["code"] == "snapshot_mismatch" and refused["write_outcome"] == "none"
    wrong_source = dict(identity, marketplace_id="another-market")
    refused = invoke("reject-source", True, sid, wrong_source, expected=1)
    assert refused["error"]["code"] == "source_mismatch" and refused["write_outcome"] == "none"
    pending = {hook["key"]: hook["current_hash"] for hook in initial["hooks"] if hook["status"] != "trusted"}
    restored = invoke("restore", True, sid, identity, pending=pending)
    assert (restored["result"], restored["state_after"], restored["write_outcome"]) == ("restored", "ready", "confirmed")
    assert set(restored["attempted_keys"]) == set(pending)
    assert set(restored["verified_keys"]) == {hook["key"] for hook in initial["hooks"]}
    assert not restored["remaining_keys"]
    ready = invoke("ready")
    assert ready["state"] == "ready" and ready["snapshot_id"] == sid and ready["change_token"] == initial["change_token"]
    unchanged = invoke("unchanged", True, sid, identity)
    assert unchanged["result"] == "unchanged" and not unchanged["attempted_keys"] and unchanged["write_outcome"] == "none"
    result["result"] = "passed"


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-exe", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    result = {"result": "not_run", "no_login": True, "no_inference": True,
              "resume": "not_run_requires_user_account", "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    try:
        if sys.version_info < (3, 11):
            raise RuntimeError("The Codex trust protocol requires Python 3.11 or later")
        probe(args, result)
    except PermissionError as exc:
        result.update(result="blocked_permission", error=str(exc))
    except Exception as exc:
        result.update(result="failed", error=type(exc).__name__ + ": " + str(exc))
    put(args.out, result)
    print(json.dumps({"result": result["result"], "report": str(args.out)}, ensure_ascii=True))
    return 0 if result["result"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
