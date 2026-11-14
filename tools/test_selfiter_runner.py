#!/usr/bin/env python3
"""Self-iteration runner contract tests; no real CLI or account use by default."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
import hashlib
import time
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins/core/scripts"))
try:
    import _selfiter_runner as runner
except ModuleNotFoundError:
    runner = None


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(runner, "runner implementation is missing")
        self.tmp = tempfile.TemporaryDirectory(prefix="wf-selfiter-test-")
        self.addCleanup(self.tmp.cleanup)
        self.job = Path(self.tmp.name) / "job"
        self.job.mkdir()
        self.env_patch = patch.dict(os.environ, {"CODEX_HOME": str(self.job / "cli-home"),
                                                "CLAUDE_CONFIG_DIR": str(self.job / "cc-config")})
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.schema = {"type": "object", "properties": {"ok": {"type": "boolean"}},
                       "required": ["ok"], "additionalProperties": False}

    def invoke(self, door="cc", stdout=None, stderr="", returncode=0, output=None, **kw):
        def execute(command, prompt, cwd, env, timeout):
            self.called = (command, prompt, cwd, env, timeout)
            if door == "codex" and output is not None:
                Path(command[command.index("-o") + 1]).write_text(
                    output, encoding="utf-8", newline="")
            return returncode, stdout or "", stderr, False
        with patch.object(runner, "capability_status", return_value={
                "capability": "verified", "reason": None, "executable": "cli.exe"}), \
             patch.object(runner, "_resolve_executable", return_value="cli.exe"), \
             patch.object(runner, "_execute", side_effect=execute):
            return runner.run_analysis(door, "frozen evidence", self.schema, self.job, **kw)

    def cc(self, **changes):
        data = {"type": "result", "subtype": "success", "is_error": False,
                "structured_output": {"ok": True}, "usage": {"input_tokens": 12},
                "total_cost_usd": 1.25}
        data.update(changes)
        return json.dumps(data)

    def test_unverified_refuses_without_model_process(self):
        with patch.object(runner, "capability_status", return_value={
                "capability": "unavailable", "reason": "not proven", "executable": "cli.exe"}), \
             patch.object(runner, "_resolve_executable", return_value="cli.exe"), \
             patch.object(runner, "_execute") as execute:
            result = runner.run_analysis("codex", "evidence", self.schema, self.job)
        self.assertFalse(result["success"])
        self.assertEqual(result["capability"], "unavailable")
        self.assertIn("not proven", result["error"])
        execute.assert_not_called()
        self.assertEqual(set(result), {"success", "output", "usage", "error", "returncode",
                                       "command", "capability", "model", "elapsed_seconds",
                                       "deferred", "probe_usage", "probe_artifacts"})
        self.assertIsNone(result["model"])
        self.assertIsNone(result["elapsed_seconds"])

    def test_cc_schema_body_envelope_usage_and_isolation(self):
        result = self.invoke(stdout=self.cc())
        self.assertTrue(result["success"], result)
        self.assertEqual(result["output"], {"ok": True})
        self.assertEqual(result["usage"]["input_tokens"], 12)
        self.assertEqual(result["usage"]["total_cost_usd"], 1.25)
        cmd, prompt, cwd, env, timeout = self.called
        self.assertEqual(json.loads(cmd[cmd.index("--json-schema") + 1]), self.schema)
        self.assertIn("--safe-mode", cmd)
        self.assertIn("--strict-mcp-config", cmd)
        self.assertEqual(cmd[cmd.index("--tools") + 1], "")
        self.assertEqual(cwd, self.job.resolve())
        self.assertEqual(env["WORKFRAME_SELFITER_JOB"], "job")
        self.assertGreater(timeout, 895)
        self.assertLessEqual(timeout, 900)
        self.assertNotIn("--model", cmd)
        self.assertFalse(any("budget" in arg or "bypass" in arg for arg in cmd))
        self.assertIn("frozen evidence", prompt)

    def test_model_and_marker_are_explicit_only(self):
        with patch.dict(os.environ, {"WORKFRAME_SELFITER_JOB": "controller-id"}):
            result = self.invoke(stdout=self.cc(), model="chosen", timeout_seconds=10)
        self.assertTrue(result["success"])
        cmd, _, _, env, timeout = self.called
        self.assertEqual(cmd[cmd.index("--model") + 1], "chosen")
        self.assertEqual(env["WORKFRAME_SELFITER_JOB"], "controller-id")
        self.assertGreater(timeout, 5)
        self.assertLessEqual(timeout, 10)

    def test_cc_rejects_each_incomplete_success_condition(self):
        for stdout in ("", "not json", "[]", self.cc(type="assistant"),
                       self.cc(subtype="error_max_turns"), self.cc(is_error=True),
                       self.cc(structured_output=None), self.cc(structured_output="{}"),
                       self.cc(is_error="false")):
            with self.subTest(stdout=stdout):
                self.assertFalse(self.invoke(stdout=stdout)["success"])
        self.assertFalse(self.invoke(stdout=self.cc(), returncode=1)["success"])

    def test_runner_does_not_apply_controller_schema_validation(self):
        result = self.invoke(stdout=self.cc(structured_output={"unknown": 1}))
        self.assertTrue(result["success"])
        self.assertEqual(result["output"], {"unknown": 1})

    def test_codex_requires_completed_turn_and_output_file(self):
        events = '\n'.join([json.dumps({"type": "thread.started", "thread_id": "id"}),
                            json.dumps({"type": "turn.started"}),
                            json.dumps({"type": "turn.completed", "usage": {
                                "input_tokens": 30, "output_tokens": 4}})])
        result = self.invoke("codex", stdout=events, output='{"ok":true}')
        self.assertTrue(result["success"], result)
        self.assertEqual(result["usage"]["output_tokens"], 4)
        self.assertEqual(result["output"], {"ok": True})
        cmd = self.called[0]
        self.assertIn("--json", cmd)
        self.assertIn("--ignore-user-config", cmd)
        self.assertEqual(cmd[cmd.index("-C") + 1], str(self.job.resolve()))
        self.assertEqual(json.loads(Path(cmd[cmd.index("--output-schema") + 1]).read_text(
            encoding="utf-8")), self.schema)
        for stdout, output in (("", '{"ok":true}'), (events, None), (events, ""),
                               (events, "[]"), (events, "invalid"),
                               (events + '\n{"type":"turn.failed"}', '{"ok":true}'),
                               (events + '\n{"type":"error"}', '{"ok":true}'),
                               (events + '\ngarbage', '{"ok":true}')):
            with self.subTest(stdout=stdout, output=output):
                self.assertFalse(self.invoke("codex", stdout=stdout, output=output)["success"])

    def test_logs_redact_secrets_and_local_paths_but_output_is_intact(self):
        secret = "test-secret-value-123456"
        with patch.dict(os.environ, {"RUNNER_TEST_TOKEN": secret}):
            result = self.invoke(stdout=self.cc(structured_output={"value": secret}),
                                 stderr="Authorization: Bearer " + secret + " " + str(Path.home()))
        self.assertEqual(result["output"], {"value": secret})
        logs = "".join(p.read_text(encoding="utf-8") for p in self.job.glob("*.log"))
        self.assertNotIn(secret, logs)
        self.assertNotIn(str(Path.home()), logs)
        self.assertIn("[REDACTED]", logs)

    def test_invalid_arguments_fail_without_launch(self):
        with patch.object(runner, "_execute") as execute:
            for args in (("other", {}, 900), ("cc", [], 900), ("cc", {}, 0),
                         ("cc", {}, True), ("cc", {}, float("inf"))):
                with self.subTest(args=args):
                    result = runner.run_analysis(args[0], "evidence", args[1], self.job,
                                                 timeout_seconds=args[2])
                    self.assertFalse(result["success"])
            execute.assert_not_called()

    def test_timeout_kills_real_descendant_and_captures_streams(self):
        child_pid = self.job / "child.pid"
        code = ("import subprocess,sys,time,pathlib; "
                "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)']); "
                "pathlib.Path(sys.argv[1]).write_bytes(str(p.pid).encode()); "
                "print('before timeout',flush=True); "
                "print('diagnostic',file=sys.stderr,flush=True);time.sleep(120)")
        rc, out, err, timed_out = runner._execute(
            [sys.executable, "-c", code, str(child_pid)], "", self.job,
            dict(os.environ), 2)
        self.assertTrue(timed_out)
        self.assertNotEqual(rc, 0)
        self.assertIn("before timeout", out)
        self.assertIn("diagnostic", err)
        pid = int(child_pid.read_bytes())
        if os.name == "nt":
            listing = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV"],
                                     capture_output=True, text=True, errors="replace", timeout=15)
            self.assertNotIn(f'"{pid}"', listing.stdout)
        else:
            try:
                state = Path(f"/proc/{pid}/stat").read_text().split()[2]
            except (FileNotFoundError, ProcessLookupError):
                # Reaped between the kill and this read (reading the stat of an exiting
                # process raises ProcessLookupError): gone is what the test wants.
                with self.assertRaises(ProcessLookupError):
                    os.kill(pid, 0)
            else:
                self.assertEqual(state, "Z")

    def test_cli_discovery_and_missing_executable(self):
        with patch.object(runner.shutil, "which", return_value="claude.cmd") as which:
            self.assertEqual(runner._resolve_executable("cc"), "claude.cmd")
            which.assert_called_once_with("claude")
        with patch.object(runner.cas, "find_codex_exe", return_value="codex.cmd"):
            self.assertEqual(runner._resolve_executable("codex"), "codex.cmd")
        with patch.object(runner, "_resolve_executable", return_value=None), \
             patch.object(runner, "_execute") as execute:
            result = runner.run_analysis("cc", "evidence", {}, self.job)
            self.assertIn("not found", result["error"])
            execute.assert_not_called()

    def test_capability_is_not_asserted_by_job_environment(self):
        with patch.dict(os.environ, {"WORKFRAME_SELFITER_JOB": "job",
                                     "WORKFRAME_SELFITER_VERIFIED": "true"}):
            for door in ("cc", "codex"):
                self.assertEqual(runner._capability(door, "fake-executable")[0], "unavailable")

    def test_public_capability_status_is_read_only_and_has_no_model_start(self):
        with patch.object(runner, "_resolve_executable", return_value="cli.exe"), \
             patch.object(runner.capability, "fingerprint", return_value={"id": "new"}), \
             patch.object(runner.capability, "cached", return_value=False), \
             patch.object(runner, "_execute") as execute:
            self.assertEqual(runner.capability_status("codex")["capability"], "requires_probe")
            execute.assert_not_called()
        with patch.object(runner, "_resolve_executable", return_value=None):
            self.assertEqual(runner.capability_status("cc")["capability"], "unavailable")
        self.assertEqual(runner.capability_status("other")["capability"], "unavailable")

    def test_cc_only_proven_build_and_platform_are_capable(self):
        for version in ("2.1.322 (Claude Code)", "2.1.323 (Claude Code)"):
            with self.subTest(version=version), \
                 patch.object(runner.capability, "fingerprint", return_value={"version": version}):
                self.assertEqual(runner._capability("cc", "cli.exe")[0], "requires_probe")

    def test_codex_only_proven_build_and_platform_are_capable(self):
        for version in ("codex-cli 0.166.0", "codex-cli 0.167.2", "custom-build"):
            with self.subTest(version=version), \
                 patch.object(runner.capability, "fingerprint", return_value={"version": version}):
                self.assertEqual(runner._capability("codex", "cli.exe")[0], "requires_probe")
        with patch.object(runner.capability, "fingerprint", side_effect=OSError("unavailable")):
            self.assertEqual(runner._capability("codex", "cli.exe")[0], "unavailable")

    def test_real_process_receives_utf8_stdin_environment_and_cwd(self):
        code = ("import sys,os,json;print(json.dumps([sys.stdin.read(),os.getcwd(),"
                "os.environ.get('WORKFRAME_SELFITER_JOB')],ensure_ascii=False))")
        env = dict(os.environ, WORKFRAME_SELFITER_JOB="own-test", PYTHONIOENCODING="utf-8")
        rc, stdout, stderr, expired = runner._execute(
            [sys.executable, "-c", code], "evidence \u4e2d\u6587", self.job, env, 10)
        self.assertEqual(rc, 0, stderr)
        self.assertFalse(expired)
        # The child reports its resolved working directory (/private/var/... on macOS), so compare
        # by identity of the directory rather than by spelling.
        reported = json.loads(stdout)
        self.assertEqual([reported[0], reported[2]], ["evidence \u4e2d\u6587", "own-test"])
        self.assertTrue(os.path.samefile(reported[1], self.job))

    def test_transport_timeout_retains_logs_and_failure(self):
        with patch.object(runner, "capability_status", return_value={
                "capability": "verified", "reason": None, "executable": "cli.exe"}), \
             patch.object(runner, "_resolve_executable", return_value="cli.exe"), \
             patch.object(runner, "_execute", return_value=(-1, "partial", "reason", True)):
            result = runner.run_analysis("cc", "evidence", {}, self.job)
        self.assertFalse(result["success"])
        self.assertIn("timed out", result["error"])
        self.assertEqual(result["returncode"], -1)
        self.assertTrue(any("partial" in p.read_text() for p in self.job.glob("*.log")))

    def test_json_rejects_duplicate_keys_and_nonfinite_numbers(self):
        for text in ('{"ok":true,"ok":false}', '{"x":NaN}', '{"x":Infinity}'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                runner._json_object(text)

    def test_usage_survives_cli_failure_without_accepting_output(self):
        result = self.invoke(stdout=self.cc(subtype="error_max_turns"), returncode=1)
        self.assertFalse(result["success"])
        self.assertIsNone(result["output"])
        self.assertEqual(result["usage"]["input_tokens"], 12)

    def test_codex_model_is_read_only_and_profile_overlay_wins(self):
        home = Path(os.environ["CODEX_HOME"])
        home.mkdir()
        config = home / "config.toml"
        config.write_text('model="base"\nprofile="work"\n', encoding="utf-8", newline="")
        overlay = home / "work.config.toml"
        overlay.write_text('model="configured"\n', encoding="utf-8", newline="")
        before = {p: p.read_bytes() for p in (config, overlay)}
        self.assertEqual(runner._configured_codex_model(), "configured")
        cmd = runner._command("codex", "cli.exe", {}, self.job, self.job / "answer.json", None)
        self.assertEqual(cmd[cmd.index("--model") + 1], "configured")
        self.assertEqual(before, {p: p.read_bytes() for p in before})
        cmd = runner._command("codex", "cli.exe", {}, self.job, self.job / "answer2.json", "explicit")
        self.assertEqual(cmd[cmd.index("--model") + 1], "explicit")

    def test_codex_candidate_disables_customization_and_external_tool_surfaces(self):
        cmd = runner._command("codex", "cli.exe", {}, self.job, self.job / "answer.json", None)
        disabled = {cmd[i + 1] for i, arg in enumerate(cmd) if arg == "--disable"}
        self.assertTrue({"hooks", "plugins", "shell_tool", "computer_use", "multi_agent",
                         "enable_mcp_apps", "image_generation", "view_image", "tool_suggest",
                         "remote_plugin", "code_mode", "skill_search"}.issubset(disabled))
        self.assertIn("project_doc_max_bytes=0", cmd)
        self.assertIn("apps._default.enabled=false", cmd)
        self.assertIn("--ignore-user-config", cmd)
        self.assertNotIn("--dangerously-bypass-hook-trust", cmd)

    def test_codex_does_not_inherit_parent_permission_or_session_overrides(self):
        inherited = {"CODEX_PERMISSION_PROFILE": "danger-full-access",
                     "CODEX_THREAD_ID": "parent-thread", "CODEX_SESSION_ID": "parent-session",
                     "CODEX_DAEMON_SHUTDOWN_SOCKET": "parent-socket",
                     "CODEX_CONFIG_OVERRIDES": "sandbox_mode=danger-full-access",
                     "HTTPS_PROXY": "http://example.invalid:8888"}
        with patch.dict(os.environ, inherited):
            result = self.invoke("codex", stdout='{"type":"turn.completed"}',
                                 output='{"ok":true}')
        self.assertTrue(result["success"], result)
        env = self.called[3]
        for key in inherited:
            if key.startswith("CODEX_"):
                self.assertFalse(key in env, key)
        self.assertEqual(env["CODEX_HOME"], os.environ["CODEX_HOME"])
        self.assertEqual(env["HTTPS_PROXY"], inherited["HTTPS_PROXY"])
        self.assertEqual(env["PATH"], os.environ["PATH"])
        self.assertEqual(env["WORKFRAME_SELFITER_JOB"], "job")

    @unittest.skipUnless(os.name == "nt", "Windows process ownership")
    def test_exited_leader_does_not_leave_a_detached_descendant(self):
        pid_file = self.job / "detached.pid"
        code = ("import subprocess,sys,pathlib; "
                "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)'],"
                "creationflags=subprocess.DETACHED_PROCESS,stdin=subprocess.DEVNULL,"
                "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); "
                "pathlib.Path(sys.argv[1]).write_bytes(str(p.pid).encode()); print('leader done')")
        pid = None
        try:
            rc, out, err, expired = runner._execute(
                [sys.executable, "-c", code, str(pid_file)], "", self.job,
                dict(os.environ), 10)
            self.assertEqual(rc, 0, err)
            self.assertFalse(expired)
            self.assertIn("leader done", out)
            pid = int(pid_file.read_bytes())
            listing = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV"],
                                     capture_output=True, text=True, errors="replace", timeout=15)
            self.assertNotIn(f'"{pid}"', listing.stdout)
        finally:
            if pid is not None:
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                               capture_output=True, timeout=15)

    @unittest.skipUnless(os.name == "nt", "Windows detached launch")
    def test_detached_worker_survives_launcher_and_inherits_environment(self):
        gate = self.job / "gate"
        result_file = self.job / "detached-result.json"
        worker = self.job / "worker.py"
        worker.write_text(
            "import json,os,pathlib,sys,time\n"
            "gate,result=map(pathlib.Path,sys.argv[1:])\n"
            "deadline=time.monotonic()+10\n"
            "while not gate.exists() and time.monotonic()<deadline: time.sleep(0.01)\n"
            "assert gate.exists(), 'launcher did not release worker'\n"
            "result.write_text(json.dumps({'pid':os.getpid(),'ppid':os.getppid(),"
            "'cwd':os.getcwd(),'marker':os.environ.get('WORKFRAME_SELFITER_JOB')}),"
            "encoding='utf-8',newline='')\n", encoding="utf-8", newline="")
        launcher = ("import subprocess,sys; p=subprocess.Popen(sys.argv[1:],"
                    "creationflags=subprocess.DETACHED_PROCESS|subprocess.CREATE_NEW_PROCESS_GROUP,"
                    "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);"
                    "print(p.pid)")
        proc = subprocess.run([sys.executable, "-c", launcher, sys.executable, str(worker),
                               str(gate), str(result_file)], cwd=self.job,
                              env=dict(os.environ, WORKFRAME_SELFITER_JOB="detached-probe"),
                              capture_output=True, text=True, timeout=10)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        pid = int(proc.stdout.strip())
        try:
            self.assertFalse(result_file.exists())
            gate.write_bytes(b"launcher exited")
            deadline = time.monotonic() + 10
            while not result_file.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            data = json.loads(result_file.read_text(encoding="utf-8"))
            self.assertEqual(data["pid"], pid)
            self.assertEqual(data["marker"], "detached-probe")
            self.assertEqual(data["cwd"], str(self.job))
        finally:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, timeout=15)

    def test_model_metadata_comes_from_observation_and_elapsed_from_execution(self):
        with patch.object(runner.time, "monotonic", side_effect=[10.0, 10.5, 12.5]):
            result = self.invoke(stdout=self.cc(modelUsage={"observed-model": {}}),
                                 model="requested-alias")
        self.assertEqual(result["model"], "observed-model")
        self.assertEqual(result["elapsed_seconds"], 2.5)
        result = self.invoke("codex", stdout='{"type":"turn.completed"}',
                             output='{"ok":true}', model="requested-alias")
        self.assertIsNone(result["model"])
        self.assertGreaterEqual(result["elapsed_seconds"], 0)
        for usage in ({}, {"one": {}, "two": {}}):
            self.assertIsNone(self.invoke(stdout=self.cc(modelUsage=usage))["model"])

    @unittest.skipUnless(os.name == "nt", "Windows exited-leader timeout")
    def test_timeout_kills_detached_descendant_after_leader_exit(self):
        pid_file = self.job / "orphan.pid"
        code = ("import subprocess,sys,pathlib; "
                "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)'],"
                "creationflags=subprocess.DETACHED_PROCESS,stdin=subprocess.DEVNULL,"
                "stdout=sys.stdout,stderr=sys.stderr); "
                "pathlib.Path(sys.argv[1]).write_bytes(str(p.pid).encode()); "
                "print('leader exited; child owns streams',flush=True)")
        pid = None
        try:
            rc, out, err, expired = runner._execute(
                [sys.executable, "-c", code, str(pid_file)], "", self.job,
                dict(os.environ), 1)
            self.assertTrue(expired)
            self.assertEqual(rc, 0, err)  # Leader success cannot hide a hung descendant.
            self.assertIn("child owns streams", out)
            pid = int(pid_file.read_bytes())
            listing = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV"],
                                     capture_output=True, text=True, errors="replace", timeout=15)
            self.assertNotIn(f'"{pid}"', listing.stdout)
        finally:
            if pid is not None:
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                               capture_output=True, timeout=15)


def probe_cc():
    """Explicit real CLI probe, never run by unittest or automatic scheduling.

    Uses only runner environment and command isolation, leaving settings/config/
    auth/trust untouched. Raw streams are redacted into this probe's temp job.
    It does not grant capability: empty init lists do not prove all hook sources
    were suppressed, especially managed policy and built-in plugin surfaces.
    """
    job = Path(tempfile.mkdtemp(prefix="wf-selfiter-capability-"))
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}},
              "required": ["ok"], "additionalProperties": False}
    exe = runner._resolve_executable("cc")
    if not exe:
        print(json.dumps({"capability": "unverified", "error": "claude missing"}))
        return
    env = dict(os.environ, WORKFRAME_SELFITER_JOB=job.name)
    output = job / "probe.json"
    command = runner._command("cc", exe, schema, job, output, None)
    command[command.index("--output-format") + 1] = "stream-json"
    command.append("--verbose")
    version = subprocess.run([exe, "--version"], capture_output=True, timeout=15).stdout
    before = hashlib.sha256(Path(exe).read_bytes()).hexdigest()
    rc, stdout, stderr, expired = runner._execute(
        command, 'Return only {"ok":true}. Do not invoke any tools.', job, env, 90)
    for suffix, stream in (("stdout", stdout), ("stderr", stderr)):
        (job / (suffix + ".log")).write_text(runner._redact(stream, env),
                                             encoding="utf-8", newline="")
    events = []
    for line in stdout.splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            pass
    init = next((e for e in events if e.get("type") == "system" and
                 e.get("subtype") == "init"), {})
    result = next((e for e in events if e.get("type") == "result"), {})
    print(json.dumps({"version": version.decode("utf-8", errors="replace").strip(),
                      "cli_sha256_unchanged": before == hashlib.sha256(Path(exe).read_bytes()).hexdigest(),
                      "returncode": rc, "timeout": expired, "tools": init.get("tools"),
                      "mcp_servers": init.get("mcp_servers"), "model": init.get("model"),
                      "result_subtype": result.get("subtype"), "is_error": result.get("is_error"),
                      "structured_output": result.get("structured_output"),
                      "usage": result.get("usage"),
                      "capability": runner.capability_status("cc")["capability"],
                      "logs": str(job)}, ensure_ascii=True))


def inspect_codex():
    """Read installed protocol schema; no model, config mutation or trust bypass."""
    job = Path(tempfile.mkdtemp(prefix="wf-selfiter-codex-schema-"))
    exe = runner._resolve_executable("codex")
    command = [exe, "app-server", "generate-json-schema", "--experimental", "--out", str(job)]
    generated = subprocess.run(command, capture_output=True, timeout=30)
    print("schema_returncode=" + str(generated.returncode))
    for path in sorted(job.rglob("*ThreadStartParams*")):
        data = json.loads(path.read_text(encoding="utf-8"))
        properties = data.get("properties", {})
        print(json.dumps({"file": path.name, "fields": list(properties),
                          "tool_fields": {k: v for k, v in properties.items()
                                          if "tool" in k.lower() or "config" in k.lower()}},
                         ensure_ascii=True))
    print("schema_dir=" + str(job))
    # Inspect the actual assembled prompt after disabling shell/hooks/plugins.
    # This command has no turn/start and cannot spend model tokens.
    command = [exe, "debug", "prompt-input", "Return OK.",
               "--disable", "hooks", "--disable", "plugins", "--disable", "shell_tool",
               "--disable", "multi_agent", "--disable", "computer_use",
               "--disable", "enable_mcp_apps", "-c", 'web_search="disabled"']
    rc, stdout, stderr, expired = runner._execute(command, "", job,
                                                 dict(os.environ, WORKFRAME_SELFITER_JOB=job.name), 30)
    (job / "prompt-input.log").write_text(runner._redact(stdout, dict(os.environ)),
                                          encoding="utf-8", newline="")
    print(json.dumps({"prompt_returncode": rc, "timeout": expired,
                      "has_apply_patch": "apply_patch" in stdout,
                      "has_exec_command": "exec_command" in stdout,
                      "stdout_bytes": len(stdout.encode("utf-8")),
                      "stderr": runner._redact(stderr[-500:], dict(os.environ))}, ensure_ascii=True))


def probe_cc_hooks():
    """Positive/negative hook control in a disposable project, one minimal turn."""
    job = Path(tempfile.mkdtemp(prefix="wf-selfiter-cc-hooks-"))
    settings = job / ".claude" / "settings.json"
    settings.parent.mkdir()
    hook_code = ("import os,pathlib;pathlib.Path(os.environ['WF_RUNNER_PROBE_MARKER'])"
                 ".write_bytes(b'fired')")
    # CC runs command hooks under Bash on Windows; unquoted backslashes in an
    # executable without spaces would be consumed despite list2cmdline's quoting.
    hook_cmd = subprocess.list2cmdline([Path(sys.executable).as_posix(), "-c", hook_code])
    settings.write_text(json.dumps({"hooks": {"SessionStart": [{"hooks": [
        {"type": "command", "command": hook_cmd}]}]}}), encoding="utf-8", newline="")
    before = settings.read_bytes()
    exe = runner._resolve_executable("cc")
    results = []
    for safe in (False, True):
        marker = job / ("safe-marker" if safe else "positive-marker")
        env = dict(os.environ, WORKFRAME_SELFITER_JOB=job.name,
                   WF_RUNNER_PROBE_MARKER=str(marker))
        command = [exe, "-p", "--max-turns", "1", "--output-format", "stream-json",
                   "--verbose", "--tools", "", "--strict-mcp-config",
                   "--no-session-persistence", "--setting-sources", "project",
                   "--debug-file", str(job / (("safe" if safe else "positive") + ".debug.log"))]
        if safe:
            command.append("--safe-mode")
        rc, stdout, stderr, expired = runner._execute(command, "Return OK.", job, env, 45)
        for suffix, stream in (("stdout", stdout), ("stderr", stderr)):
            (job / (("safe" if safe else "positive") + "." + suffix + ".log")).write_text(
                runner._redact(stream, env), encoding="utf-8", newline="")
        results.append({"safe_mode": safe, "marker_fired": marker.exists(),
                        "returncode": rc, "timeout": expired,
                        "stderr": runner._redact(stderr[-1000:], env),
                        "events": [e.get("subtype", e.get("type")) for e in
                                   (json.loads(line) for line in stdout.splitlines() if line.strip())]})
    print(json.dumps({"controls": results, "settings_unchanged": settings.read_bytes() == before,
                      "logs": str(job)}, ensure_ascii=True))


def probe_codex():
    """One explicit real transport/tool-denial probe in an unmarked scratch cwd."""
    job = Path(tempfile.mkdtemp(prefix="wf-selfiter-codex-capability-"))
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}},
              "required": ["ok"], "additionalProperties": False}
    exe = runner._resolve_executable("codex")
    command = runner._command("codex", exe, schema, job, job / "result.json", None)
    env = dict(os.environ, WORKFRAME_SELFITER_JOB=job.name)
    prompt = ('This is an authorized tool isolation probe in a disposable directory. '
              'If apply_patch is available, attempt to create runner-tool-canary.txt containing '
              'only PROBE. Do not use any other tool. Then return {"ok":true}.')
    rc, stdout, stderr, expired = runner._execute(command, prompt, job, env, 90)
    for suffix, stream in (("stdout", stdout), ("stderr", stderr)):
        (job / (suffix + ".log")).write_text(runner._redact(stream, env),
                                             encoding="utf-8", newline="")
    events = []
    for line in stdout.splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            pass
    print(json.dumps({"returncode": rc, "timeout": expired,
                      "item_types": [e.get("item", {}).get("type") for e in events
                                     if e.get("item")],
                      "events": [e.get("type") for e in events],
                      "usage": runner._reported_usage("codex", stdout),
                      "canary_written": (job / "runner-tool-canary.txt").exists(),
                      "model": command[command.index("--model") + 1] if "--model" in command else None,
                      "stderr_tail": runner._redact(stderr[-1200:], env),
                      "logs": str(job)}, ensure_ascii=True))


def probe_codex_matrix():
    """Real write-denial matrix; rollout calls, not the model's report, are evidence."""
    base = Path(tempfile.mkdtemp(prefix="wf-selfiter-codex-matrix-"))
    job = base / "job"
    project = base / "project"
    external = base / "external"
    for directory in (job, project, external):
        directory.mkdir()
    # Only the sibling project has a marker. The invocation cwd stays unmarked.
    (project / ".git").mkdir()
    targets = []
    patches = []
    for directory in (job, project, external):
        for operation in ("create", "change", "delete"):
            path = directory / (operation + "-sentinel.txt")
            if operation != "create":
                path.write_bytes(b"ORIGINAL\n")
            targets.append(path)
            name = path.as_posix()
            if operation == "create":
                body = f"*** Add File: {name}\n+CHANGED\n"
            elif operation == "change":
                body = f"*** Update File: {name}\n@@\n-ORIGINAL\n+CHANGED\n"
            else:
                body = f"*** Delete File: {name}\n"
            patches.append("*** Begin Patch\n" + body + "*** End Patch")
    before = {p: p.read_bytes() if p.exists() else None for p in targets}
    exe = runner._resolve_executable("codex")
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}},
              "required": ["ok"], "additionalProperties": False}
    command = runner._command("codex", exe, schema, job, job / "result.json", None)
    # This explicit probe retains its own rollout for inspection; production
    # remains ephemeral. No settings, authentication or trust is rewritten.
    command.remove("--ephemeral")
    prompt = ("Authorized write-isolation probe in disposable directories. Call apply_patch "
              "separately for EACH of the following nine patches, even after a previous rejection. "
              "Do not combine patches, do not use other tools, and do not skip attempts. "
              "All attempts are expected to be denied by the read-only policy. "
              "Finally return {\"ok\":true}.\n\n" + "\n\n".join(patches))
    env = dict(os.environ, WORKFRAME_SELFITER_JOB=job.name)
    rc, stdout, stderr, expired = runner._execute(command, prompt, job, env, 180)
    for suffix, stream in (("stdout", stdout), ("stderr", stderr)):
        (job / (suffix + ".log")).write_text(runner._redact(stream, env),
                                             encoding="utf-8", newline="")
    events = [json.loads(line) for line in stdout.splitlines() if line.strip()]
    thread_id = next((e.get("thread_id") for e in events if e.get("type") == "thread.started"), None)
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    rollouts = list((home / "sessions").rglob("*" + thread_id + "*.jsonl")) if thread_id else []
    calls = []
    tool_outputs = []
    if rollouts:
        for line in rollouts[0].read_text(encoding="utf-8").splitlines():
            event = json.loads(line)
            payload = event.get("payload", {})
            if event.get("type") != "response_item":
                continue
            if payload.get("type") in {"function_call", "custom_tool_call"}:
                calls.append(payload)
            elif payload.get("type") in {"function_call_output", "custom_tool_call_output"}:
                tool_outputs.append(payload)
    (job / "rollout-tools.log").write_text(runner._redact(json.dumps({
        "calls": calls, "outputs": tool_outputs}, ensure_ascii=False), env),
        encoding="utf-8", newline="")
    attempts = []
    for path in targets:
        matching = [c for c in calls if path.as_posix() in str(c.get("input", c.get("arguments", "")))]
        ids = {c.get("call_id") for c in matching}
        outputs = [o.get("output") for o in tool_outputs if o.get("call_id") in ids]
        attempts.append({"area": path.parent.name, "operation": path.stem,
                         "calls": len(matching), "outputs": outputs,
                         "unchanged": before[path] == (path.read_bytes() if path.exists() else None)})
    print(json.dumps({"returncode": rc, "timeout": expired, "thread_id": thread_id,
                      "rollout_found": bool(rollouts), "tool_names": [c.get("name") for c in calls],
                      "attempts": attempts, "stderr_denials": stderr.count("patch rejected"),
                      "logs": str(job)}, ensure_ascii=True))


def codex_runtime_worker(base):
    """One real public-API turn, gated until the detached launcher has exited."""
    base = Path(base).resolve()
    deadline = time.monotonic() + 20
    while not (base / "launch-exited").exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    if not (base / "launch-exited").exists():
        raise RuntimeError("runtime probe launcher did not exit")
    job = base / "job"
    job.mkdir()
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}},
              "required": ["ok"], "additionalProperties": False}
    result = runner.run_analysis("codex", 'Frozen evidence: {"ok":true}. Return that object. '
                                 'Do not invoke tools.', schema, job, timeout_seconds=90)
    report = {"pid": os.getpid(), "parent_pid": os.getppid(), "result": result,
              "marker": os.environ.get("WORKFRAME_SELFITER_JOB"),
              "launcher_exited_before_execution": True}
    (base / "worker-result.json").write_text(json.dumps(report, indent=2),
                                            encoding="utf-8", newline="")


def probe_codex_runtime(base):
    """Explicit, one-account-turn integration probe; never selected by unittest.

    Keep original auth in place and hash config/trust/auth before and after.
    A short-lived launcher exits before releasing a real detached worker. Raw
    runner streams, exact command, binary version/hash and public API metadata
    remain in the caller's newly created scratch directory.
    """
    base = Path(base).resolve()
    base.mkdir(parents=True, exist_ok=False)
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    files = [home / name for name in ("config.toml", "auth.json", "hooks.json")]
    files += list(home.glob("*.config.toml"))
    files += [ROOT.parent / name for name in
              (".claude/settings.json", ".claude/settings.local.json", ".codex/config.toml")]

    def fingerprints():
        return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None
                for p in files}

    before = fingerprints()
    exe = runner._resolve_executable("codex")
    version = subprocess.run([exe, "--version"], capture_output=True, timeout=10)
    binary_hash = hashlib.sha256(Path(exe).read_bytes()).hexdigest()
    launch_code = ("import subprocess,sys; p=subprocess.Popen(sys.argv[1:],"
                   "creationflags=subprocess.DETACHED_PROCESS|subprocess.CREATE_NEW_PROCESS_GROUP,"
                   "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);"
                   "print(p.pid)")
    env = dict(os.environ, WORKFRAME_SELFITER_JOB=base.name)
    launcher = subprocess.run([sys.executable, "-c", launch_code, sys.executable, "-B",
                               str(Path(__file__).resolve()), "--codex-runtime-worker", str(base)],
                              env=env, cwd=base, capture_output=True, text=True, timeout=15)
    if launcher.returncode != 0:
        raise RuntimeError("detached launcher failed: " + launcher.stderr)
    pid = int(launcher.stdout.strip())
    (base / "launch-exited").write_bytes(b"launcher returned before model start")
    deadline = time.monotonic() + 115
    try:
        while not (base / "worker-result.json").exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        report = json.loads((base / "worker-result.json").read_text(encoding="utf-8"))
        after = fingerprints()
        binary_after = hashlib.sha256(Path(exe).read_bytes()).hexdigest()
        report.update(version=version.stdout.decode("utf-8", errors="replace").strip(),
                      executable=exe, executable_sha256=binary_hash, worker_pid=pid,
                      executable_sha256_after=binary_after,
                      cli_binary_unchanged=binary_hash == binary_after,
                      fingerprints_before=before, fingerprints_after=after,
                      protected_files_unchanged=before == after, logs=str(base))
        (base / "summary.json").write_text(json.dumps(report, indent=2),
                                          encoding="utf-8", newline="")
        print(json.dumps(report, ensure_ascii=True))
        return (report["result"]["success"] and report["protected_files_unchanged"]
                and report["cli_binary_unchanged"])
    finally:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True, timeout=15)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    if sys.argv[1:] == ["--probe-cc"]:
        probe_cc()
    elif sys.argv[1:] == ["--inspect-codex"]:
        inspect_codex()
    elif sys.argv[1:] == ["--probe-cc-hooks"]:
        probe_cc_hooks()
    elif sys.argv[1:] == ["--probe-codex"]:
        probe_codex()
    elif sys.argv[1:] == ["--probe-codex-matrix"]:
        probe_codex_matrix()
    elif len(sys.argv) == 3 and sys.argv[1] == "--probe-codex-runtime":
        sys.exit(0 if probe_codex_runtime(sys.argv[2]) else 1)
    elif len(sys.argv) == 3 and sys.argv[1] == "--codex-runtime-worker":
        codex_runtime_worker(sys.argv[2])
    elif sys.argv[1:] == ["--smoke-cc"]:
        job = Path(tempfile.mkdtemp(prefix="wf-selfiter-cc-public-api-"))
        schema = {"type": "object", "properties": {"ok": {"type": "boolean"}},
                  "required": ["ok"], "additionalProperties": False}
        result = runner.run_analysis("cc", 'Frozen evidence: {"ok":true}. Return that object.',
                                     schema, job, timeout_seconds=90)
        print(json.dumps({"preflight": runner.capability_status("cc"),
                          "result": result, "logs": str(job)}, ensure_ascii=True))
        sys.exit(0 if result["success"] else 1)
    else:
        unittest.main()
