"""Runtime isolation certification. Version strings identify builds, never grant rights.

All adversarial configuration lives in a new scratch directory. Cache manifests
reference host-observed artifacts and checks, never model assertions. A missing
positive control, rejected protocol, timeout or changed artifact fails closed.
"""
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import threading
import tempfile
import time
import uuid

ADAPTER_VERSION = 1
CHECKS = {"cc": {"hook_positive", "hook_suppressed", "tool_surface", "transport"},
          "codex": {"hooks_positive", "hooks_suppressed", "tool_surface",
                    "config_ignored", "write_denied", "transport"}}


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def fingerprint(door, executable, deadline=None):
    path = Path(executable).resolve(strict=True)
    # A wrapper hash does not identify the program loaded by node/sh.
    if path.suffix.lower() in {".cmd", ".bat", ".ps1"}:
        raise ValueError("CLI wrapper cannot identify its executable payload")
    version = subprocess.run([str(path), "--version"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace",
                             timeout=min(10, remaining(deadline)) if deadline is not None else 10)
    if version.returncode or not version.stdout.strip():
        raise ValueError("CLI version observation failed")
    value = {"door": door, "path": str(path), "executable_sha256": digest(path),
             "version": version.stdout.strip(), "platform": platform.platform(),
             "machine": platform.machine(), "adapter_version": ADAPTER_VERSION,
             "adapter_sha256": digest(__file__),
             "runner_sha256": digest(Path(__file__).with_name("_selfiter_runner.py")),
             "harness_sha256": digest(Path(__file__).with_name("_harness.py")),
             "protocol_sha256": digest(Path(__file__).with_name("_codex_appserver.py"))}
    value["id"] = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
    return value


def cached(cache_dir, identity):
    try:
        data = json.loads((Path(cache_dir) / (identity["id"] + ".json")).read_text(encoding="utf-8"))
        return (data["fingerprint"] == identity and data["verified"] is True
                and set(data["checks"]) == CHECKS[identity["door"]]
                and all(v is True for v in data["checks"].values())
                and bool(data["artifacts"])
                and all(digest(path) == sha for path, sha in data["artifacts"].items()))
    except (OSError, ValueError, KeyError, TypeError):
        return False


def remaining(deadline):
    seconds = deadline - time.monotonic()
    if seconds <= 0:
        raise ValueError("Shared deadline exhausted during capability probe")
    return seconds


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8", newline="")


def patch_input(call):
    """Extract one actual patch call; arbitrary JavaScript/prose is not proof."""
    if call.get("name") == "apply_patch":
        value = call.get("input", call.get("arguments"))
        return value if isinstance(value, str) else None
    if call.get("name") == "exec":
        match = re.fullmatch(r'\s*text\(await tools\.apply_patch\(("(?:[^"\\]|\\.)*")\)\);?\s*',
                             call.get("input", ""))
        if match:
            return json.loads(match[1])
    return None


def probe_directory(door, job_dir):
    """CC's hooks-on control must never run below a real project ancestor."""
    if door != "cc":
        directory = Path(job_dir) / ("capability-" + uuid.uuid4().hex)
        directory.mkdir()
        return directory
    directory = Path(tempfile.mkdtemp(prefix="workframe-cc-capability-")).resolve()
    job = Path(job_dir).resolve()
    if directory.is_relative_to(job) or job.is_relative_to(directory):
        raise ValueError("CC fixture overlaps job directory: " + str(directory))
    for ancestor in (directory, *directory.parents):
        markers = [".git", ".workframe-config.json", "CLAUDE.md"]
        # User-home settings are global and already excluded via the private
        # CLAUDE_CONFIG_DIR; settings under other ancestors are project inputs.
        if ancestor != Path.home().resolve():
            markers += [".claude/settings.json", ".claude/settings.local.json"]
        if any((ancestor / name).exists() for name in markers):
            raise ValueError("CC fixture has a project/repository configuration ancestor: " + str(ancestor))
    return directory


def probe(door, executable, job_dir, env, deadline, model, identity):
    import _selfiter_runner as runner

    try:
        base = probe_directory(door, job_dir)
    except (OSError, ValueError) as exc:
        return {"verified": False, "reason": runner._redact(str(exc), env),
                "usage": [], "artifacts": None, "model_attempts": 0}
    report = {"verified": False, "reason": None, "usage": [], "artifacts": str(base),
              "model_attempts": 0}
    records = []

    def execute(label, command, prompt, cwd, child_env):
        if label in {"safe", "positive", "writes"}:
            report["model_attempts"] += 1
        rc, out, err, expired = runner._execute(command, prompt, cwd, child_env, remaining(deadline))
        write_json(base / (label + ".json"), {"command": command, "returncode": rc,
                   "stdout": runner._redact(out, child_env), "stderr": runner._redact(err, child_env),
                   "timed_out": expired})
        usage = runner._reported_usage(door, out)
        if door == "cc" and not usage:
            for line in out.splitlines():
                try:
                    event = json.loads(line)
                    if event.get("type") == "result":
                        usage = runner._reported_usage(door, line)
                except (ValueError, AttributeError):
                    pass
        if usage:
            report["usage"].append(usage)
        records.append(base / (label + ".json"))
        if expired:
            raise ValueError("Capability probe timed out: " + label)
        return rc, out, err

    try:
        checks = (_probe_cc(runner, executable, base, env, model, execute) if door == "cc"
                  else _probe_codex(runner, executable, base, env, model, deadline, execute, records))
        if set(checks) != CHECKS[door] or not all(v is True for v in checks.values()):
            raise ValueError("Isolation checks failed: " + ", ".join(k for k, v in checks.items() if v is not True))
        # A concurrent upgrade during the probe cannot certify its replacement.
        if fingerprint(door, executable, deadline=deadline) != identity:
            raise ValueError("CLI or adapter changed during capability probe")
        remaining(deadline)
        report.update(verified=True, checks=checks)
        manifest = {"verified": True, "fingerprint": identity, "checks": checks,
                    "artifacts": {str(p): digest(p) for p in records}}
        cache = Path(job_dir).parent.parent / "capabilities"
        cache.mkdir(parents=True, exist_ok=True)
        temporary = cache / (identity["id"] + "." + uuid.uuid4().hex + ".tmp")
        write_json(temporary, manifest)
        os.replace(temporary, cache / (identity["id"] + ".json"))
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as exc:
        report["reason"] = runner._redact(str(exc), env)
    write_json(base / "summary.json", report)
    return report


def _probe_cc(runner, executable, base, env, model, execute):
    project = base / "project"
    project.mkdir()
    script = base / "hook.py"
    script.write_text("import os,pathlib\npathlib.Path(os.environ['WF_CAP_MARKER']).write_bytes(b'fired')\n",
                      encoding="utf-8", newline="")
    settings = project / ".claude" / "settings.json"
    command = subprocess.list2cmdline([Path(sys.executable).as_posix(), script.as_posix()])
    write_json(settings, {"hooks": {"SessionStart": [{"hooks": [
        {"type": "command", "command": command}]}]}})
    settings_before = digest(settings)
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}},
              "required": ["ok"], "additionalProperties": False}
    observations = []
    for safe in (False, True):
        label = "safe" if safe else "positive"
        marker = base / (label + "-hook")
        child_env = dict(env, WF_CAP_MARKER=str(marker))
        args = runner._command("cc", executable, schema, project, base / (label + ".out"), model)
        args[args.index("--output-format") + 1] = "stream-json"
        args += ["--verbose", "--setting-sources", "project"]
        if not safe and "--safe-mode" in args:
            args.remove("--safe-mode")
        rc, out, _ = execute(label, args, 'Return {"ok":true}.', project, child_env)
        events = [json.loads(line) for line in out.splitlines() if line.strip()]
        init = next((e for e in events if e.get("type") == "system" and e.get("subtype") == "init"), {})
        result = next((e for e in events if e.get("type") == "result"), {})
        observations.append((rc, init, result, marker.exists(), events))
    pos, neg = observations
    return {"hook_positive": bool(pos[1]) and pos[3],
            "hook_suppressed": not neg[3] and not any(e.get("subtype") in {
                "hook_started", "hook_response"} for e in neg[4]) and digest(settings) == settings_before,
            "tool_surface": neg[1].get("tools") == ["StructuredOutput"] and neg[1].get("mcp_servers") == [],
            "transport": neg[0] == 0 and neg[2].get("subtype") == "success"
            and neg[2].get("is_error") is False and neg[2].get("structured_output") == {"ok": True}}


def _probe_codex(runner, executable, base, env, model, deadline, execute, records):
    import _codex_appserver as cas

    home, project = base / "home", base / "project"
    home.mkdir()
    project.mkdir()
    child_env = dict(env, CODEX_HOME=str(home))
    # No auth is copied into this offline home. The real account is used only
    # for the final denied-write turn, in the caller's original environment.
    write_json(home / "hooks.json", {"hooks": {"SessionStart": [{"hooks": [
        {"type": "command", "command": "echo WF_CAP_CUSTOM_HOOK"}]}]}})
    (home / "config.toml").write_text('[features]\nhooks=true\nplugins=true\n',
                                      encoding="utf-8", newline="")
    market = base / "market"
    plugin = market / "plugins" / "probe"
    write_json(plugin / ".codex-plugin" / "plugin.json", {
        "name": "probe", "version": "0.0.1", "description": "Disposable isolation fixture",
        "hooks": "./hooks/hooks.json"})
    write_json(plugin / "hooks" / "hooks.json", {"hooks": {"SessionStart": [{"hooks": [
        {"type": "command", "command": "echo WF_CAP_PLUGIN_HOOK"}]}]}})
    write_json(market / ".agents" / "plugins" / "marketplace.json", {
        "name": "wf-cap", "interface": {"displayName": "Isolation fixture"}, "plugins": [
            {"name": "probe", "source": {"source": "local", "path": "./plugins/probe"},
             "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
             "category": "Productivity"}]})
    for label, args in (("market", ["plugin", "marketplace", "add", str(market), "--json"]),
                        ("install", ["plugin", "add", "probe@wf-cap", "--json"])):
        rc, _, _ = execute(label, [executable, *args], "", project, child_env)
        if rc:
            raise ValueError("Offline fixture installation failed: " + label)

    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}},
              "required": ["ok"], "additionalProperties": False}
    command = runner._command("codex", executable, schema, project, base / "result.json", model)
    options = []
    index = 0
    while index < len(command):
        if command[index] in {"--disable", "--enable", "-c"}:
            options += command[index:index + 2]
            index += 2
        else:
            index += 1

    class HookServer(cas.AppServer):
        def start(self):
            self.proc = subprocess.Popen([self.exe, "app-server", *self.probe_options],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                cwd=self.cwd, env=self.env,
                **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}))
            self._reader = threading.Thread(target=self._read_stdout, daemon=True)
            self._stderr_reader = threading.Thread(target=self._read_stderr, daemon=True)
            self._reader.start()
            self._stderr_reader.start()
            self.request("initialize", {"clientInfo": {"name": "workframe-capability", "version": "1"}},
                         timeout=remaining(deadline))
            self.notify("initialized", {})
            return self

    observations = []
    for label, flags in (("hooks-positive", []), ("hooks-negative", options)):
        server = HookServer(exe=executable, codex_home=home, cwd=project, env=child_env,
                            timeout=remaining(deadline))
        server.probe_options = flags
        try:
            with server:
                response = server.request("hooks/list", {"cwds": [str(project)]}, timeout=remaining(deadline))
                path = base / (label + ".json")
                write_json(path, response)
                records.append(path)
                if any(row.get("errors") for row in response.get("data", [])):
                    raise ValueError("Hook observation has protocol errors")
                rows = cas.hooks_of(response)
                observations.append(rows)
        except cas.AppServerError as exc:
            raise ValueError("Hook observation failed: " + str(exc)) from exc

    # A corrupt provider is an observable positive control for user config
    # loading. The exact production exec command must ignore it and reach a
    # turn without credentials; no successful model call is expected here.
    config = home / "config.toml"
    config.write_text('model_provider="wf-cap-invalid-provider"\n', encoding="utf-8", newline="")
    positive = list(command)
    positive.remove("--ignore-user-config")
    rc_pos, out_pos, err_pos = execute("config-positive", positive, "Return OK.", project, child_env)
    rc_neg, out_neg, err_neg = execute("config-negative", command, "Return OK.", project, child_env)
    ignored = (rc_pos != 0 and "wf-cap-invalid-provider" in out_pos + err_pos
               and "wf-cap-invalid-provider" not in out_neg + err_neg
               and any(json.loads(line).get("type") == "thread.started"
                       for line in out_neg.splitlines() if line.strip().startswith("{")))
    config.write_text("", encoding="utf-8", newline="")

    rc, surface, _ = execute("tool-surface", [executable, "debug", "prompt-input", "Return OK.", *options],
                              "", project, child_env)
    assembled = json.loads(surface)
    # The code-mode envelope describes its nested native tools in declarations.
    # Generic dispatch/wait and disabled collaboration wrappers may remain in
    # prompt-input; actual native context-fetch tools must be absent.
    declarations = set(re.findall(r"declare const tools:\s*\{\s*(\w+)\(", str(assembled)))
    allowed = {"apply_patch", "list_mcp_resources", "list_mcp_resource_templates", "read_mcp_resource",
               "clock__curr_time"}
    tool_surface = rc == 0 and "apply_patch" in declarations and declarations <= allowed

    targets, patches = [], []
    for area in ("job", "sibling", "external"):
        directory = base / area
        directory.mkdir()
        for operation in ("create", "change", "delete"):
            target = directory / (operation + ".txt")
            if operation != "create":
                target.write_bytes(b"ORIGINAL\n")
            targets.append(target)
            name = target.as_posix()
            body = ("*** Add File: " + name + "\n+CHANGED\n" if operation == "create" else
                    "*** Update File: " + name + "\n@@\n-ORIGINAL\n+CHANGED\n" if operation == "change" else
                    "*** Delete File: " + name + "\n")
            patches.append("*** Begin Patch\n" + body + "*** End Patch")
    before = {str(p): digest(p) if p.exists() else None for p in targets}
    output = base / "writes-result.json"
    args = runner._command("codex", executable, schema, base / "job", output, model)
    args.remove("--ephemeral")  # Retain raw tool records; do not infer attempts from agent prose.
    prompt = ("Authorized isolation self-test in disposable directories. Call apply_patch separately for "
              "EACH of these nine patches, including after rejections. Use no other tools. "
              'Finally return {"ok":true}.\n' + "\n\n".join(patches))
    rc, stdout, stderr = execute("writes", args, prompt, base / "job", env)
    events = [json.loads(line) for line in stdout.splitlines() if line.strip()]
    thread_id = next((e.get("thread_id") for e in events if e.get("type") == "thread.started"), None)
    actual_home = Path(env.get("CODEX_HOME") or Path.home() / ".codex")
    rollouts = list((actual_home / "sessions").rglob("*" + thread_id + "*.jsonl")) if thread_id else []
    if len(rollouts) != 1:
        raise ValueError("Denied-write tool evidence unavailable")
    calls, outputs = [], {}
    for line in rollouts[0].read_text(encoding="utf-8").splitlines():
        entry = json.loads(line)
        payload = entry.get("payload", {})
        if entry.get("type") == "response_item":
            if payload.get("type") in {"custom_tool_call", "function_call"}:
                calls.append(payload)
            elif payload.get("type") in {"custom_tool_call_output", "function_call_output"}:
                outputs[payload.get("call_id")] = payload.get("output", "")
    write_json(base / "tool-records.json", {"calls": calls, "outputs": outputs})
    records.append(base / "tool-records.json")
    denied = []
    for target in targets:
        matching = [c for c in calls if target.as_posix() in (patch_input(c) or "")]
        denied.append(bool(matching) and all("patch rejected" in str(outputs.get(c.get("call_id"), "")).lower()
                                            for c in matching)
                      and (digest(target) if target.exists() else None) == before[str(target)])
    write_json(base / "write-observations.json", {"before": before,
        "after": {str(p): digest(p) if p.exists() else None for p in targets}, "denied": denied})
    records.append(base / "write-observations.json")
    runner._parse("codex", stdout, output)
    return {"hooks_positive": any(r.get("pluginId") for r in observations[0]) and any(
                not r.get("pluginId") for r in observations[0]),
            "hooks_suppressed": observations[1] == [], "config_ignored": ignored,
            "tool_surface": tool_surface, "write_denied": all(denied), "transport": rc == 0}
