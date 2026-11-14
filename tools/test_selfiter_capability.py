"""Capability cache and runtime proof contracts; no account use in unit tests."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins/core/scripts"))
import _selfiter_runner as runner
import _selfiter_capability as cap


class CapabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="wf-capability-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.exe = self.base / "cli.exe"
        self.exe.write_bytes(b"test-build")
        self.cache = self.base / "capabilities"
        environment = patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.base / "original-cc")})
        environment.start()
        self.addCleanup(environment.stop)

    def test_new_build_requires_probe_not_exact_version_allowlist(self):
        import inspect
        self.assertIn("cache_dir", inspect.signature(runner.capability_status).parameters)
        # subprocess.run is replaced process-wide here; on macOS platform.platform() shells out to
        # `file`, so it is replaced too instead of receiving the version stub as its result.
        with patch.object(runner, "_resolve_executable", return_value=str(self.exe)), \
             patch.object(runner.subprocess, "run", return_value=type("Version", (), {
                 "returncode": 0, "stdout": "codex-cli 0.167.2"})()), \
             patch.object(runner.capability.platform, "platform", return_value="test-os"), \
             patch.object(runner, "_execute") as execute:
            status = runner.capability_status("codex", cache_dir=self.cache)
        self.assertEqual(status["capability"], "requires_probe")
        self.assertIn("fingerprint", status)
        self.assertFalse(self.cache.exists())
        execute.assert_not_called()

    def test_missing_cli_is_unavailable(self):
        with patch.object(runner, "_resolve_executable", return_value=None):
            self.assertEqual(runner.capability_status("cc")["capability"], "unavailable")

    def test_capability_failure_is_deferred(self):
        self.assertTrue(callable(getattr(runner, "_probe_capability", None)))
        job = self.base / "jobs" / "one"
        job.mkdir(parents=True)
        status = {"capability": "requires_probe", "reason": "new build",
                  "executable": str(self.exe), "fingerprint": {"id": "build"}}
        with patch.object(runner, "capability_status", return_value=status), \
             patch.object(runner, "_probe_capability", return_value={
                 "verified": False, "reason": "project hook fired", "usage": [],
                 "artifacts": str(job / "probe")}), \
             patch.object(runner, "_execute") as execute:
            result = runner.run_analysis("cc", "frozen evidence", {}, job)
        self.assertTrue(result["deferred"])
        self.assertFalse(result["success"])
        self.assertIn("hook fired", result["error"])
        execute.assert_not_called()

    def test_probe_and_analysis_share_deadline(self):
        self.assertTrue(callable(getattr(runner, "_probe_capability", None)))
        job = self.base / "jobs" / "one"
        job.mkdir(parents=True)
        status = {"capability": "requires_probe", "reason": "new build",
                  "executable": str(self.exe), "fingerprint": {"id": "build"}}
        clock = [100.0]

        def probe(*args, **kwargs):
            clock[0] += 7
            return {"verified": True, "usage": [{"input_tokens": 3}], "artifacts": "probe"}

        envelope = json.dumps({"type": "result", "subtype": "success", "is_error": False,
                               "structured_output": {}, "usage": {"input_tokens": 5}})
        with patch.object(runner, "capability_status", return_value=status), \
             patch.object(runner, "_probe_capability", side_effect=probe), \
             patch.object(runner.time, "monotonic", side_effect=lambda: clock[0]), \
             patch.object(runner, "_execute", return_value=(0, envelope, "", False)) as execute:
            result = runner.run_analysis("cc", "frozen evidence", {}, job, timeout_seconds=10)
        self.assertTrue(result["success"], result)
        self.assertEqual(execute.call_args.args[-1], 3)
        self.assertEqual(result["probe_usage"], [{"input_tokens": 3}])

    def test_nested_patch_evidence_requires_literal_single_invocation(self):
        self.assertTrue(callable(getattr(runner.capability, "patch_input", None)))
        patch_text = "*** Begin Patch\n*** Add File: canary.txt\n+x\n*** End Patch"
        valid = {"name": "exec", "input": "text(await tools.apply_patch(" + json.dumps(patch_text) + "));\n"}
        self.assertEqual(runner.capability.patch_input(valid), patch_text)
        for text in ('text("tools.apply_patch fake");', valid["input"] + "text(await tools.exec_command({}));",
                     'text(await tools.apply_patch(variable));'):
            self.assertIsNone(runner.capability.patch_input({"name": "exec", "input": text}))

    def test_cache_requires_all_host_checks_and_unchanged_raw_artifacts(self):
        self.cache.mkdir()
        artifact = self.base / "raw.json"
        artifact.write_bytes(b"host-observed")
        identity = {"id": "id", "door": "cc", "version": "future-build"}
        manifest = {"verified": True, "fingerprint": identity,
                    "checks": {k: True for k in ("hook_positive", "hook_suppressed", "tool_surface", "transport")},
                    "artifacts": {str(artifact): cap.digest(artifact)}}
        path = self.cache / "id.json"
        cap.write_json(path, manifest)
        self.assertTrue(cap.cached(self.cache, identity))
        for key in ("hook_positive", "hook_suppressed", "tool_surface", "transport"):
            with self.subTest(check=key):
                changed = dict(manifest, checks=dict(manifest["checks"], **{key: False}))
                cap.write_json(path, changed)
                self.assertFalse(cap.cached(self.cache, identity))
        cap.write_json(path, manifest)
        artifact.write_bytes(b"changed by other process")
        self.assertFalse(cap.cached(self.cache, identity))
        artifact.write_bytes(b"host-observed")
        self.assertTrue(cap.cached(self.cache, identity))
        self.assertFalse(cap.cached(self.cache, dict(identity, version="replacement")))
        cap.write_json(path, dict(manifest, artifacts={}))
        self.assertFalse(cap.cached(self.cache, identity))

    def test_fingerprint_changes_with_binary_version_platform_and_adapter(self):
        version = type("Version", (), {"returncode": 0, "stdout": "codex-cli 0.167.2"})()
        with patch.object(cap.subprocess, "run", return_value=version), \
             patch.object(cap.platform, "platform", return_value="test-os"):
            first = cap.fingerprint("codex", self.exe)
            self.exe.write_bytes(b"replacement-build")
            self.assertNotEqual(first["id"], cap.fingerprint("codex", self.exe)["id"])
            self.exe.write_bytes(b"test-build")
            self.assertEqual(first["id"], cap.fingerprint("codex", self.exe)["id"])
            version.stdout = "codex-cli 0.167.3"
            self.assertNotEqual(first["id"], cap.fingerprint("codex", self.exe)["id"])
            version.stdout = "codex-cli 0.167.2"
            with patch.object(cap.platform, "platform", return_value="other-os"):
                self.assertNotEqual(first["id"], cap.fingerprint("codex", self.exe)["id"])
            with patch.object(cap, "ADAPTER_VERSION", 999):
                self.assertNotEqual(first["id"], cap.fingerprint("codex", self.exe)["id"])

    def test_expired_probe_never_launches_analysis(self):
        job = self.base / "jobs" / "one"
        job.mkdir(parents=True)
        status = {"capability": "requires_probe", "reason": None,
                  "executable": str(self.exe), "fingerprint": {"id": "build"}}
        clock = [100.0]
        def probe(*args, **kwargs):
            clock[0] = 110.0
            return {"verified": True, "usage": [], "artifacts": "probe"}
        with patch.object(runner, "capability_status", return_value=status), \
             patch.object(runner, "_probe_capability", side_effect=probe), \
             patch.object(runner.time, "monotonic", side_effect=lambda: clock[0]), \
             patch.object(runner, "_execute") as execute:
            result = runner.run_analysis("cc", "evidence", {}, job, timeout_seconds=10)
        self.assertTrue(result["deferred"])
        self.assertIn("deadline", result["error"])
        execute.assert_not_called()

    def test_cached_proof_skips_model_probe(self):
        job = self.base / "jobs" / "one"
        job.mkdir(parents=True)
        status = {"capability": "verified", "reason": None, "executable": str(self.exe)}
        out = json.dumps({"type": "result", "subtype": "success", "is_error": False,
                          "structured_output": {"ok": True}})
        with patch.object(runner, "capability_status", return_value=status), \
             patch.object(runner, "_probe_capability") as probe, \
             patch.object(runner, "_execute", return_value=(0, out, "", False)):
            result = runner.run_analysis("cc", "evidence", {}, job)
        self.assertTrue(result["success"])
        self.assertFalse(result["deferred"])
        self.assertEqual(result["probe_usage"], [])
        probe.assert_not_called()

    def test_cc_auth_is_read_into_environment_without_rewriting_user_files(self):
        self.assertTrue(callable(getattr(runner, "_cc_environment", None)))
        user = self.base / "real-cc"
        user.mkdir()
        credentials = user / ".credentials.json"
        credentials.write_text(json.dumps({"claudeAiOauth": {"accessToken": "test-token"}}),
                               encoding="utf-8", newline="")
        before = credentials.read_bytes()
        isolated = runner._cc_environment(self.base, {"CLAUDE_CONFIG_DIR": str(user), "PATH": "preserved"})
        self.assertEqual(isolated["CLAUDE_CODE_OAUTH_TOKEN"], "test-token")
        self.assertNotEqual(isolated["CLAUDE_CONFIG_DIR"], str(user))
        self.assertEqual(isolated["PATH"], "preserved")
        self.assertFalse((Path(isolated["CLAUDE_CONFIG_DIR"]) / ".credentials.json").exists())
        self.assertEqual(credentials.read_bytes(), before)

    def test_cc_preserves_original_model_and_explicit_override(self):
        settings = self.base / "settings.json"
        settings.write_text('{"model":"opus","hooks":{"ignored":true}}', encoding="utf-8", newline="")
        before = settings.read_bytes()
        with patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.base)}):
            for requested, expected in ((None, "opus"), ("chosen-model", "chosen-model")):
                command = runner._command("cc", "cli", {}, self.base, self.base / "out.json", requested)
                self.assertIn("--model", command)
                self.assertEqual(command[command.index("--model") + 1], expected)
        self.assertEqual(settings.read_bytes(), before)

    def test_cc_invalid_model_configuration_never_silently_defaults(self):
        with patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.base)}):
            for contents in ('bad-json', '[]', '{"model":null}', '{"model":12}',
                             '{"model":" "}', '{"model":"opus","model":"sonnet"}'):
                with self.subTest(contents=contents):
                    (self.base / "settings.json").write_text(contents, encoding="utf-8", newline="")
                    with self.assertRaises(ValueError):
                        runner._command("cc", "cli", {}, self.base, self.base / "out.json", None)
                    command = runner._command("cc", "cli", {}, self.base, self.base / "out.json", "explicit")
                    self.assertEqual(command[command.index("--model") + 1], "explicit")

    def test_cc_probe_and_analysis_receive_same_inherited_model(self):
        (self.base / "settings.json").write_text('{"model":"opus"}', encoding="utf-8", newline="")
        job = self.base / "jobs" / "one"
        job.mkdir(parents=True)
        status = {"capability": "requires_probe", "reason": None, "executable": str(self.exe),
                  "fingerprint": {"id": "build"}}
        out = json.dumps({"type": "result", "subtype": "success", "is_error": False,
                          "structured_output": {"ok": True}})
        with patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.base)}), \
             patch.object(runner, "capability_status", return_value=status), \
             patch.object(runner, "_probe_capability", return_value={"verified": True}) as probe, \
             patch.object(runner, "_execute", return_value=(0, out, "", False)) as execute:
            result = runner.run_analysis("cc", "evidence", {}, job)
        self.assertTrue(result["success"], result)
        self.assertEqual(probe.call_args.args[5], "opus")
        command = execute.call_args.args[0]
        self.assertEqual(command[command.index("--model") + 1], "opus")

    def test_cc_missing_model_retains_cli_default_and_bad_config_defers(self):
        with patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.base)}):
            for contents in (None, '{}'):
                if contents is not None:
                    (self.base / "settings.json").write_text(contents, encoding="utf-8", newline="")
                self.assertNotIn("--model", runner._command("cc", "cli", {}, self.base,
                                                           self.base / "out.json", None))
            (self.base / "settings.json").write_text('{"model":false}', encoding="utf-8", newline="")
            with patch.object(runner, "_execute") as execute:
                result = runner.run_analysis("cc", "evidence", {}, self.base)
            self.assertFalse(result["success"])
            self.assertTrue(result["deferred"])
            execute.assert_not_called()

    def test_cc_config_directory_is_owned_by_harness_even_from_codex(self):
        import _harness
        self.assertTrue(callable(getattr(_harness, "cc_config_dir", None)))
        self.assertTrue(callable(getattr(_harness, "cc_oauth_token", None)))
        with patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.base), "CODEX_THREAD_ID": "parent"}):
            self.assertEqual(_harness.cc_config_dir(), self.base)
        with patch.dict(os.environ, {}, clear=True), patch.object(_harness.Path, "home", return_value=self.base):
            self.assertEqual(_harness.cc_config_dir(), self.base / ".claude")
        self.assertEqual(_harness.cc_oauth_token({"CLAUDE_CODE_OAUTH_TOKEN": "provided"}), "provided")
        self.assertIsNone(_harness.cc_oauth_token({}))

    def test_cc_fixture_is_outside_job_and_project_ancestors(self):
        self.assertTrue(callable(getattr(cap, "probe_directory", None)))
        project = self.base / "user-project"
        job = project / ".workframe/state/selfiter/jobs/one"
        job.mkdir(parents=True)
        (project / ".workframe-config.json").write_bytes(b"{}")
        external = self.base / "system-temp-fixture"
        external.mkdir()
        with patch.object(cap.tempfile, "mkdtemp", return_value=str(external)):
            fixture = cap.probe_directory("cc", job)
        self.assertEqual(fixture, external.resolve())
        self.assertFalse(fixture.is_relative_to(project.resolve()))
        with patch.object(cap.tempfile, "mkdtemp", return_value=str(job)):
            with self.assertRaisesRegex(ValueError, "job|project|repository"):
                cap.probe_directory("cc", job)
        for index, marker in enumerate((".git", ".workframe-config.json", "CLAUDE.md",
                                         ".claude/settings.json", ".claude/settings.local.json")):
            parent = self.base / ("ancestor-" + str(index))
            nested = parent / "nested-temp"
            nested.mkdir(parents=True)
            (parent / marker).parent.mkdir(parents=True, exist_ok=True)
            (parent / marker).write_bytes(b"fixture marker")
            with self.subTest(marker=marker), patch.object(cap.tempfile, "mkdtemp", return_value=str(nested)):
                with self.assertRaisesRegex(ValueError, "project|repository|ancestor"):
                    cap.probe_directory("cc", job)

    def test_unsafe_cc_system_temp_defers_before_any_cli_launch(self):
        job = self.base / "project/jobs/one"
        job.mkdir(parents=True)
        status = {"capability": "requires_probe", "reason": None, "executable": str(self.exe),
                  "fingerprint": {"id": "build"}}
        with patch.object(runner, "capability_status", return_value=status), \
             patch.object(cap.tempfile, "mkdtemp", return_value=str(job)), \
             patch.object(runner, "_execute") as execute:
            result = runner.run_analysis("cc", "evidence", {}, job)
        self.assertTrue(result["deferred"])
        self.assertIn("overlaps", result["error"])
        self.assertFalse(execute.called)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    unittest.main()
