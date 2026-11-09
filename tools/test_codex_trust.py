#!/usr/bin/env python3
"""只读信任诊断状态矩阵；假 transport，绝不启动本机 Codex。"""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins/core/scripts"))
import _codex_trust as ct
import _codex_appserver as cas
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


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    unittest.main()
