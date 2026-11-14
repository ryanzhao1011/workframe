#!/usr/bin/env python3
"""Source-aware installation tests; all user files live in an os.makedirs home."""

import builtins
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
import uuid
from contextlib import redirect_stdout
from unittest.mock import patch

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "plugins/core/scripts"
sys.path.insert(0, str(SCRIPTS))


def put(path, value):
    os.makedirs(str(path.parent), exist_ok=True)
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    with path.open("w", encoding="utf-8", newline="") as stream:
        stream.write(text)


def snapshot(root):
    return {path.relative_to(root).as_posix():
            hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "directory"
            for path in root.rglob("*")}


class InstallSourceTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / "tmp" / ("install-source-" + uuid.uuid4().hex)
        self.home = self.root / "home"
        self.project = self.root / "new project"
        self.market = self.root / "market"
        for path in (self.home, self.market, self.home / ".claude", self.home / ".codex/.tmp"):
            os.makedirs(str(path), exist_ok=True)
        self.addCleanup(shutil.rmtree, str(self.root))
        self.env = dict(os.environ, HOME=str(self.home), USERPROFILE=str(self.home),
                        CLAUDE_CONFIG_DIR=str(self.home / ".claude"),
                        CODEX_HOME=str(self.home / ".codex"),
                        WF_CODEX_EXE=str(self.root / "no-cli.exe"),
                        PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
        self.environ = patch.dict(os.environ, self.env)
        self.environ.start()
        self.addCleanup(self.environ.stop)
        self.registry = self.home / ".claude/plugins/known_marketplaces.json"
        self.codex_config = self.home / ".codex/config.toml"
        self.source = {"kind": "directory", "source": str(self.market)}
        self.subscription = {"marketplace_name": "workframe",
                             "source": {"source": "directory", "path": str(self.market)},
                             "install_location": str(self.market)}
        self.params = self.root / "params.json"
        put(self.params, {"project_name": "Sample", "one_line_goal": "A sample project",
                          "business_context": "An isolated installation test",
                          "subscription": self.subscription})

    def scaffold(self, expected=None, *extra):
        args = [sys.executable, "-B", str(SCRIPTS / "project_scaffold.py"),
                "--project", str(self.project), "--params", str(self.params),
                "--create-missing", "--write-subscription"]
        if expected is not None:
            args += ["--expect-source", expected if isinstance(expected, str) else json.dumps(expected)]
        return subprocess.run(args + list(extra), cwd=str(ROOT), env=self.env,
                              capture_output=True, text=True, encoding="utf-8", timeout=30)

    def cc_register(self, source):
        put(self.registry, {"workframe": {"source": source, "installLocation": str(self.market)},
                            "unrelated": {"keep": True}})

    def codex_register(self, kind, source):
        put(self.codex_config, '[marketplaces.workframe]\nsource_type = '
            + json.dumps(kind) + '\nsource = ' + json.dumps(source) + '\n')

    def door(self, expected, *, upgrade=False, marketplace=None):
        import workframe_door as door
        os.makedirs(str(self.project), exist_ok=True)
        put(self.project / ".workframe-config.json", {})
        args = ["--upgrade" if upgrade else "--codex", "--project", str(self.project),
                "--expect-source", json.dumps(expected)]
        if marketplace is not None:
            args += ["--marketplace", marketplace]
        import _marketplace_source as source

        def resolved(exe, candidate, expected, home, run):
            source.require_codex(expected, "workframe", candidate, home)
            return "workframe"

        # Stop immediately after the source check. Never invoke a real CLI.
        with patch.object(door, "step_preflight", side_effect=door.DoorError(4, "continued")) as next_step, \
                patch.object(door, "codex_exe", return_value=str(self.root / "no-cli.exe")), \
                patch.object(source, "probe_codex_market", side_effect=resolved):
            with redirect_stdout(io.StringIO()) as output:
                code = door.main(args)
        return code, output.getvalue(), next_step

    def test_scaffold_matching_directory_runs_real_install(self):
        self.cc_register(self.subscription["source"])
        result = self.scaffold(self.source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.project / "AGENTS.md").is_file())
        landed = json.loads((self.project / ".claude/settings.json").read_text(encoding="utf-8"))
        self.assertEqual(landed["extraKnownMarketplaces"]["workframe"]["source"],
                         {"source": "directory", "path": str(self.market)})

    def test_scaffold_mismatch_is_zero_write(self):
        self.cc_register({"source": "git", "url": "https://example.invalid/other.git"})
        before = snapshot(self.root)
        result = self.scaffold(self.source)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("source_mismatch", result.stdout)
        self.assertEqual(snapshot(self.root), before)

    def test_scaffold_absent_registration_installs_expected_source(self):
        result = self.scaffold(self.source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(self.registry.read_text(encoding="utf-8"))["workframe"]["source"],
                         {"source": "directory", "path": str(self.market)})

    def test_scaffold_bad_registration_is_zero_write(self):
        for value in ("{broken", "", "[]", '{"workframe": null}',
                      '{"workframe": {"source": {"source": "unknown"}}}',
                      '{"workframe": {"source": {"source": "directory", "path": ""}}}',
                      '{"workframe": {}, "workframe": {}}'):
            with self.subTest(value=value):
                put(self.registry, value)
                before = snapshot(self.root)
                result = self.scaffold(self.source)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("source_unverified", result.stdout)
                self.assertEqual(snapshot(self.root), before)

    def test_scaffold_expected_source_must_match_subscription(self):
        before = snapshot(self.root)
        result = self.scaffold({"kind": "git", "source": "owner/other"})
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("source_mismatch", result.stdout)
        self.assertEqual(snapshot(self.root), before)

    def test_scaffold_matching_git_and_bom_registry(self):
        self.subscription["source"] = {"source": "github", "repo": "owner/repo"}
        data = json.loads(self.params.read_text(encoding="utf-8"))
        data["subscription"] = self.subscription
        put(self.params, data)
        put(self.registry, chr(0xFEFF) + json.dumps({"workframe": {
            "source": {"source": "git", "url": "owner/repo"}, "installLocation": str(self.market)}}))
        result = self.scaffold({"kind": "git", "source": "owner/repo"})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_scaffold_path_alias_preserves_registered_source(self):
        alias = str(self.market / ".." / "market")
        self.cc_register({"source": "directory", "path": alias})
        before = self.registry.read_bytes()
        result = self.scaffold(self.source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.registry.read_bytes(), before)

    def test_scaffold_relative_source_lands_as_verified_absolute_directory(self):
        data = json.loads(self.params.read_text(encoding="utf-8"))
        data["subscription"]["source"]["path"] = os.path.relpath(str(self.market), str(ROOT))
        put(self.params, data)
        result = self.scaffold(self.source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        landed = json.loads((self.project / ".claude/settings.json").read_text(encoding="utf-8"))
        path = landed["extraKnownMarketplaces"]["workframe"]["source"]["path"]
        self.assertTrue(Path(path).is_absolute(), path)
        self.assertEqual(os.path.normcase(str(Path(path).resolve())), os.path.normcase(str(self.market.resolve())))

    def test_scaffold_existing_relative_declaration_is_resolved_from_project(self):
        relative = os.path.relpath(str(self.market), str(self.project))
        put(self.project / ".claude/settings.json", {"extraKnownMarketplaces": {
            "workframe": {"source": {"source": "directory", "path": relative}}}})
        result = self.scaffold(self.source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_scaffold_wrong_project_relative_declaration_stops_without_writes(self):
        relative = os.path.relpath(str(self.market), str(ROOT))
        put(self.project / ".claude/settings.json", {"extraKnownMarketplaces": {
            "workframe": {"source": {"source": "directory", "path": relative}}}})
        before = snapshot(self.root)
        result = self.scaffold(self.source)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("source_mismatch", result.stdout)
        self.assertEqual(snapshot(self.root), before)

    def test_scaffold_relative_source_plan_uses_same_identity_as_write(self):
        self.cc_register(self.subscription["source"])
        data = json.loads(self.params.read_text(encoding="utf-8"))
        data["subscription"]["source"]["path"] = os.path.relpath(str(self.market), str(ROOT))
        put(self.params, data)
        before = snapshot(self.root)
        result = self.scaffold(self.source, "--print-plan")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("被别的源占用", result.stdout)
        self.assertEqual(snapshot(self.root), before)

    def test_scaffold_invalid_expected_source_rejected_before_writes(self):
        for raw in ('{}', '{"kind":"unknown","source":null}',
                    '{"kind":"directory","source":""}',
                    '{"kind":"git","kind":"directory","source":"x"}',
                    '{"kind":"git","source":NaN}'):
            with self.subTest(raw=raw):
                before = snapshot(self.root)
                result = self.scaffold(raw)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("bad_usage", result.stderr)
                self.assertEqual(snapshot(self.root), before)

    def test_scaffold_print_plan_checks_source_without_writes(self):
        self.cc_register({"source": "git", "url": "owner/other"})
        before = snapshot(self.root)
        result = self.scaffold(self.source, "--print-plan")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("source_mismatch", result.stdout)
        self.assertEqual(snapshot(self.root), before)

    def test_write_subscription_guard_cannot_be_bypassed_by_direct_call(self):
        import project_scaffold as scaffold
        self.cc_register({"source": "git", "url": "owner/other"})
        before = snapshot(self.root)
        ok, lines, detail = scaffold.write_subscription(self.project, self.subscription,
                                                        expected_source=self.source)
        self.assertFalse(ok)
        self.assertIn("source_mismatch", "\n".join(lines))
        self.assertTrue(detail)
        self.assertEqual(snapshot(self.root), before)

    def test_codex_mismatch_stops_before_any_install_step(self):
        self.codex_register("local", str(self.market))
        before = self.codex_config.read_bytes()
        code, output, next_step = self.door({"kind": "git", "source": "owner/repo"})
        self.assertEqual(code, 2, output)
        self.assertIn("source_mismatch", output)
        next_step.assert_not_called()
        self.assertEqual(self.codex_config.read_bytes(), before)

    def test_codex_matching_and_absent_registration_continue(self):
        for registered in (False, True):
            with self.subTest(registered=registered):
                if registered:
                    self.codex_register("local", str(self.market / ".." / "market"))
                code, output, next_step = self.door(self.source)
                self.assertEqual(code, 4, output)
                next_step.assert_called_once()

    def test_codex_bad_registry_stops_without_repair(self):
        for raw in ('bad toml {', '[marketplaces]\nworkframe = false\n',
                    '[marketplaces.workframe]\nsource_type = "local"\n',
                    '[marketplaces.workframe]\nsource_type = "other"\nsource = "x"\n'):
            with self.subTest(raw=raw):
                put(self.codex_config, raw)
                before = self.codex_config.read_bytes()
                code, output, next_step = self.door(self.source)
                self.assertEqual(code, 2, output)
                self.assertIn("source_unverified", output)
                next_step.assert_not_called()
                self.assertEqual(self.codex_config.read_bytes(), before)

    def test_codex_explicit_source_mismatch_stops_before_registration(self):
        code, output, next_step = self.door(self.source, marketplace="owner/other")
        self.assertEqual(code, 2, output)
        self.assertIn("source_mismatch", output)
        next_step.assert_not_called()

    def test_codex_upgrade_checks_existing_source(self):
        self.codex_register("git", "https://example.invalid/repo.git")
        code, output, next_step = self.door(self.source, upgrade=True)
        self.assertEqual(code, 2, output)
        self.assertIn("source_mismatch", output)
        next_step.assert_not_called()

    def test_default_scaffold_keeps_conflict_behavior(self):
        self.cc_register({"source": "git", "url": "owner/other"})
        before = self.registry.read_bytes()
        result = self.scaffold()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.registry.read_bytes(), before)

    def test_project_source_conflict_rejects_before_any_write(self):
        put(self.project / ".claude/settings.json", {"extraKnownMarketplaces": {
            "workframe": {"source": {"source": "github", "repo": "owner/other"}}}})
        before = snapshot(self.root)
        result = self.scaffold(self.source)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("source_mismatch", result.stdout)
        self.assertEqual(snapshot(self.root), before)

    def test_codex_matching_git_continues_without_rewriting_registry(self):
        raw = "https://example.invalid/repo.git"
        self.codex_register("git", raw)
        before = self.codex_config.read_bytes()
        code, output, next_step = self.door({"kind": "git", "source": raw})
        self.assertEqual(code, 4, output)
        next_step.assert_called_once()
        self.assertEqual(self.codex_config.read_bytes(), before)

    def test_codex_same_raw_value_with_different_kind_rejects(self):
        raw = os.path.normcase(str(self.market))
        self.codex_register("local", raw)
        code, output, next_step = self.door({"kind": "git", "source": raw})
        self.assertEqual(code, 2, output)
        self.assertIn("source_mismatch", output)
        next_step.assert_not_called()

    def test_codex_git_source_uses_exact_registered_value(self):
        self.codex_register("git", "https://example.invalid/repo.git")
        code, output, next_step = self.door({"kind": "git", "source": "https://example.invalid/other.git"})
        self.assertEqual(code, 2, output)
        self.assertIn("source_mismatch", output)
        next_step.assert_not_called()

    def test_codex_expected_source_becomes_the_registration_argument(self):
        import _marketplace_source as source
        with patch.object(source, "require_codex", wraps=source.require_codex) as guard:
            code, output, _ = self.door(self.source)
        self.assertEqual(code, 4, output)
        self.assertEqual(guard.call_args[0][2], os.path.normcase(str(self.market.resolve())))

    def test_default_codex_does_not_read_registry_before_legacy_preflight(self):
        import workframe_door as door
        put(self.codex_config, "broken toml {")
        os.makedirs(str(self.project), exist_ok=True)
        put(self.project / ".workframe-config.json", {})
        with patch.object(door, "step_preflight", side_effect=door.DoorError(4, "continued")) as next_step:
            with redirect_stdout(io.StringIO()):
                code = door.main(["--codex", "--project", str(self.project)])
        self.assertEqual(code, 4)
        next_step.assert_called_once()

    def missing_runtime(self, mode, *, expected=True):
        import workframe_door as door
        os.makedirs(str(self.project), exist_ok=True)
        put(self.project / ".workframe-config.json", {})
        self.codex_register("local", str(self.market))
        before = snapshot(self.root)
        args = ([mode] if mode else []) + ["--project", str(self.project)]
        if expected:
            args += ["--expect-source", json.dumps(self.source)]
        imports = []
        real_import = builtins.__import__

        def unavailable(name, *positional, **keywords):
            if name == "tomllib":
                imports.append(name)
                raise ModuleNotFoundError("no tomllib", name=name)
            return real_import(name, *positional, **keywords)

        def write_if_reached(*_args, **_kwargs):
            put(self.codex_config, "unexpected write")

        with patch.object(builtins, "__import__", side_effect=unavailable), \
                patch.object(door.sys, "version_info", (3, 8, 17)), \
                patch.object(door, "do_codex", side_effect=write_if_reached) as install, \
                patch.object(door, "say_agents_md_notes"), \
                patch.object(door, "check_cc", return_value=([], [])), \
                patch.object(door.cas, "find_codex_exe", return_value=str(self.root / "no-cli.exe")):
            with redirect_stdout(io.StringIO()) as output:
                code = door.main(args)
        return code, output.getvalue(), install, imports, before, snapshot(self.root)

    def test_missing_tomllib_source_modes_return_environment_error(self):
        for mode in ("--codex", "--upgrade"):
            with self.subTest(mode=mode):
                code, output, _install, imports, _before, _after = self.missing_runtime(mode)
                self.assertEqual(code, 2, output)
                self.assertEqual(imports, ["tomllib"])

    def test_missing_tomllib_source_modes_do_not_write_or_start_install(self):
        for mode in ("--codex", "--upgrade"):
            with self.subTest(mode=mode):
                _code, _output, install, _imports, before, after = self.missing_runtime(mode)
                self.assertEqual(after, before)
                install.assert_not_called()

    def test_missing_tomllib_message_identifies_required_and_current_python(self):
        for mode in ("--codex", "--upgrade"):
            with self.subTest(mode=mode):
                _code, output, _install, _imports, _before, _after = self.missing_runtime(mode)
                self.assertIn("Python 3.11", output)
                self.assertIn("或更高", output)
                self.assertIn("当前 Python 3.8.17", output)
                self.assertIn("tomllib", output)

    def test_default_modes_do_not_gain_the_missing_tomllib_gate(self):
        for mode in ("--codex", "--upgrade", "--both", None):
            with self.subTest(mode=mode):
                code, output, install, imports, _before, _after = self.missing_runtime(mode, expected=False)
                self.assertEqual(code, 0, output)
                install.assert_called_once()
                self.assertEqual(imports, [])

    def test_available_tomllib_source_modes_continue_the_existing_flow(self):
        import workframe_door as door
        os.makedirs(str(self.project), exist_ok=True)
        put(self.project / ".workframe-config.json", {})
        for mode in ("--codex", "--upgrade"):
            with self.subTest(mode=mode), patch.object(door, "do_codex") as install, \
                    patch.object(door, "say_agents_md_notes"), redirect_stdout(io.StringIO()):
                code = door.main([mode, "--project", str(self.project),
                                  "--expect-source", json.dumps(self.source)])
                self.assertEqual(code, 0)
                install.assert_called_once()

    def test_cli_missing_tomllib_preserves_project_registry_and_plugin(self):
        # Run without -B or PYTHONDONTWRITEBYTECODE: the entry point itself must
        # keep the rejected source-aware command from creating plugin pyc files.
        plugin = self.root / "plugin"
        shutil.copytree(str(SCRIPTS), str(plugin / "scripts"), ignore=shutil.ignore_patterns("__pycache__"))
        os.makedirs(str(plugin / "bin"), exist_ok=True)
        shutil.copyfile(str(ROOT / "plugins/core/bin/workframe-door"), str(plugin / "bin/workframe-door"))
        os.makedirs(str(self.project), exist_ok=True)
        put(self.project / ".workframe-config.json", {})
        self.codex_register("local", str(self.market))
        driver = self.root / "driver.py"
        put(driver, 'import builtins, runpy, sys\n'
            'original = builtins.__import__\n'
            'def unavailable(name, *args, **kwargs):\n'
            '    if name == "tomllib":\n'
            '        raise ModuleNotFoundError("no tomllib", name=name)\n'
            '    return original(name, *args, **kwargs)\n'
            'builtins.__import__ = unavailable\n'
            'sys.argv = sys.argv[1:]\n'
            'runpy.run_path(sys.argv[0], run_name="__main__")\n')
        env = dict(self.env)
        env.pop("PYTHONDONTWRITEBYTECODE", None)
        for entry in (plugin / "scripts/workframe_door.py", plugin / "bin/workframe-door"):
            for mode in ("--codex", "--upgrade"):
                for option in ("--expect-source", "--expect-s", "--e"):
                    for equals in (False, True):
                        with self.subTest(entry=entry.name, mode=mode, option=option, equals=equals):
                            flag = ([option + "=" + json.dumps(self.source)] if equals
                                    else [option, json.dumps(self.source)])
                            before = snapshot(self.root)
                            result = subprocess.run([sys.executable, str(driver), str(entry), mode,
                                                     "--project", str(self.project)] + flag,
                                                    cwd=str(ROOT), env=env, capture_output=True,
                                                    text=True, encoding="utf-8", timeout=15)
                            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                            self.assertIn("Python 3.11", result.stdout)
                            self.assertIn("当前 Python " + sys.version.split()[0], result.stdout)
                            self.assertEqual(snapshot(self.root), before)

    def test_shared_source_conversion_and_directory_prefix(self):
        import _marketplace_source as source
        self.assertEqual(source.claude_source({"source": "github", "repo": "owner/repo"}),
                         {"kind": "git", "source": "owner/repo"})
        self.assertEqual(source.codex_source({"source_type": "git", "source": "owner/repo"}),
                         {"kind": "git", "source": "owner/repo"})
        self.assertEqual(source.norm_path("\\\\?\\" + str(self.market)),
                         os.path.normcase(str(self.market.resolve())))

    def test_source_comparison_failure_refuses_cleanly(self):
        import _marketplace_source as source
        with patch.object(source, "norm_path", side_effect=OSError("unreadable path")):
            with self.assertRaisesRegex(source.SourceError, "source_unverified"):
                source.require_same(self.source, self.source, "test")

    def test_direct_subscription_rejects_duplicate_registry_before_writes(self):
        import project_scaffold as scaffold
        entry = json.dumps({"source": self.subscription["source"], "installLocation": str(self.market)})
        put(self.registry, '{"workframe": {}, "workframe": ' + entry + '}')
        before = snapshot(self.root)
        ok, lines, _ = scaffold.write_subscription(self.project, self.subscription, expected_source=self.source)
        self.assertFalse(ok)
        self.assertIn("source_unverified", "\n".join(lines))
        self.assertEqual(snapshot(self.root), before)

    def test_direct_subscription_rejects_corrupt_registry_before_writes(self):
        import project_scaffold as scaffold
        put(self.registry, "{broken")
        before = snapshot(self.root)
        ok, lines, _ = scaffold.write_subscription(self.project, self.subscription, expected_source=self.source)
        self.assertFalse(ok)
        self.assertIn("source_unverified", "\n".join(lines))
        self.assertEqual(snapshot(self.root), before)

    def through_registration(self, *, requested_name="workframe", marketplace=None, probe_source=None,
                             reply=None, probe_error=None):
        """Exercise the real add call boundary, with Codex itself as a transport stub."""
        import workframe_door as door
        os.makedirs(str(self.project), exist_ok=True)
        put(self.project / ".workframe-config.json", {})
        args = ["--codex", "--project", str(self.project), "--marketplace-name", requested_name,
                "--expect-source", json.dumps(self.source)]
        if marketplace is not None:
            args += ["--marketplace", marketplace]
        calls = []

        def cli(exe, command, cwd=None, timeout=180, env=None):
            calls.append((command, Path(cwd), dict(env) if env else None))
            if env is not None:
                self.assertNotEqual(env["CODEX_HOME"], str(self.home / ".codex"))
                if probe_error is not None:
                    raise probe_error
                put(Path(env["CODEX_HOME"]) / "config.toml",
                    '[marketplaces.workframe]\nsource_type = "local"\nsource = '
                    + json.dumps(probe_source or str(self.market)) + '\n')
            return 0, json.dumps(reply if reply is not None else {
                "marketplaceName": "workframe", "alreadyAdded": False}), ""

        with patch.object(door, "step_preflight"), patch.object(door, "step_version"), \
                patch.object(door, "codex_exe", return_value=str(self.root / "no-cli.exe")), \
                patch.object(door, "run_codex", side_effect=cli), \
                patch.object(door, "step_plugin_add", side_effect=door.DoorError(4, "after registration")):
            with redirect_stdout(io.StringIO()) as output:
                code = door.main(args)
        return code, output.getvalue(), calls

    def test_actual_market_name_conflict_is_checked_before_real_registration(self):
        self.codex_register("local", str(self.root / "old market"))
        before = self.codex_config.read_bytes()
        code, output, calls = self.through_registration(requested_name="custom-alias")
        self.assertEqual(code, 2, output)
        self.assertIn("source_mismatch", output)
        self.assertEqual(len(calls), 1)  # only the private-home resolver
        self.assertIsNotNone(calls[0][2])
        self.assertEqual(self.codex_config.read_bytes(), before)
        self.assertEqual(list((self.home / ".codex/.tmp").iterdir()), [])

    def test_actual_market_name_matching_registration_continues(self):
        self.codex_register("local", str(self.market))
        code, output, calls = self.through_registration(requested_name="custom-alias")
        self.assertEqual(code, 4, output)
        self.assertEqual(len(calls), 2)
        self.assertIsNotNone(calls[0][2])
        self.assertIsNone(calls[1][2])
        self.assertEqual(calls[1][1], self.project)

    def test_unrelated_requested_alias_does_not_block_actual_matching_market(self):
        self.codex_register("local", str(self.market))
        with self.codex_config.open("a", encoding="utf-8", newline="") as stream:
            stream.write('[marketplaces.custom_alias]\nsource_type = "git"\nsource = "owner/other"\n')
        code, output, calls = self.through_registration(requested_name="custom_alias")
        self.assertEqual(code, 4, output)
        self.assertEqual(len(calls), 2)

    def test_relative_marketplace_uses_verified_absolute_path_for_real_registration(self):
        relative = os.path.relpath(str(self.market), str(Path.cwd()))
        code, output, calls = self.through_registration(marketplace=relative)
        self.assertEqual(code, 4, output)
        self.assertEqual(len(calls), 2)
        expected = os.path.normcase(str(self.market.resolve()))
        self.assertEqual([call[0][3] for call in calls], [expected, expected])

    def test_isolated_resolution_rejects_unexpected_source(self):
        code, output, calls = self.through_registration(probe_source=str(self.root / "wrong source"))
        self.assertEqual(code, 2, output)
        self.assertIn("source_mismatch", output)
        self.assertEqual(len(calls), 1)
        self.assertFalse(self.codex_config.exists())

    def test_isolated_resolution_must_report_actual_name(self):
        code, output, calls = self.through_registration(reply={})
        self.assertEqual(code, 2, output)
        self.assertIn("source_unverified", output)
        self.assertEqual(len(calls), 1)
        self.assertFalse(self.codex_config.exists())

    def test_isolated_resolution_timeout_stops_before_real_registration(self):
        code, output, calls = self.through_registration(probe_error=subprocess.TimeoutExpired("codex", 180))
        self.assertEqual(code, 2, output)
        self.assertIn("source_unverified", output)
        self.assertEqual(len(calls), 1)
        self.assertFalse(self.codex_config.exists())
        self.assertEqual(list((self.home / ".codex/.tmp").iterdir()), [])

    def test_isolated_resolution_requires_recorded_source(self):
        import _marketplace_source as source
        before = snapshot(self.root)
        with self.assertRaisesRegex(source.SourceError, "source_unverified"):
            source.require_codex(self.source, "workframe", str(self.market), self.home / ".codex",
                                 require_present=True)
        self.assertEqual(snapshot(self.root), before)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    unittest.main(verbosity=2)
