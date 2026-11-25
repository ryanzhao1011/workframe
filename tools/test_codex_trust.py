#!/usr/bin/env python3
"""只读信任诊断状态矩阵；假 transport，绝不启动本机 Codex。"""
import copy
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins/core/scripts"))
import _codex_trust as ct
import _codex_appserver as cas
import _query_codex_runtime as runtime
import _query_codex_trust as launch_trust
import workframe_doctor as doctor

PLUGIN = ROOT / "plugins/core"
PID = "core@custom-market"
FIXTURE_HOME = Path(tempfile.gettempdir()) / "wf-trust-fixture-home"
MANIFEST = json.loads((PLUGIN / "hooks/hooks.codex.manifest.json").read_text(encoding="utf-8"))


def listing(status="trusted"):
    return {"data": [{"hooks": [dict(pluginId=PID, sourcePath=str(PLUGIN / "hooks/hooks.codex.json"),
        key=PID + ":" + r["key"], command="run", currentHash="config:content", trustStatus=status)
        for r in MANIFEST["entries"]], "errors": [], "warnings": []}]}


class Server:
    def __init__(self, response):
        self.response = response
        self.calls = []
    def hooks_list(self, cwds):
        self.calls.append(("hooks/list", cwds))
        if isinstance(self.response, Exception):
            raise self.response
        return copy.deepcopy(self.response)
    def config_batch_write(self, edits):
        raise AssertionError("诊断写了信任或启用配置")
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass


class TrustTests(unittest.TestCase):
    def inspect(self, data):
        s = Server(data)
        result = ct.inspect_listing(data, PID)
        return result

    def test_states_and_counts(self):
        for status, state in (("trusted", ct.TRUSTED), ("modified", ct.NEEDS_TRUST),
                              ("untrusted", ct.NEEDS_TRUST)):
            with self.subTest(status=status):
                r = self.inspect(listing(status))
                self.assertEqual(r.state, state)
                self.assertEqual((r.pending, r.total), (0 if status == "trusted" else len(MANIFEST["entries"]), len(MANIFEST["entries"])))
                if status != "trusted":
                    self.assertIn(f"{r.pending}/{r.total}", r.message)
                    self.assertIn("workframe-door --upgrade", r.message)
                    self.assertIn("重开 Codex 会话", r.message)

    def test_foreign_plugins_and_zero(self):
        data = listing()
        foreign = dict(data["data"][0]["hooks"][0], pluginId="core@other", trustStatus="modified")
        data["data"][0]["hooks"].append(foreign)
        self.assertEqual(self.inspect(data).state, ct.TRUSTED)
        data["data"][0]["hooks"] = [foreign]
        self.assertEqual(self.inspect(data).state, ct.INSTALL_ERROR)

    def test_missing_duplicate_sources_and_manifest(self):
        for mutate in (lambda hs: hs.pop(), lambda hs: hs.append(dict(hs[0])),
                       lambda hs: hs[0].update(sourcePath=str(ROOT / "other/hooks/hooks.codex.json")),
                       lambda hs: hs[0].update(command="")):
            data = listing()
            mutate(data["data"][0]["hooks"])
            self.assertEqual(self.inspect(data).state, ct.INSTALL_ERROR)
        with patch.object(ct, "read_manifest", side_effect=ValueError("bad manifest")):
            self.assertEqual(self.inspect(listing()).state, ct.INSTALL_ERROR)

    def test_protocol_is_unknown(self):
        for data in ({}, {"data": [{"hooks": [None]}]}, listing("mystery")):
            self.assertEqual(self.inspect(data).state, ct.UNVERIFIED)

    def test_diagnose_readonly_scope_and_failures(self):
        home = FIXTURE_HOME
        with patch.object(ct, "project_plugin", return_value=(ct.TRUSTED, PID, "")):
            for response, state in ((listing("modified"), ct.NEEDS_TRUST),
                                    (cas.AppServerError("timeout"), ct.UNVERIFIED)):
                server = Server(response)
                seen = []
                def factory(**kw):
                    seen.append(kw)
                    return server
                r = ct.diagnose(ROOT, home=home, exe="nonexistent-codex", server_factory=factory)
                self.assertEqual(r.state, state)
                self.assertEqual(seen[0]["codex_home"], home)
                self.assertEqual(seen[0]["cwd"], str(ROOT.resolve()))
                self.assertEqual([x[0] for x in server.calls], ["hooks/list"])
            with patch.object(cas, "find_codex_exe", return_value=None):
                self.assertEqual(ct.diagnose(ROOT, home=home).state, ct.UNVERIFIED)
        for state in (ct.NOT_CONNECTED, ct.DISABLED):
            with patch.object(ct, "project_plugin", return_value=(state, None, "skip")), \
                 patch.object(cas, "AppServer", side_effect=AssertionError("不应起进程")):
                self.assertEqual(ct.diagnose(ROOT, home=home).state, state)

    def test_old_python_without_tomllib(self):
        import codex_roles as cr
        with tempfile.TemporaryDirectory() as tmp, patch.dict(sys.modules, {"tomllib": None}):
            proj = Path(tmp)
            self.assertEqual(ct.project_plugin(proj)[0], ct.NOT_CONNECTED)        # 没接门：不碰 tomllib
            (proj / cr.CONFIG_REL).parent.mkdir(parents=True, exist_ok=True)
            (proj / cr.CONFIG_REL).write_text(f"{cr.BLOCK_BEGIN}\n[plugins.\"{PID}\"]\nenabled = true\n{cr.BLOCK_END}\n",
                                              encoding="utf-8", newline="")
            with patch.object(cas, "AppServer", side_effect=AssertionError("不应起进程")):
                r = ct.diagnose(proj, home=FIXTURE_HOME)
            self.assertEqual(r.state, ct.UNVERIFIED)
            self.assertIn("3.11", r.message)
            self.assertNotIn("--upgrade", r.message)

    def test_unverified_points_to_recheck(self):
        with patch.object(ct, "project_plugin", return_value=(ct.TRUSTED, PID, "")), \
             patch.object(cas, "find_codex_exe", return_value=None):
            r = ct.diagnose(ROOT, home=FIXTURE_HOME)
        self.assertEqual(r.state, ct.UNVERIFIED)
        self.assertNotIn("--upgrade", r.message)
        self.assertIn("--check", r.message)
        self.assertNotIn("--upgrade", self.inspect({}).message)

    def test_malformed_installed_manifest(self):
        for installed in ({"hooks": [1]}, {"hooks": {"SessionStart": [1]}},
                          {"hooks": {"SessionStart": [{"hooks": [1]}]}}):
            with patch.object(Path, "read_text", side_effect=[json.dumps(MANIFEST), json.dumps(installed)]):
                self.assertEqual(self.inspect(listing()).state, ct.INSTALL_ERROR)

    def test_home_resolution(self):
        import os
        explicit = FIXTURE_HOME / "explicit"
        cache_root = FIXTURE_HOME / "cache-home/plugins/cache/market/core/1"
        expected = cache_root.parents[4]
        with patch.dict(os.environ, {"CODEX_HOME": str(explicit)}):
            self.assertEqual(ct.codex_home(), explicit)
        with patch.dict(os.environ, {}, clear=True), \
             patch.object(ct._harness, "plugin_root_candidates", return_value=[cache_root]), \
             patch.object(Path, "is_file", return_value=True):
            self.assertEqual(ct.codex_home(), expected.resolve())
        with patch.dict(os.environ, {}, clear=True), \
             patch.object(ct._harness, "plugin_root_candidates", return_value=[]), \
             patch.object(Path, "home", return_value=explicit):
            self.assertEqual(ct.codex_home(), explicit / ".codex")

    def test_diagnostic_never_writes(self):
        server = Server(listing("modified"))
        with patch.object(ct, "project_plugin", return_value=(ct.TRUSTED, PID, "")), \
             patch.object(Path, "write_bytes") as wb, patch.object(Path, "write_text") as wt, \
             patch.object(ct, "write_seed") as audit:
            r = ct.diagnose(ROOT, home=FIXTURE_HOME, exe="absent", server_factory=lambda **kw: server)
            self.assertEqual(r.state, ct.NEEDS_TRUST)
            wb.assert_not_called()
            wt.assert_not_called()
            audit.assert_not_called()

    def test_door_uses_same_verdict(self):
        import workframe_door as wd
        for status in ("trusted", "modified"):
            server = Server(listing(status))
            with patch.object(ct, "project_plugin", return_value=(ct.TRUSTED, PID, "")), \
                 patch.object(cas, "AppServer", return_value=server), \
                 patch.object(wd, "user_layer_enabled", return_value=False), \
                 patch.object(wd, "project_trust_state", return_value="trusted"), \
                 patch.object(wd, "version_mismatch", return_value=None), \
                 patch.object(wd.codex_roles, "verify", return_value=[]), \
                 patch.object(wd, "roles_registered_problems", return_value=[]), \
                 patch.object(wd, "features_multi_agent", return_value="on"), \
                 patch.object(ct, "orphan_count", return_value=0):
                problems, info, mine = wd.check_static("absent", ROOT, "wrong-market")
                verdict = ct.inspect_listing(listing(status), PID)
                self.assertEqual(len(mine), len(MANIFEST["entries"]))
                self.assertIn(verdict.message, info if status == "trusted" else problems)

    def test_closed_door_without_ledger(self):
        import workframe_door as wd
        empty = {"data": [{"hooks": [], "errors": [], "warnings": []}]}
        with patch.object(ct, "project_plugin", return_value=(ct.DISABLED, PID, "关闭")), \
             patch.object(wd.codex_roles, "managed_block_state", return_value=wd.codex_roles.BLOCK_UNLEDGERED), \
             patch.object(cas, "AppServer", return_value=Server(empty)), \
             patch.object(ct, "inspect_listing", side_effect=AssertionError("关闭门不诊断信任")), \
             patch.object(wd, "user_layer_enabled", return_value=False), \
             patch.object(wd, "project_trust_state", return_value="trusted"), \
             patch.object(wd.codex_roles, "verify", return_value=[]), \
             patch.object(wd, "roles_registered_problems", return_value=[]), \
             patch.object(wd, "features_multi_agent", return_value="on"):
            problems, info, mine = wd.check_static("absent", ROOT, "wrong")
            self.assertEqual(problems, [])
        self.assertEqual(doctor.INSTALL_CHECKS[0][0], "legacy_state_dir")

    def test_current_disclosures(self):
        import re
        files = [ROOT / "docs" / n for n in ("concepts.md", "setup-guide.md", "quickstart.md", "context-injection.md", "README.md")]
        files += [ROOT / p for p in ("plugins/core/scripts/_codex_hooks.py", "plugins/core/scripts/gen_codex_hooks.py",
                  "plugins/core/scripts/workframe_door.py", "plugins/core/hooks/hooks.codex.manifest.json",
                  "plugins/workframe-launcher/skills/setup/SKILL.md", "plugins/workframe-launcher/skills/setup/reference/proposal-page.md")]
        forbidden = (r"自动补种", r"需重批", r"只改脚本正文.*不会", r"绕过它直接升级也行", r"裸敲.*也可以")
        for path in files:
            text = path.read_text(encoding="utf-8")
            for pattern in forbidden:
                self.assertIsNone(re.search(pattern, text), f"{path}: {pattern}")
        guide = (ROOT / "docs/setup-guide.md").read_text(encoding="utf-8")
        for phrase in ("Continue without trusting", "非交互", "TUI", "入口内异常可见"):
            self.assertIn(phrase, guide)

    def test_project_managed_scope(self):
        import codex_roles as cr
        for body, state, pid in (("", ct.NOT_CONNECTED, None),
            (cr.BLOCK_BEGIN + '\n[plugins."' + PID + '"]\nenabled = true\n' + cr.BLOCK_END, ct.TRUSTED, PID),
            (cr.BLOCK_BEGIN + '\n[plugins."' + PID + '"]\nenabled = false\n' + cr.BLOCK_END, ct.DISABLED, PID),
            (cr.BLOCK_BEGIN + '\n[plugins."' + PID + '"]\nenabled = [\n' + cr.BLOCK_END, ct.INSTALL_ERROR, None)):
            with patch.object(Path, "read_text", return_value=body):
                got = ct.project_plugin(ROOT)
                self.assertEqual((got[0], got[1]), (state, pid))

    def test_doctor_grades(self):
        ctx = {"paths": SimpleNamespace(project=ROOT)}
        for state, level in ((ct.NOT_CONNECTED, "info"), (ct.DISABLED, "info"),
                             (ct.TRUSTED, "ok"), (ct.NEEDS_TRUST, "error"),
                             (ct.INSTALL_ERROR, "error"), (ct.UNVERIFIED, "warn")):
            with patch.object(ct, "diagnose", return_value=ct.Diagnosis(state, "实时结果")):
                self.assertEqual(doctor.check_codex_trust(ctx), [(level, "实时结果")])

    def test_retirement_and_preserved_keys(self):
        import _codex_hooks as ch
        data = json.loads((PLUGIN / ch.CODEX_MANIFEST_REL).read_text(encoding="utf-8"))
        self.assertNotIn("codex-trust-seed.py", json.dumps(data))
        self.assertFalse((PLUGIN / "scripts/codex-trust-seed.py").exists())
        self.assertEqual(ch.FROZEN_SCRIPTS, ())
        # The retired seed key stays absent as new, unrelated hooks are added.
        self.assertEqual(len(ch.entries(data)), len(MANIFEST["entries"]))
        self.assertNotIn("hooks/hooks.codex.json:session_start:0:5", [r["key"] for r in ch.entries(data)])
        self.assertEqual(ch.build_manifest(data)["entries"], MANIFEST["entries"])

    def test_compound_hash_and_audit_caller(self):
        edits = cas.trust_edits(listing()["data"][0]["hooks"])
        self.assertTrue(edits)
        self.assertTrue(all(e["value"] == "config:content" for e in edits))
        rec = ct.seed_record(PLUGIN, PID, ["key"], seeded_by="workframe-door")
        self.assertEqual(rec["seeded_by"], "workframe-door")


class LaunchPreflightTests(unittest.TestCase):
    def setUp(self):
        temp = Path(tempfile.gettempdir()).resolve()
        root = (temp / f"workframe-launch-{uuid4().hex}").resolve()
        root.relative_to(temp)
        root.mkdir()
        self.addCleanup(shutil.rmtree, root)
        self.project = root / "project"
        self.home = root / "home"
        self.project.mkdir()
        self.home.mkdir()
        for name in ("installation_id", "skills/.system/.codex-system-skills.marker", ".tmp/plugins.sync.lock"):
            path = self.home / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"initialised fixture")
        self.entry = root / "codex.exe"
        self.entry.write_bytes(b"MZfixture" if os.name == "nt" else b"\x7fELFfixture")
        self.options = {"codex_exe": str(self.entry), "codex_home": str(self.home)}
        self.config = {"windows": {"sandbox": "elevated"}}
        self.require_cleanup(patch.dict(os.environ, {"CODEX_SQLITE_HOME": ""}))
        self.require_cleanup(patch.object(runtime, "config_layer_paths", return_value=[self.home / "config.toml"]))
        self.version = self.require_cleanup(patch.object(launch_trust, "_read_version", return_value="0.167.2"))
        owner = Server({"data": [{"cwd": str(self.project), "hooks": [], "errors": []}]})

        def effective(include_layers, cwd):
            owner.calls.append(("config/read", include_layers, cwd))
            return {"config": copy.deepcopy(self.config), "layers": []}

        owner.config_read = effective
        self.owner = owner
        self.factory = self.require_cleanup(patch.object(cas, "AppServer", return_value=owner))

    def require_cleanup(self, context):
        result = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        return result

    def observe(self, **kwargs):
        preflight = getattr(launch_trust, "launch_preflight", None)
        self.assertTrue(callable(preflight), "the private launch observation is unavailable")
        return preflight(self.project, self.options, **kwargs)

    def test_explicit_sandbox_uses_one_config_read_and_one_hook_list(self):
        data, metadata = self.observe()
        self.assertEqual(metadata.get("_sandbox"), "elevated")
        self.assertEqual(data["context"]["codex_version"], "0.167.2")
        self.assertEqual(self.owner.calls, [("config/read", True, self.project.as_posix()),
                                           ("hooks/list", [self.project.as_posix()])])

    def test_missing_sandbox_first_reads_only_configuration_and_final_override_observes_hooks(self):
        self.config.clear()
        _, metadata = self.observe()
        self.assertIn("_sandbox", metadata)
        self.assertIsNone(metadata["_sandbox"])
        self.assertEqual([call[0] for call in self.owner.calls], ["config/read"])

        self.options["codex_config"] = ['windows.sandbox="unelevated"']
        self.config["windows"] = {"sandbox": "unelevated"}
        self.observe(require_sandbox=False, version_hint=metadata["_version_hint"])
        self.assertEqual([call[0] for call in self.owner.calls], ["config/read", "config/read", "hooks/list"])
        self.assertEqual(self.factory.call_args.kwargs["config_overrides"], ('windows.sandbox="unelevated"',))
        self.assertEqual(self.version.call_count, 1)

    def test_launch_allows_runtime_files_without_walking_logs_or_control_directories(self):
        (self.home / "log").mkdir()
        def config_read(include_layers, cwd):
            (self.home / "log" / "codex-tui.log").write_bytes(b"native log")
            (self.home / "state_5.sqlite-wal").write_bytes(b"sqlite companion")
            return {"config": self.config}
        self.owner.config_read = config_read
        with patch.object(os, "walk", side_effect=AssertionError("runtime directory traversed")):
            _, metadata = self.observe()
        self.factory.assert_called_once()
        self.assertNotIn("_audit_error", metadata)

    def test_static_redirect_is_refused_before_a_version_or_server_process(self):
        target = self.project / "new-logs"
        self.options["codex_config"] = [f"log_dir={json.dumps(target.as_posix())}"]
        data, _ = self.observe()
        self.assertEqual([p["code"] for p in data["problems"]], ["protected_path_overlap"])
        self.version.assert_not_called()
        self.factory.assert_not_called()

    def test_relative_profile_path_and_sqlite_environment_use_their_real_base(self):
        self.options["codex_profile"] = "work"
        profile = self.home / "work.config.toml"
        profile.write_bytes(b'log_dir = "../project/logs"\n')
        data, _ = self.observe()
        self.assertEqual([p["code"] for p in data["problems"]], ["protected_path_overlap"])
        profile.write_bytes(b'log_dir = "../safe-logs"\n')
        with patch.dict(os.environ, {"CODEX_SQLITE_HOME": "db"}):
            data, _ = self.observe()
        self.assertEqual([p["code"] for p in data["problems"]], ["protected_path_overlap"])
        self.factory.assert_not_called()

    def test_effective_config_is_checked_before_requesting_hooks(self):
        self.config["sqlite_home"] = self.project.as_posix()
        data, _ = self.observe()
        self.assertEqual([p["code"] for p in data["problems"]], ["protected_path_overlap"])
        self.assertEqual([call[0] for call in self.owner.calls], ["config/read"])

    def test_effective_config_rpc_error_keeps_the_protocol_diagnosis(self):
        self.owner.config_read = Mock(side_effect=cas.AppServerRpcError("fixture unsupported RPC"))
        data, metadata = self.observe()
        self.assertEqual(data["state"], "version_unsupported")
        self.assertEqual([p["code"] for p in data["problems"]], ["protocol_unsupported"])
        self.assertNotIn("_sandbox", metadata)

    def test_effective_config_transport_deadline_keeps_the_timeout_diagnosis(self):
        now = 100.0

        def stalled(*_, **_kwargs):
            nonlocal now
            now = 150.0
            raise cas.AppServerError("fixture expired transport")

        self.owner.config_read = stalled
        with patch.object(time, "monotonic", side_effect=lambda: now):
            data, metadata = self.observe()
        self.assertEqual(data["problems"][0]["code"], "timeout")
        self.assertNotIn("_sandbox", metadata)

    def test_effective_redirect_is_compared_with_the_observed_plugin_location(self):
        loaded_plugin = self.home.parent / "actual-installation"
        loaded_plugin.mkdir()
        self.config["log_dir"] = (loaded_plugin / "new-logs").as_posix()
        self.config.update({"plugins": {PID: {"enabled": True}}})
        # The effective guard also runs after listing to see the loaded root when
        # it differs from the plugin that owns the query script.
        self.owner.response["data"][0]["hooks"] = [{
            "pluginId": PID, "sourcePath": str(loaded_plugin / "hooks/hooks.codex.json"),
        }]
        data, _ = self.observe()
        self.assertEqual([p["code"] for p in data["problems"]], ["protected_path_overlap"])
        self.assertFalse(data["recovery"]["eligible"])

    def test_redirect_through_a_parent_link_is_checked_before_spawn(self):
        redirect = self.home / "redirect"
        try:
            redirect.symlink_to(self.project, target_is_directory=True)
        except OSError as exc:
            if os.name == "nt" and exc.winerror == 1314:
                self.skipTest("Creating a symlink requires developer mode on this Windows host")
            raise
        self.options["codex_config"] = [f"log_dir={json.dumps((redirect / 'new').as_posix())}"]
        data, _ = self.observe()
        self.assertEqual([p["code"] for p in data["problems"]], ["protected_path_overlap"])
        self.version.assert_not_called()
        self.factory.assert_not_called()

    def test_unresolvable_known_path_is_refused_before_spawn(self):
        (self.home / "log").write_bytes(b"mis-shaped runtime path")
        data, _ = self.observe()
        self.assertEqual([p["code"] for p in data["problems"]], ["side_effect_scope_unknown"])
        self.version.assert_not_called()
        self.factory.assert_not_called()

    def test_public_query_keeps_guard_even_if_an_option_claims_launch_purpose(self):
        with patch.object(launch_trust, "protected_overlap", return_value=True) as strict_guard:
            data, _ = launch_trust.query(self.project, {**self.options, "purpose": "launch"})
        strict_guard.assert_called_once()
        self.assertEqual([p["code"] for p in data["problems"]], ["protected_path_overlap"])
        self.factory.assert_not_called()

    def test_replacing_binary_invalidates_version_hint_even_at_same_size_and_mtime(self):
        _, metadata = self.observe()
        hint = metadata["_version_hint"]
        self.observe(version_hint=hint)
        self.assertEqual(self.version.call_count, 1)
        previous = self.entry.stat()
        replacement = self.entry.with_suffix(".replacement")
        replacement.write_bytes(self.entry.read_bytes().replace(b"fixture", b"changed"))
        os.utime(replacement, ns=(previous.st_atime_ns, previous.st_mtime_ns))
        replacement.replace(self.entry)
        self.assertEqual(self.entry.stat().st_size, previous.st_size)
        self.assertEqual(self.entry.stat().st_mtime_ns, previous.st_mtime_ns)
        self.version.return_value = "0.169.1"
        data, _ = self.observe(version_hint=hint)
        self.assertEqual(self.version.call_count, 2)
        self.assertEqual(self.factory.call_count, 2)
        self.assertEqual(data["context"]["codex_version"], "0.169.1")
        self.assertEqual(data["state"], "unverified")

    def test_editing_binary_in_place_invalidates_version_hint_even_with_mtime_restored(self):
        _, metadata = self.observe()
        previous = self.entry.stat()
        with self.entry.open("r+b") as target:
            target.seek(-7, os.SEEK_END)
            target.write(b"changed")
        os.utime(self.entry, ns=(previous.st_atime_ns, previous.st_mtime_ns))
        current = self.entry.stat()
        self.assertEqual((current.st_mtime_ns, current.st_ino, current.st_size),
                         (previous.st_mtime_ns, previous.st_ino, previous.st_size))
        self.version.return_value = "0.169.1"
        data, _ = self.observe(version_hint=metadata["_version_hint"])
        self.assertEqual(self.version.call_count, 2)
        self.assertEqual(data["context"]["codex_version"], "0.169.1")
        self.factory.assert_called_once()

    @unittest.skipUnless(os.name == "nt", "Windows build has the observation evidence")
    def test_0_169_0_can_observe_only_an_active_launch(self):
        self.version.return_value = "0.169.0"
        for name in ("state_5.sqlite", "goals_1.sqlite", "memories_1.sqlite", "queue_1.sqlite"):
            (self.home / name).write_bytes(b"warm fixture")
        _, metadata = self.observe()
        self.assertEqual(metadata["_sandbox"], "elevated")
        self.assertIs(metadata["_launch_can_authorize"], False)
        strict, strict_metadata = launch_trust.query(self.project, self.options)
        self.assertEqual(strict["state"], "unverified")
        self.assertIsNone(strict["snapshot_id"])
        self.assertNotIn("side_effects", strict_metadata)
        self.factory.assert_called_once()

    def test_curated_remote_cache_and_actual_plugin_can_be_siblings(self):
        installed = self.home / "plugins/cache/workframe/core/1"
        installed.mkdir(parents=True)
        remote = self.home / "plugins/cache/openai-curated-remote/core/1"
        remote.mkdir(parents=True)
        self.assertFalse(launch_trust.protected_overlap(self.project, self.home, plugin_root=installed))
        self.assertTrue(launch_trust.protected_overlap(self.project, self.home, plugin_root=remote))
        with patch.object(launch_trust, "PLUGIN_ROOT", installed):
            self.observe()
        self.factory.assert_called_once()
        self.factory.reset_mock()
        with patch.object(launch_trust, "PLUGIN_ROOT", remote):
            data, _ = self.observe()
        self.assertEqual([p["code"] for p in data["problems"]], ["protected_path_overlap"])
        self.factory.assert_not_called()

    def test_close_failure_invalidates_the_observation_before_it_is_returned(self):
        close_error = getattr(cas, "AppServerCloseError", RuntimeError)

        class DidNotClose(Server):
            def __exit__(self, *_):
                raise close_error("fixture server still running")

        owner = DidNotClose(self.owner.response)
        owner.config_read = self.owner.config_read
        self.factory.return_value = owner

        def ready(_adapter, _server, context, _project, **_):
            data = launch_trust.empty(context)
            data.update(state="ready", snapshot_id="sha256:" + "a" * 64)
            return data

        with patch.object(runtime.Codex01672, "observe", ready):
            data, metadata = self.observe()
        self.assertEqual(data["state"], "unverified")
        self.assertIn("exit could not be confirmed", metadata.get("_audit_error", ""))
        self.assertIsNone(data["snapshot_id"])


class AppServerShutdownTests(unittest.TestCase):
    def test_successful_shutdown_waits_for_the_process(self):
        server = cas.AppServer(exe="synthetic")
        server.proc = Mock()
        with patch.object(cas, "kill_tree", return_value=True):
            server.close()
        server.proc.wait.assert_called_once()
        self.assertTrue(server._dead.is_set())

    @unittest.skipUnless(os.name == "nt", "taskkill is a Windows command")
    def test_taskkill_failure_does_not_hide_confirmed_owned_process_exit(self):
        server = cas.AppServer(exe="synthetic")
        server.proc = Mock()
        server.proc.poll.return_value = None
        with patch.object(cas.subprocess, "run", return_value=SimpleNamespace(returncode=1)):
            server.close()
        server.proc.kill.assert_called_once()
        server.proc.wait.assert_called_once()

    def test_report_success_only_after_owned_process_exits(self):
        import subprocess
        server = cas.AppServer(exe="synthetic")
        server.proc = Mock()
        server.proc.wait.side_effect = subprocess.TimeoutExpired("synthetic", 1)
        with patch.object(cas, "kill_tree", return_value=True) as kill_tree:
            with self.assertRaises(getattr(cas, "AppServerCloseError", RuntimeError)):
                server.close()
        kill_tree.assert_called_once()
        server.proc.wait.assert_called_once()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    unittest.main()
