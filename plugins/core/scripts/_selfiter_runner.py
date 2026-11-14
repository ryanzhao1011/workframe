#!/usr/bin/env python3
"""Transport for frozen-evidence analysis, never an operation executor.

The controller owns scheduling, schema validation, evidence freezing and writes.
``prompt`` must contain the evidence itself: tools are not a context-fetch path.
An unverified CLI is refused before model startup. Help text, read-only sandboxing,
or a model's claim of isolation are not capability evidence.

No user settings, auth or hook trust are written. Runtime isolation evidence is
cached against the executable, platform and adapter source, never a version list.
"""
import json
import math
import os
import re
import shutil
import signal
import subprocess
import time
import tomllib
import uuid
from pathlib import Path

import _codex_appserver as cas
import _selfiter_capability as capability
import _harness as harness_env

MAX_OUTPUT_BYTES = 4 * 1024 * 1024


def _resolve_executable(door):
    """Reuse npm vendor resolution, including Windows .cmd discovery via which."""
    return cas.find_codex_exe() if door == "codex" else shutil.which("claude")


def _capability(door, executable):
    """Read-only build check against completed real isolation probes.

    No environment switch or job-supplied file can assert verification. In
    particular WORKFRAME_SELFITER_JOB is a recursion marker, not authorization.

    Windows / Claude Code 2.1.322 was probed on 2026-11-11 UTC using the
    --probe-cc and --probe-cc-hooks entry points: existing login produced a
    successful structured result and real usage; init exposed StructuredOutput
    alone and zero MCP servers. A project SessionStart hook wrote a canary under
    normal print mode; safe-mode suppressed both the canary and hook events,
    without changing the fixture configuration. Other builds/platforms need the
    same evidence before being admitted; version help alone never grants access.

    Windows / codex-cli 0.166.0 was probed on 2026-11-11 UTC with the original
    login and configured gpt-6-luna: exec returned structured JSON and usage.
    Nine actual apply_patch create/change/delete attempts across job, sibling
    project and external scratch were rejected, with unchanged file bytes.
    An enabled MCP canary emitted mcp_tool_call (denied by approval=never);
    disabling it removed that call. In an unauthenticated fixture installed via
    the native marketplace, hooks/list showed trusted custom and plugin hooks:
    hooks-off removed both, plugins-off left custom, both-off removed both, and
    restoring flags restored both. Command isolation ignores user config and
    disables hooks/plugins/MCP apps; read-only alone is not the isolation proof.
    Built-in apply_patch remains routable but cannot write. Windows detached,
    environment and process-tree cleanup were also exercised using real OS
    processes; mocked unit results do not establish capability.
    """
    # Historical probe notes above explain the adapter's original design; they
    # are not authorization for any build. Only the runtime cache grants access.
    try:
        capability.fingerprint(door, executable)
        return "requires_probe", "CLI isolation requires runtime proof"
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return "unavailable", str(exc)


def _configured_codex_model():
    """Read model selection only; never copy auth or mutate the original home.

    An explicit model argument takes precedence at the caller. Profile overlays
    are read from the same home, so ignoring user customization does not silently
    substitute a different configured model. Custom providers need separate
    capability evidence and are not reconstructed from credentials here.
    """
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    path = home / "config.toml"
    if not path.exists():
        return None
    config = tomllib.loads(path.read_text(encoding="utf-8"))
    profile = config.get("profile")
    if profile:
        if not isinstance(profile, str) or Path(profile).name != profile or profile in {".", ".."}:
            raise ValueError("invalid Codex profile name")
        legacy = config.get("profiles", {}).get(profile, {})
        config.update(legacy)
        overlay = home / (profile + ".config.toml")
        if overlay.exists():
            config.update(tomllib.loads(overlay.read_text(encoding="utf-8")))
    if config.get("model_provider", "openai") != "openai":
        raise ValueError("custom Codex provider isolation has not been verified")
    model = config.get("model")
    if model is not None and (not isinstance(model, str) or not model.strip()):
        raise ValueError("invalid configured Codex model")
    return model


def capability_status(door, cache_dir=None, deadline=None):
    """Read-only preflight before scheduling reservation; never starts a model.

    Return capability/reason/executable. ``run_analysis`` repeats this check so
    a changed CLI between scheduling and invocation cannot inherit an old grant.
    Missing executables, invalid configuration and unproven builds fail closed.
    """
    result = {"capability": "unavailable", "reason": None, "executable": None,
              "fingerprint": None}
    try:
        if door not in {"cc", "codex"}:
            raise ValueError("door must be cc or codex")
        result["executable"] = _resolve_executable(door)
        if not result["executable"]:
            raise ValueError("CLI executable not found for " + door)
        if door == "codex":
            _configured_codex_model()
        result["fingerprint"] = capability.fingerprint(door, result["executable"], deadline=deadline)
        if cache_dir is None:
            from _state_io import state_dir_of
            cache_dir = state_dir_of(Path.cwd()) / "selfiter" / "capabilities"
        verified = capability.cached(cache_dir, result["fingerprint"])
        result["capability"] = "verified" if verified else "requires_probe"
        result["reason"] = None if verified else "CLI isolation requires runtime proof"
    except (OSError, ValueError, TypeError, subprocess.SubprocessError) as exc:
        result["reason"] = _redact(str(exc), dict(os.environ))
    return result


def _probe_capability(door, executable, job_dir, env, deadline, model, fingerprint):
    return capability.probe(door, executable, job_dir, env, deadline, model, fingerprint)


def _configured_cc_model():
    """Read only the original user model selection before isolating CC config."""
    original = harness_env.cc_config_dir()
    path = original / "settings.json"
    if not path.exists():
        return None
    settings = _json_object(path.read_text(encoding="utf-8"))
    if "model" not in settings:
        return None
    model = settings["model"]
    if not isinstance(model, str) or not model.strip():
        raise ValueError("invalid configured Claude model")
    return model


def _cc_environment(job_dir, env):
    """Keep CC's global config/cache writes in the job, with auth only in env.

    Native CC updates its global JSON even in print/safe mode. Read the existing
    OAuth access token without copying credentials or refreshing the user's
    credential file. Expired/unavailable credentials fail closed at the CLI.
    """
    isolated = dict(env)
    original = harness_env.cc_config_dir(env)
    if not harness_env.cc_oauth_token(isolated) and not isolated.get("ANTHROPIC_API_KEY"):
        credentials = original / ".credentials.json"
        if credentials.is_file():
            data = _json_object(credentials.read_text(encoding="utf-8"))
            oauth = data.get("claudeAiOauth", {})
            token = oauth.get("accessToken") if isinstance(oauth, dict) else None
            if isinstance(token, str) and token:
                isolated["CLAUDE_CODE_OAUTH_TOKEN"] = token
    config = Path(job_dir) / ("cc-home-" + uuid.uuid4().hex)
    config.mkdir()
    isolated.update(CLAUDE_CONFIG_DIR=str(config), DISABLE_AUTOUPDATER="1",
                    CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1")
    return isolated


def _command(door, executable, schema, job_dir, output_path, model):
    if door == "cc":
        if model is None:
            model = _configured_cc_model()
        command = [executable, "-p", "--safe-mode", "--tools", "",
                   "--strict-mcp-config", "--no-session-persistence",
                   "--permission-mode", "dontAsk", "--output-format", "json",
                   "--json-schema", json.dumps(schema, ensure_ascii=False)]
    else:
        schema_path = output_path.with_suffix(".schema.json")
        schema_path.write_text(json.dumps(schema, ensure_ascii=False),
                               encoding="utf-8", newline="")
        # Runtime capability proof is required before analysis uses this command.
        # Auth still uses the unchanged CODEX_HOME. Only model selection is read
        # from user configuration; hooks/plugins/MCP config are not imported.
        command = [executable, "exec", "--sandbox", "read-only", "--ephemeral",
                   "--ignore-user-config", "--disable", "hooks", "--disable", "plugins",
                   "--disable", "shell_tool", "--disable", "computer_use",
                   "--disable", "multi_agent", "--disable", "enable_mcp_apps",
                   "--disable", "image_generation", "--disable", "view_image",
                   "--disable", "tool_suggest", "--disable", "remote_plugin",
                   "--disable", "code_mode", "--disable", "skill_search",
                   "--disable", "skill_mcp_dependency_install", "--disable", "shell_snapshot",
                   "-c", "apps._default.enabled=false", "-c", "project_doc_max_bytes=0",
                   "-c", 'approval_policy="never"',
                   "-c", 'web_search="disabled"',
                   "--json", "--skip-git-repo-check", "--color", "never",
                   "--output-schema", str(schema_path), "-o", str(output_path),
                   "-C", str(job_dir)]
        if model is None:
            model = _configured_codex_model()
    if model is not None:
        command += ["--model", model]
    if door == "codex":
        command.append("-")
    return command


def _kill_tree(proc):
    """Kill a private POSIX group or a live Windows tree during setup failure.

    cas.kill_tree is sufficient for app-server cleanup on Windows, but its POSIX
    terminate/kill fallback only targets the parent. It also skips exited leaders,
    whose descendants may still hold stdout open; neither contract suffices here.
    Normal Windows execution uses _WindowsJob to retain exited-leader ownership.
    """
    if os.name == "nt":
        killed = subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                                capture_output=True, timeout=15)
        if killed.returncode and proc.poll() is None:
            proc.kill()
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass  # The entire group has already exited.


class _WindowsJob:
    """Own descendants even after an exited leader; no breakaway permission.

    The CLI starts suspended and is assigned before its first instruction. A
    running-process assignment would leave a race in which it spawns children
    outside the job. Closing the last handle also cleans up on success/errors.
    """

    def __init__(self):
        import ctypes
        from ctypes import wintypes as w

        class Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
                        ("PerJobUserTimeLimit", ctypes.c_longlong), ("LimitFlags", w.DWORD),
                        ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", w.DWORD), ("Affinity", ctypes.c_size_t),
                        ("PriorityClass", w.DWORD), ("SchedulingClass", w.DWORD)]

        class Extended(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", ctypes.c_ulonglong * 6),
                        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.ctypes = ctypes
        for name, args, result in (
                ("CreateJobObjectW", [w.LPVOID, w.LPCWSTR], w.HANDLE),
                ("SetInformationJobObject", [w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD], w.BOOL),
                ("AssignProcessToJobObject", [w.HANDLE, w.HANDLE], w.BOOL),
                ("TerminateJobObject", [w.HANDLE, w.UINT], w.BOOL),
                ("CloseHandle", [w.HANDLE], w.BOOL),
                ("CreateToolhelp32Snapshot", [w.DWORD, w.DWORD], w.HANDLE),
                ("Thread32First", [w.HANDLE, w.LPVOID], w.BOOL),
                ("Thread32Next", [w.HANDLE, w.LPVOID], w.BOOL),
                ("OpenThread", [w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
                ("ResumeThread", [w.HANDLE], w.DWORD)):
            function = getattr(self.api, name)
            function.argtypes, function.restype = args, result
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = Extended()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits),
                                                ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def attach(self, proc):
        ctypes = self.ctypes
        if not self.api.AssignProcessToJobObject(self.handle, int(proc._handle)):
            raise ctypes.WinError(ctypes.get_last_error())
        # Popen closes the initial thread handle. Enumerate the suspended thread
        # using documented Win32 APIs and resume only this private process.
        from ctypes import wintypes as w

        class ThreadEntry(ctypes.Structure):
            _fields_ = [("dwSize", w.DWORD), ("cntUsage", w.DWORD),
                        ("th32ThreadID", w.DWORD), ("th32OwnerProcessID", w.DWORD),
                        ("tpBasePri", w.LONG), ("tpDeltaPri", w.LONG), ("dwFlags", w.DWORD)]

        snapshot = self.api.CreateToolhelp32Snapshot(4, 0)  # TH32CS_SNAPTHREAD
        if snapshot == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            entry = ThreadEntry()
            entry.dwSize = ctypes.sizeof(entry)
            more = self.api.Thread32First(snapshot, ctypes.byref(entry))
            while more:
                if entry.th32OwnerProcessID == proc.pid:
                    thread = self.api.OpenThread(2, False, entry.th32ThreadID)
                    if not thread:
                        raise ctypes.WinError(ctypes.get_last_error())
                    try:
                        if self.api.ResumeThread(thread) == 0xFFFFFFFF:
                            raise ctypes.WinError(ctypes.get_last_error())
                        return
                    finally:
                        self.api.CloseHandle(thread)
                more = self.api.Thread32Next(snapshot, ctypes.byref(entry))
            raise OSError("runner could not find the suspended CLI thread")
        finally:
            self.api.CloseHandle(snapshot)

    def terminate(self):
        if not self.api.TerminateJobObject(self.handle, 1):
            raise self.ctypes.WinError(self.ctypes.get_last_error())

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


def _execute(command, prompt, cwd, env, timeout):
    """Return (returncode, stdout, stderr, timed_out); stdin owns a finite EOF."""
    options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP |
               subprocess.CREATE_NO_WINDOW | 0x00000004} if os.name == "nt" else {"start_new_session": True}
    # CREATE_SUSPENDED (0x4) prevents descendants escaping before job assignment.
    job = _WindowsJob() if os.name == "nt" else None
    proc = None
    timed_out = False
    try:
        proc = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, **options)
        if job:
            job.attach(proc)
        try:
            out, err = proc.communicate(prompt.encode("utf-8"), timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            job.terminate() if job else _kill_tree(proc)
            try:
                out, err = proc.communicate(timeout=5)
            except subprocess.TimeoutExpired as exc:
                # POSIX descendants can escape their private process group.
                # Do not hang the controller on inherited stream handles.
                out, err = exc.output or b"", exc.stderr or b""
                proc.stdout.close()
                proc.stderr.close()
    except BaseException:
        if proc:
            _kill_tree(proc)
        raise
    finally:
        if job:
            job.close()
        if proc:
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                if stream:
                    stream.close()
            proc.wait(timeout=5)
    return proc.returncode, out.decode("utf-8", errors="replace"), \
        err.decode("utf-8", errors="replace"), timed_out


def _redact(text, env):
    """Best-effort credential/path redaction for logs, never alter model output.

    This covers credential-shaped fields and secret-bearing environment values;
    it cannot identify arbitrary business information in a frozen evidence pack.
    Logs are local job artifacts, not suitable for automatic public disclosure.
    """
    for key, value in sorted(env.items(), key=lambda pair: len(pair[1]), reverse=True):
        if len(value) >= 6 and re.search(r"token|secret|password|api.?key|credential", key, re.I):
            text = text.replace(value, "[REDACTED]")
    text = re.sub(r"(?i)(Bearer\s+)[^\s\"',}]+", r"\1[REDACTED]", text)
    text = re.sub(r'(?i)([\"\']?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|'
                  r'password|secret)[\"\']?\s*[:=]\s*[\"\']?)[^\s\"\',}]+',
                  r"\1[REDACTED]", text)
    text = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}", "[REDACTED]", text)
    home = str(Path.home())
    for spelling in (home, home.replace("\\", "/"), home.replace("\\", "\\\\")):
        text = text.replace(spelling, "<home>")
    return text


def _json_object(text):
    def reject_constant(value):
        raise ValueError("non-finite JSON number: " + value)

    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key: " + key)
            result[key] = value
        return result

    value = json.loads(text, parse_constant=reject_constant, object_pairs_hook=unique_keys)
    if not isinstance(value, dict):
        raise ValueError("expected a JSON object")
    return value


def _parse(door, stdout, output_path):
    if len(stdout.encode("utf-8")) > MAX_OUTPUT_BYTES:
        raise ValueError("CLI output exceeds transport size limit")
    if door == "cc":
        envelope = _json_object(stdout)
        usage = envelope.get("usage")
        usage = dict(usage) if isinstance(usage, dict) else {}
        if "total_cost_usd" in envelope:
            usage["total_cost_usd"] = envelope["total_cost_usd"]
        if (envelope.get("type") != "result" or envelope.get("subtype") != "success"
                or envelope.get("is_error") is not False):
            raise ValueError("Claude did not return a complete successful result envelope")
        output = envelope.get("structured_output")
        if not isinstance(output, dict):
            raise ValueError("Claude structured_output is missing or is not an object")
        return output, usage
    events = [_json_object(line) for line in stdout.splitlines() if line.strip()]
    completed = [event for event in events if event.get("type") == "turn.completed"]
    if (len(completed) != 1 or events[-1].get("type") != "turn.completed"
            or any(event.get("type") in {"error", "turn.failed"} for event in events)):
        raise ValueError("Codex did not return one complete successful turn")
    if not output_path.is_file() or output_path.stat().st_size > MAX_OUTPUT_BYTES:
        raise ValueError("Codex result file missing or exceeds transport size limit")
    output = _json_object(output_path.read_text(encoding="utf-8"))
    usage = completed[0].get("usage")
    return output, dict(usage) if isinstance(usage, dict) else {}


def _reported_usage(door, stdout):
    """Record observed usage even if the CLI failed after spending tokens."""
    try:
        if door == "cc":
            envelope = _json_object(stdout)
            usage = envelope.get("usage")
            result = dict(usage) if isinstance(usage, dict) else {}
            if "total_cost_usd" in envelope:
                result["total_cost_usd"] = envelope["total_cost_usd"]
            return result
        for line in reversed(stdout.splitlines()):
            event = _json_object(line)
            if event.get("type") == "turn.completed" and isinstance(event.get("usage"), dict):
                return dict(event["usage"])
    except ValueError:
        return {}
    return {}


def _observed_model(door, stdout):
    """Read CLI metadata only; requested aliases and agent prose are not evidence."""
    try:
        if door == "cc":
            models = _json_object(stdout).get("modelUsage")
            if isinstance(models, dict) and len(models) == 1:
                model = next(iter(models))
                return model if isinstance(model, str) and model.strip() else None
        else:
            models = {event["model"] for event in
                      (_json_object(line) for line in stdout.splitlines() if line.strip())
                      if event.get("type") in {"thread.started", "turn.started", "turn.completed"}
                      and isinstance(event.get("model"), str) and event["model"].strip()}
            if len(models) == 1:
                return models.pop()
    except ValueError:
        pass
    return None


def run_analysis(door, prompt, schema, job_dir, timeout_seconds=900, model=None):
    """Return success/output/usage/error/returncode/command/capability and metadata.

    ``cc`` and ``codex`` are the only door names. Caller passes a JSON Schema
    object; this layer only parses the transport envelope and JSON object, leaving
    jsonschema and operation validation to the controller. Unknown usage is {},
    never a fabricated zero-dollar cost. Capability failure never starts a model.
    Each attempt uses unique artifact names so old results cannot satisfy a retry.
    ``model`` is CLI-observed metadata, never the requested alias; unknown is None.
    ``elapsed_seconds`` measures the execution attempt with a monotonic clock,
    including timeout cleanup, and remains None if preflight prevents execution.
    """
    result = {"success": False, "output": None, "usage": {}, "error": None,
              "returncode": None, "command": [], "capability": "unverified",
              "model": None, "elapsed_seconds": None, "deferred": False,
              "probe_usage": [], "probe_artifacts": None}
    env = dict(os.environ)
    try:
        if door not in {"cc", "codex"}:
            raise ValueError("door must be cc or codex")
        if not isinstance(schema, dict) or not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("schema must be an object and prompt must contain frozen evidence")
        if (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
                or not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
            raise ValueError("timeout_seconds must be a positive finite number")
        if model is not None and (not isinstance(model, str) or not model.strip()):
            raise ValueError("model must be a nonempty string or None")
        if door == "codex":
            # Preserve original login/home and networking, not the interactive
            # parent's permission profile, daemon socket or session overrides.
            env = {key: value for key, value in env.items()
                   if not key.upper().startswith("CODEX_") or key.upper() == "CODEX_HOME"}
        job_dir = Path(job_dir).resolve()
        if not job_dir.is_dir():
            raise ValueError("job_dir must be an existing isolated directory")
        if door == "cc":
            if model is None:
                try:
                    model = _configured_cc_model()
                except (OSError, ValueError, TypeError) as exc:
                    result["deferred"] = True
                    raise ValueError("Cannot inherit Claude model selection: " + str(exc)) from exc
            env = _cc_environment(job_dir, env)
        started = time.monotonic()
        deadline = started + timeout_seconds
        status = capability_status(door, cache_dir=job_dir.parent.parent / "capabilities", deadline=deadline)
        executable = status["executable"]
        result["capability"] = status["capability"]
        env["WORKFRAME_SELFITER_JOB"] = env.get("WORKFRAME_SELFITER_JOB") or job_dir.name
        if status["capability"] == "requires_probe":
            proof = _probe_capability(door, executable, job_dir, env, deadline, model,
                                      status["fingerprint"])
            result["probe_usage"] = proof.get("usage", [])
            result["probe_artifacts"] = proof.get("artifacts")
            result["elapsed_seconds"] = time.monotonic() - started
            if not proof.get("verified"):
                result.update(deferred=True, error=proof.get("reason") or "Isolation proof incomplete")
                return result
            result["capability"] = "verified"
        elif status["capability"] != "verified":
            result["deferred"] = True
            result["error"] = status["reason"] or "CLI isolation is unverified"
            return result
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            result.update(deferred=True, error="Shared deadline exhausted during capability probe")
            return result
        output_path = job_dir / ("runner-" + uuid.uuid4().hex + ".json")
        command = _command(door, executable, schema, job_dir, output_path, model)
        # Prompt is only sent through stdin, never exposed in process listings.
        result["command"] = [_redact(arg, env) for arg in command]
        env["WORKFRAME_SELFITER_JOB"] = env.get("WORKFRAME_SELFITER_JOB") or job_dir.name
        try:
            returncode, stdout, stderr, timed_out = _execute(
                command, prompt, job_dir, env, remaining)
        finally:
            result["elapsed_seconds"] = time.monotonic() - started
        result["returncode"] = returncode
        result["usage"] = _reported_usage(door, stdout)
        result["model"] = _observed_model(door, stdout)
        for suffix, stream in (("stdout", stdout), ("stderr", stderr)):
            output_path.with_suffix("." + suffix + ".log").write_text(
                _redact(stream[:MAX_OUTPUT_BYTES], env), encoding="utf-8", newline="")
        if timed_out:
            raise ValueError("analysis timed out; process-tree termination requested")
        if returncode != 0:
            raise ValueError("CLI exited unsuccessfully: " + str(returncode))
        result["output"], result["usage"] = _parse(door, stdout, output_path)
        result["success"] = True
    except (OSError, ValueError, TypeError, subprocess.SubprocessError) as exc:
        result["error"] = _redact(str(exc), env)
    return result
