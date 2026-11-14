#!/usr/bin/env python3
"""Read-only query contract tests using isolated projects and user homes."""

import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import shutil
import shlex
import struct
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "plugins" / "core"
SCRIPTS = CORE / "scripts"
QUERY = SCRIPTS / "workframe_query.py"
sys.path.insert(0, str(SCRIPTS))

from _state_io import legacy_rel, state_dir_of  # noqa: E402
import _harness  # noqa: E402
import project_scaffold as scaffold  # noqa: E402
import workframe_doctor as doctor  # noqa: E402


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    path.write_text(text, encoding="utf-8", newline="")


def expected_hook_trust_name(config_file, plugin_id):
    """Independent test oracle from the pinned upstream byte contract."""
    parts = (str(config_file).encode("utf-8"), plugin_id.encode("utf-8"))
    return hashlib.sha256(b"".join(struct.pack("<Q", len(part)) + part for part in parts)).hexdigest()


def snapshot(root):
    """Include directories, links, and every file's bytes, including hidden files."""
    out = {}

    def walk(directory):
        for entry in sorted(directory.iterdir()):
            rel = entry.relative_to(root).as_posix()
            if _harness.is_dir_link(entry) or entry.is_symlink():
                out[rel] = ("link", os.readlink(entry))
            elif entry.is_dir():
                out[rel + "/"] = ("directory",)
                walk(entry)
            else:
                out[rel] = ("file", hashlib.sha256(entry.read_bytes()).hexdigest())

    walk(root)
    return out


class QueryFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wf-query-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / "sample project"
        self.project.mkdir()
        self.home = self.root / "home"
        self.home.mkdir()
        self.claude_home = self.home / ".claude"
        self.codex_home = self.home / ".codex"
        self.env = dict(os.environ, HOME=str(self.home), USERPROFILE=str(self.home),
                        CLAUDE_CONFIG_DIR=str(self.claude_home),
                        CODEX_HOME=str(self.codex_home), PYTHONUTF8="1",
                        PYTHONIOENCODING="utf-8", GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_CONFIG_NOSYSTEM="1")
        # Test the entry point's own bytecode suppression, not the test runner's.
        self.env.pop("PYTHONDONTWRITEBYTECODE", None)

    def cli(self, *args, script=QUERY):
        self.assertTrue(script.is_file(), "query entry point must exist")
        result = subprocess.run([sys.executable, str(script), *map(str, args)],
                                env=self.env, cwd=self.root, capture_output=True,
                                timeout=15)
        self.assertFalse(result.stdout.startswith(bytes([239, 187, 191])), "no BOM")
        raw = result.stdout.decode("utf-8")
        value = json.loads(raw)
        self.assertIsInstance(value, dict)
        self.assertEqual(value["schema"], "workframe.query.v1")
        self.assertIn("depends_on", value)
        return result.returncode, value, result.stderr.decode("utf-8")

    def query(self, *args, script=QUERY):
        return self.cli("project-status", "--project", self.project, *args, script=script)

    def connect(self):
        put(self.project / ".workframe-config.json", {"project_name": "sample"})

    def git(self, *args, directory=None):
        result = subprocess.run(["git", *map(str, args)], cwd=directory or self.project,
                                env=self.env, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def commit_fixture(self, directory=None):
        # Traverse the fixture so Git applies its ignore rules to scaffold logs.
        # Explicitly naming ignored children aborts before zero-write assertions.
        self.git("add", "--all", "--", ".", directory=directory)
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                 "-c", "commit.gpgsign=false", "commit", "-qm", "fixture", directory=directory)

    def install_identity_filter(self, directory, driver="fixture"):
        git_dir = Path(self.git("rev-parse", "--absolute-git-dir", directory=directory).stdout.decode("utf-8").strip())
        log = git_dir / "fixture-clean.log"
        hook = git_dir / "hooks" / "fixture-clean"
        put(hook, "#!/bin/sh\nprintf 'filter-ran\\n' >> " + shlex.quote(log.as_posix()) + "\ncat\n")
        hook.chmod(0o755)
        self.git("config", f"filter.{driver}.clean", shlex.quote(hook.as_posix()), directory=directory)
        return log

    def touch_tracked(self, path):
        stamp = path.stat().st_mtime_ns + 2_000_000_000
        os.utime(path, ns=(stamp, stamp))


class QueryCliTests(QueryFixture):
    def test_subdirectory_query_checks_parent_filters_and_returns_parent_dependencies(self):
        self.git("init", "-q")
        readme = self.project / "README.md"
        child = self.project / "child"
        put(readme, "tracked\n")
        put(self.project / ".gitattributes", "README.md filter=fixture\n")
        put(child / "source.py", "pass\n")
        self.commit_fixture()
        log = self.install_identity_filter(self.project)
        self.touch_tracked(readme)
        self.git("status", "--porcelain")
        self.assertEqual(log.read_bytes(), b"filter-ran\n", "parent filter positive control")
        log.write_bytes(b"")
        self.touch_tracked(readme)
        before = snapshot(self.project)
        code, value, _ = self.cli("project-status", "--project", child)
        self.assertEqual(log.read_bytes(), b"", "query scope must match status scope")
        self.assertEqual(snapshot(self.project), before)
        self.assertEqual((code, value["error"]["code"]), (1, "internal"))
        self.assertIn("../", value["depends_on"])
        self.assertIn("../.git/", value["depends_on"])

    def test_reserved_attribute_display_names_cannot_hide_external_filters(self):
        self.git("init", "-q")
        readme = self.project / "README.md"
        put(readme, "tracked\n")
        put(self.project / ".gitattributes", "README.md filter=fixture\n")
        self.commit_fixture()
        for driver in ("set", "unset", "unspecified"):
            with self.subTest(driver=driver):
                put(self.project / ".gitattributes", f"README.md filter={driver}\n")
                self.commit_fixture()
                log = self.install_identity_filter(self.project, driver=driver)
                log.write_bytes(b"")
                self.touch_tracked(readme)
                self.git("status", "--porcelain")
                self.assertEqual(log.read_bytes(), b"filter-ran\n", "named filter positive control")
                log.write_bytes(b"")
                self.touch_tracked(readme)
                before = snapshot(self.project)
                code, value, _ = self.query()
                self.assertEqual(log.read_bytes(), b"", "display name must not bypass read-only guard")
                self.assertEqual(snapshot(self.project), before)
                self.assertEqual((code, value["error"]["code"]), (1, "internal"))

    def test_process_filter_is_rejected_and_missing_process_guard_is_detected(self):
        self.git("init", "-q")
        readme = self.project / "README.md"
        put(readme, "tracked\n")
        put(self.project / ".gitattributes", "README.md filter=fixture\n")
        self.commit_fixture()
        hook = self.project / ".git" / "hooks" / "fixture-process"
        log = self.project / ".git" / "fixture-process.log"
        put(hook, "#!/bin/sh\nprintf 'process-ran\\n' >> " + shlex.quote(log.as_posix()) + "\nexit 1\n")
        hook.chmod(0o755)
        self.git("config", "filter.fixture.process", shlex.quote(hook.as_posix()))
        self.touch_tracked(readme)
        subprocess.run(["git", "status", "--porcelain"], cwd=self.project, env=self.env,
                       capture_output=True, timeout=15)
        self.assertEqual(log.read_bytes(), b"process-ran\n", "process filter must really start")
        log.write_bytes(b"")
        self.touch_tracked(readme)
        before = snapshot(self.project)
        code, value, _ = self.query()
        self.assertEqual(log.read_bytes(), b"")
        self.assertEqual(snapshot(self.project), before)
        self.assertEqual((code, value["error"]["code"]), (1, "internal"))
        self.assertEqual(value["error"]["detail"]["reason"], "external_git_filter")
        plugin = self.root / "process-guard-mutant"
        shutil.copytree(CORE, plugin, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        script = plugin / "scripts" / "workframe_query.py"
        source = script.read_text(encoding="utf-8")
        anchor = r"^filter\..*\.(clean|process)$"
        self.assertEqual(source.count(anchor), 1)
        mutated = source.replace(anchor, r"^filter\..*\.clean$")
        self.assertNotEqual(mutated, source)
        script.write_text(mutated, encoding="utf-8", newline="")
        before = snapshot(self.project)
        self.query(script=script)
        self.assertEqual(log.read_bytes(), b"process-ran\n", "removing only the process branch must expose the write")
        with self.assertRaises(AssertionError):
            self.assertEqual(snapshot(self.project), before)

    def test_external_clean_filter_is_rejected_before_any_write(self):
        self.git("init", "-q")
        readme = self.project / "README.md"
        put(readme, "tracked\n")
        put(self.project / ".gitattributes", "README.md filter=fixture\n")
        self.commit_fixture()
        log = self.install_identity_filter(self.project)
        self.touch_tracked(readme)
        self.git("status", "--porcelain")
        self.assertEqual(log.read_bytes(), b"filter-ran\n", "positive control must execute clean filter")
        log.write_bytes(b"")
        self.touch_tracked(readme)
        before = snapshot(self.project)
        code, value, _ = self.query()
        self.assertEqual(log.read_bytes(), b"", "query must reject before invoking the clean filter")
        self.assertEqual(snapshot(self.project), before)
        self.assertEqual(code, 1)
        self.assertEqual(value["error"]["code"], "internal")
        self.assertIn("filter", value["error"]["message"])
        self.assertIn(".git/", value["depends_on"])

    def test_unused_external_filter_configuration_does_not_block_query(self):
        self.git("init", "-q")
        put(self.project / "README.md", "tracked\n")
        self.commit_fixture()
        log = self.install_identity_filter(self.project)
        before = snapshot(self.project)
        code, value, _ = self.query()
        self.assertEqual(code, 0)
        self.assertEqual(value["data"]["git"], {"is_repo": True, "dirty": False})
        self.assertFalse(log.exists())
        self.assertEqual(snapshot(self.project), before)

    def test_initialized_submodule_clean_filter_is_rejected_before_any_write(self):
        seed = self.root / "seed"
        seed.mkdir()
        self.git("init", "-q", directory=seed)
        put(seed / "README.md", "tracked\n")
        put(seed / ".gitattributes", "README.md filter=fixture\n")
        self.commit_fixture(directory=seed)
        self.git("init", "-q")
        put(self.project / "README.md", "parent\n")
        self.commit_fixture()
        self.git("-c", "protocol.file.allow=always", "submodule", "add", "-q", str(seed), "nested")
        self.commit_fixture()
        child = self.project / "nested"
        log = self.install_identity_filter(child)
        readme = child / "README.md"
        self.touch_tracked(readme)
        self.git("status", "--porcelain", directory=child)
        self.assertEqual(log.read_bytes(), b"filter-ran\n", "child filter positive control")
        log.write_bytes(b"")
        self.touch_tracked(readme)
        before = snapshot(self.project)
        code, value, _ = self.query()
        self.assertEqual(log.read_bytes(), b"", "parent query must not execute child filter")
        self.assertEqual(snapshot(self.project), before)
        self.assertEqual(code, 1)
        self.assertEqual(value["error"]["code"], "internal")
        self.assertIn("filter", value["error"]["message"])

    def without_tomllib(self, *args):
        # Isolate the missing-module failure mode without claiming this runs
        # on an older interpreter. The host interpreter is still the test host.
        bootstrap = '''
import importlib.abc, runpy, sys
class MissingToml(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "tomllib":
            raise ModuleNotFoundError("simulated missing tomllib")
sys.meta_path.insert(0, MissingToml())
sys.path.insert(0, sys.argv[1])
sys.argv = sys.argv[2:]
runpy.run_path(sys.argv[0], run_name="__main__")
'''
        result = subprocess.run([sys.executable, "-c", bootstrap, str(SCRIPTS), str(QUERY), *map(str, args)],
                                env=self.env, cwd=self.root, capture_output=True, timeout=15)
        self.assertTrue(result.stdout, result.stderr.decode("utf-8", "replace"))
        return result.returncode, json.loads(result.stdout.decode("utf-8"))

    def test_missing_tomllib_keeps_capabilities_and_non_codex_query_available(self):
        code, value = self.without_tomllib("--capabilities")
        self.assertEqual(code, 0)
        self.assertEqual(value["data"]["schemas"], ["workframe.query.v1"])
        code, value = self.without_tomllib("project-status", "--project", self.project)
        self.assertEqual(code, 0)
        self.assertEqual(value["data"]["support"], {"claude": False, "codex": False})

    def test_missing_tomllib_with_codex_config_returns_json_error(self):
        put(self.project / ".codex" / "config.toml", '[plugins."core@sample"]\nenabled = true\n')
        code, value = self.without_tomllib("project-status", "--project", self.project)
        self.assertEqual(code, 1)
        self.assertEqual(value["error"]["code"], "internal")
        self.assertIn("Python 3.11", value["error"]["message"])
        self.assertIn(".codex/config.toml", value["depends_on"])

    def test_usage_errors_echo_topic_and_known_dependencies(self):
        code, value, _ = self.query("--bogus")
        self.assertEqual(code, 2)
        self.assertEqual(value["topic"], "project-status")
        self.assertIn(".workframe-config.json", value["depends_on"])

    def test_encoding_and_toml_end_of_document_errors_have_actual_lines(self):
        marker = self.project / ".workframe-config.json"
        marker.write_bytes(b'{\n"project_name": "' + bytes([255]) + b'"\n}')
        code, value, _ = self.query()
        self.assertEqual(code, 1)
        self.assertEqual(value["error"]["detail"]["line"], 2)
        self.connect()
        put(self.project / ".codex" / "config.toml", '# fixture\n# another line\n[plugins."core@sample"')
        code, value, _ = self.query()
        self.assertEqual(code, 1)
        self.assertEqual(value["error"]["detail"]["line"], 3)

    def test_cc_config_override_and_git_source_are_read_without_changing_registration(self):
        alternate = self.root / "alternate-claude"
        self.env["CLAUDE_CONFIG_DIR"] = str(alternate)
        put(alternate / "plugins" / "known_marketplaces.json", {
            "sample": {"installLocation": str(CORE.parent.parent),
                       "source": {"source": "github", "repo": "example/sample"}}})
        before = snapshot(alternate)
        code, value, _ = self.query()
        self.assertEqual(code, 0)
        self.assertEqual(value["data"]["marketplace_source"], {"kind": "git", "source": "example/sample"})
        self.assertEqual(snapshot(alternate), before)

    def test_codex_directory_source_uses_exact_executing_location(self):
        raw = str(CORE.parent.parent).replace(chr(92), chr(92) * 2)
        put(self.codex_home / "config.toml", '[marketplaces.sample]\nsource_type = "local"\nsource = "'
            + raw + '"\n')
        code, value, _ = self.query()
        self.assertEqual(code, 0)
        self.assertEqual(value["data"]["marketplace_source"], {"kind": "directory", "source": str(CORE.parent.parent)})

    def test_directory_sources_deduplicate_windows_extended_length_registration(self):
        source = str(CORE.parent.parent)
        put(self.claude_home / "plugins" / "known_marketplaces.json", {
            name: {"installLocation": source,
                   "source": {"source": "directory", "path": source}}
            for name in ("sample", "alias")})
        extended = "\\\\?\\" + source
        put(self.codex_home / "config.toml", '[marketplaces.sample]\nsource_type = "local"\nsource = '
            + json.dumps(extended) + '\n')
        code, value, _ = self.query()
        self.assertEqual(code, 0)
        self.assertEqual(value["data"]["marketplace_source"], {"kind": "directory", "source": source})
        self.assertNotIn("Executing plugin matches multiple marketplace sources", value["warnings"])

    def test_different_directory_sources_remain_ambiguous(self):
        source = str(CORE.parent.parent)
        put(self.claude_home / "plugins" / "known_marketplaces.json", {
            name: {"installLocation": source,
                   "source": {"source": "directory", "path": raw}}
            for name, raw in (("sample", source), ("other", str(self.root / "other")))})
        code, value, _ = self.query()
        self.assertEqual(code, 0)
        self.assertEqual(value["data"]["marketplace_source"], {"kind": "unknown", "source": None})
        self.assertIn("Executing plugin matches multiple marketplace sources", value["warnings"])

    def test_codex_extended_length_source_preserves_original_value(self):
        source = "\\\\?\\" + str(CORE.parent.parent)
        put(self.codex_home / "config.toml", '[marketplaces.sample]\nsource_type = "local"\nsource = '
            + json.dumps(source) + '\n')
        code, value, _ = self.query()
        self.assertEqual(code, 0)
        self.assertEqual(value["data"]["marketplace_source"], {"kind": "directory", "source": source})

    def test_capabilities_only_advertises_implemented_topics(self):
        code, value, stderr = self.cli("--capabilities")
        self.assertEqual((code, stderr), (0, ""))
        self.assertEqual(value["data"], {"schemas": ["workframe.query.v1"],
                                        "topics": ["project-status", "gates", "codex-trust", "board", "modules", "stats"],
                                        "codex_trust": {"states": ["ready", "needs_trust", "disabled_by_user", "not_installed", "version_unsupported", "unverified"],
                                            "recovery": {"schema": "workframe.codex-trust-restore.v1", "command": "workframe-codex-trust",
                                                         "operations": ["restore"], "automatic_write_supported": True,
                                                         "supported_codex_versions": ["0.167.2"]}}})
        self.assertIsNone(value["project"])
        code, value, _ = self.cli("--schema", "workframe.query.v1", "--capabilities")
        self.assertEqual(code, 0)
        self.assertEqual(value["topic"], "capabilities")

    def test_empty_directory_has_complete_success_envelope(self):
        code, value, stderr = self.query()
        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(value["topic"], "project-status")
        self.assertEqual(value["project"], self.project.resolve().as_posix())
        self.assertRegex(value["generated_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00$")
        manifest = json.loads((CORE / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(value["framework_version"], manifest["version"])
        data = value["data"]
        self.assertIs(data["connected"], False)
        self.assertIsNone(data["layout"])
        self.assertIsNone(data["project_name"])
        self.assertIsNone(data["setup"])
        self.assertEqual(data["support"], {"claude": False, "codex": False})
        self.assertIsNone(data["ancestor_project"])
        self.assertEqual(data["directory_kind"], "near_empty")
        self.assertEqual(data["git"], {"is_repo": False, "dirty": False})
        self.assertEqual(data["marketplace_source"], {"kind": "unknown", "source": None})
        for path in (".workframe-config.json", ".claude/settings.json", ".codex/config.toml",
                     state_dir_of(self.project).relative_to(self.project).as_posix() + "/setup-state.json"):
            self.assertIn(path, value["depends_on"], "missing files must still be watched")

    def test_connected_unicode_and_no_setup_record(self):
        put(self.project / ".workframe-config.json", {"project_name": "示例项目"})
        code, value, _ = self.query("--schema", "workframe.query.v1")
        self.assertEqual(code, 0)
        self.assertEqual(value["data"]["project_name"], "示例项目")
        self.assertEqual(value["data"]["layout"], "current")
        self.assertEqual(value["data"]["setup"], {
            "state": "none", "steps": [], "completed_count": 0, "last_acceptance": None})

    def test_missing_arguments_are_json_usage_errors(self):
        for args in ((), ("project-status",), ("project-status", "--project"),
                     ("project-status", "--project", str(self.project), "--bogus")):
            with self.subTest(args=args):
                code, value, _ = self.cli(*args)
                self.assertEqual(code, 2)
                self.assertEqual(value["error"]["code"], "bad_usage")

    def test_reserved_topics_expose_connection_errors_without_advertising_implementation(self):
        code, value, _ = self.cli("board", "--project", self.project)
        self.assertEqual(code, 1)
        self.assertEqual(value["error"]["code"], "not_connected")
        self.connect()
        code, value, _ = self.cli("board", "--project", self.project)
        self.assertEqual(code, 1)
        self.assertEqual(value["error"]["code"], "not_found")
        (self.project / legacy_rel("state")).mkdir(parents=True)
        code, value, _ = self.cli("board", "--project", self.project)
        self.assertEqual(code, 1)
        self.assertEqual(value["error"]["code"], "legacy_layout")
        self.assertIn(".workframe-config.json", value["depends_on"])

    def test_invalid_project_is_json_usage_error(self):
        file = self.root / "file"
        put(file, "content")
        for project in (self.root / "missing", file):
            with self.subTest(project=project):
                code, value, _ = self.cli("project-status", "--project", project)
                self.assertEqual(code, 2)
                self.assertEqual(value["error"]["code"], "bad_usage")

    def test_schema_unsupported_lists_supported_versions(self):
        code, value, _ = self.query("--schema", "workframe.query.v999")
        self.assertEqual(code, 1)
        self.assertEqual(value["error"]["code"], "schema_unsupported")
        self.assertEqual(value["error"]["detail"]["supported"], ["workframe.query.v1"])

    def test_marker_syntax_error_preserves_dependency_and_line(self):
        put(self.project / ".workframe-config.json", '{\n  "project_name": ]\n}')
        code, value, _ = self.query()
        self.assertEqual(code, 1)
        self.assertEqual(value["error"]["code"], "parse_error")
        self.assertEqual(value["error"]["detail"], {"file": ".workframe-config.json", "line": 2})
        self.assertIn(".workframe-config.json", value["depends_on"])

    def test_marker_wrong_shape_is_parse_error(self):
        for value in ([], "[]", "null", "false"):
            with self.subTest(value=value):
                put(self.project / ".workframe-config.json", value)
                code, result, _ = self.query()
                self.assertEqual(code, 1)
                self.assertEqual(result["error"]["code"], "parse_error")

    def test_claude_support_uses_project_enabled_core_plugin(self):
        self.connect()
        for enabled in (True, False):
            put(self.project / ".claude" / "settings.json", {
                "enabledPlugins": {"core@sample": enabled, "other@sample": True}})
            code, value, _ = self.query()
            self.assertEqual(code, 0)
            self.assertEqual(value["data"]["support"], {"claude": enabled, "codex": False})

    def test_codex_support_uses_project_enabled_core_plugin(self):
        self.connect()
        for enabled in (True, False):
            put(self.project / ".codex" / "config.toml", '[plugins."core@sample"]\nenabled = '
                + str(enabled).lower() + '\n[plugins."other@sample"]\nenabled = true\n')
            code, value, _ = self.query()
            self.assertEqual(code, 0)
            self.assertEqual(value["data"]["support"], {"claude": False, "codex": enabled})

    def test_support_does_not_inherit_user_configuration(self):
        put(self.claude_home / "settings.json", {"enabledPlugins": {"core@sample": True}})
        put(self.codex_home / "config.toml", '[plugins."core@sample"]\nenabled = true\n')
        self.connect()
        _, value, _ = self.query()
        self.assertEqual(value["data"]["support"], {"claude": False, "codex": False})

    def test_project_tool_syntax_errors_report_file_and_line(self):
        self.connect()
        # Expected lines are what the standard-library parsers report for these inputs.
        for rel, content, line in ((".claude/settings.json", '{\n"enabledPlugins": ]}', 2),
                                   (".claude/settings.local.json", '{\n"enabledPlugins": ]}', 2),
                                   (".codex/config.toml", '[plugins\n', 1)):
            with self.subTest(file=rel):
                put(self.project / rel, content)
                code, value, _ = self.query()
                self.assertEqual(code, 1)
                self.assertEqual(value["error"]["code"], "parse_error")
                self.assertEqual(value["error"]["detail"]["file"], rel)
                self.assertEqual(value["error"]["detail"]["line"], line)
                self.assertIn(rel, value["depends_on"])
                put(self.project / rel, "{}" if rel.endswith("json") else "")

    def test_ancestor_is_nearest_connected_directory(self):
        put(self.root / ".workframe-config.json", {})
        put(self.home / ".workframe-config.json", {})
        _, value, _ = self.query()
        self.assertEqual(value["data"]["ancestor_project"], self.root.resolve().as_posix())
        self.assertIn("../.workframe-config.json", value["depends_on"])
        self.connect()
        _, value, _ = self.query()
        self.assertIsNone(value["data"]["ancestor_project"])

    def test_legacy_layout_uses_doctor_detection_without_reading_old_state(self):
        self.connect()
        old = self.project / legacy_rel("state")
        put(old / "setup-state.json", "malformed old data")
        code, value, _ = self.query()
        self.assertEqual(code, 0)
        self.assertEqual(value["data"]["layout"], "legacy")
        self.assertIn(old.relative_to(self.project).as_posix() + "/", value["depends_on"])
        self.assertEqual(value["data"]["setup"]["state"], "none")

    def test_claude_native_agent_memory_does_not_mean_legacy_layout(self):
        self.connect()
        put(self.project / legacy_rel("memory") / "custom-agent" / "MEMORY.md", "native memory")
        _, value, _ = self.query()
        self.assertEqual(value["data"]["layout"], "current")

    def test_git_tracks_dirty_state_without_optional_index_refresh(self):
        def git(*args):
            result = subprocess.run(["git", "-C", str(self.project), *args], env=self.env,
                                    capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)

        git("init", "-q")
        put(self.project / "README.md", "tracked\n")
        git("add", "README.md")
        git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
            "-c", "commit.gpgsign=false", "commit", "-qm", "fixture")
        index = self.project / ".git" / "index"
        original = index.read_bytes()
        readme = self.project / "README.md"
        stamp = readme.stat().st_mtime_ns + 2_000_000_000
        os.utime(readme, ns=(stamp, stamp))
        code, value, _ = self.query()
        self.assertEqual(code, 0)
        self.assertEqual(value["data"]["git"], {"is_repo": True, "dirty": False})
        self.assertEqual(index.read_bytes(), original)
        put(readme, "changed\n")
        _, value, _ = self.query()
        self.assertEqual(value["data"]["git"], {"is_repo": True, "dirty": True})

    def test_git_fsmonitor_is_not_executed_by_query(self):
        def git(*args):
            result = subprocess.run(["git", "-C", str(self.project), *args], env=self.env,
                                    capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)

        git("init", "-q")
        readme = self.project / "README.md"
        put(readme, "original\n")
        git("add", "README.md")
        git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
            "-c", "commit.gpgsign=false", "commit", "-qm", "fixture")
        hook = self.project / ".git" / "hooks" / "fixture-fsmonitor"
        put(hook,
            "#!/bin/sh\nprintf 'written-by-fsmonitor\\n' > README.md\nprintf 'token\\000'\n")
        hook.chmod(0o755)
        git("config", "core.fsmonitor", ".git/hooks/fixture-fsmonitor")
        git("status", "--porcelain")
        self.assertEqual(readme.read_bytes(), b"written-by-fsmonitor\n", "positive control must execute hook")
        put(readme, "original\n")
        before = snapshot(self.project)
        code, value, _ = self.query()
        self.assertEqual(code, 0)
        self.assertEqual(readme.read_bytes(), b"original\n")
        self.assertEqual(snapshot(self.project), before)
        self.assertEqual(value["data"]["git"], {"is_repo": True, "dirty": False})

    def test_marketplace_source_matches_executing_plugin_not_first_registry_entry(self):
        put(self.claude_home / "plugins" / "known_marketplaces.json", {
            "unrelated": {"installLocation": str(self.root / "other"),
                          "source": {"source": "git", "url": "https://example.invalid/other.git"}},
            "sample": {"installLocation": str(CORE.parent.parent),
                       "source": {"source": "directory", "path": str(CORE.parent.parent)}}})
        code, value, _ = self.query()
        self.assertEqual(code, 0)
        self.assertEqual(value["data"]["marketplace_source"], {
            "kind": "directory", "source": str(CORE.parent.parent)})


class DirectoryTests(QueryFixture):
    def classify(self, project=None, **kwargs):
        path = SCRIPTS / "_project_directory.py"
        self.assertTrue(path.is_file(), "shared directory classifier must exist")
        module = importlib.import_module("_project_directory")
        with patch.dict(os.environ, self.env):
            return module.classify_directory(project or self.project, **kwargs)

    def test_empty_git_only_and_readme_only_are_near_empty(self):
        self.assertEqual(self.classify(), "near_empty")
        (self.project / ".git").mkdir()
        self.assertEqual(self.classify(), "near_empty")
        put(self.project / "README.md", "sample")
        self.assertEqual(self.classify(), "near_empty")
        self.assertEqual(self.classify(for_setup=True), "project")

    def test_hidden_content_is_not_query_near_empty(self):
        put(self.project / ".hidden", "content")
        self.assertEqual(self.classify(), "plain")

    def test_scaffold_retains_original_near_empty_exemptions(self):
        for name in scaffold.NEAR_EMPTY_ALLOWLIST:
            put(self.project / name, "fixture")
        self.assertEqual(scaffold._substantive_entries(self.project), [])
        put(self.project / "source.py", "print('sample')")
        self.assertEqual(scaffold._substantive_entries(self.project), ["source.py"])

    def test_container_precedes_repo_for_query_and_repo_precedes_container_for_setup(self):
        desktop = self.home / "Desktop"
        desktop.mkdir()
        self.assertEqual(self.classify(desktop), "container")
        (desktop / ".git").mkdir()
        self.assertEqual(self.classify(desktop), "container")
        self.assertEqual(self.classify(desktop, for_setup=True), "project")
        for name in ("Downloads", "Documents", "桌面", "下载", "文档", "デスクトップ"):
            directory = self.home / name
            directory.mkdir()
            self.assertEqual(self.classify(directory), "container")
        self.assertEqual(self.classify(self.home), "container")
        self.assertEqual(self.classify(Path(self.project.anchor)), "container")

    def test_project_manifests_and_monorepo_child(self):
        for name in ("package.json", "pyproject.toml", "Cargo.toml", "go.mod", "pom.xml", "sample.sln"):
            with self.subTest(name=name):
                directory = self.project / name.replace(".", "-")
                directory.mkdir()
                put(directory / name, "fixture")
                self.assertEqual(self.classify(directory), "project")
        child = self.project / "packages" / "child"
        child.mkdir(parents=True)
        put(child / "source.py", "pass")
        self.assertEqual(self.classify(child), "plain")
        (self.project / ".git").mkdir()
        self.assertEqual(self.classify(), "project")

    def test_enumeration_failure_is_unknown_but_scaffold_still_raises(self):
        with patch.object(Path, "iterdir", side_effect=PermissionError("fixture denied")):
            self.assertEqual(self.classify(), "unknown")
            with self.assertRaises(PermissionError):
                scaffold._substantive_entries(self.project)

    def test_setup_cli_reuses_shared_classification_without_writing(self):
        (self.project / ".git").mkdir()
        before = snapshot(self.root)
        result = subprocess.run([sys.executable, "-B", str(SCRIPTS / "project_scaffold.py"),
                                 "--project", str(self.project), "--classify-directory"],
                                env=self.env, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout.decode("utf-8")), {"directory_kind": "project"})
        self.assertEqual(snapshot(self.root), before)


class SetupStatusTests(QueryFixture):
    def setUp(self):
        super().setUp()
        self.connect()
        self.record = state_dir_of(self.project) / "setup-state.json"

    def test_required_steps_pending_failed_complete_and_deferred(self):
        cases = (
            ({"scaffold": "ok"}, "in_progress", {"scaffold": "ok", "subscribe": "pending", "agents_md": "pending"}, 1),
            ({"scaffold": "ok", "subscribe": "failed: offline"}, "failed", {"scaffold": "ok", "subscribe": "failed", "agents_md": "pending"}, 1),
            ({"scaffold": "ok", "subscribe": "ok", "agents_md": "ok"}, "complete", {"scaffold": "ok", "subscribe": "ok", "agents_md": "ok"}, 3),
        )
        for steps, state, expected, count in cases:
            with self.subTest(state=state):
                put(self.record, {"steps": steps})
                code, result, _ = self.query()
                self.assertEqual(code, 0)
                setup = result["data"]["setup"]
                self.assertEqual(setup["state"], state)
                self.assertEqual({step["name"]: step["state"] for step in setup["steps"]}, expected)
                self.assertEqual(setup["completed_count"], count)
        put(self.record, {"steps": {"scaffold": "ok", "subscribe": "ok"}})
        put(self.project / "AGENTS.md", "# Fixture")
        _, result, _ = self.query()
        self.assertEqual(result["data"]["setup"]["state"], "complete")
        self.assertIn({"name": "agents_md", "state": "deferred"}, result["data"]["setup"]["steps"])

    def test_paused_batches_and_failure_precedence(self):
        record = {"steps": {"scaffold": "ok", "subscribe": "ok", "agents_md": "ok"},
                  "pending_work": {"batches": [{"name": "sample", "pace": "paused"}]}}
        put(self.record, record)
        _, result, _ = self.query()
        self.assertEqual(result["data"]["setup"]["state"], "complete")
        self.assertEqual(result["data"]["setup"]["completed_count"], 3)
        record["steps"]["subscribe"] = "failed: fixture"
        put(self.record, record)
        _, result, _ = self.query()
        self.assertEqual(result["data"]["setup"]["state"], "failed")

    def test_incomplete_required_steps_remain_paused(self):
        put(self.record, {"steps": {"scaffold": "ok"},
                          "pending_work": {"batches": [{"name": "sample", "pace": "paused"}]}})
        _, result, _ = self.query()
        self.assertEqual(result["data"]["setup"]["state"], "paused")
        self.assertEqual(result["data"]["setup"]["completed_count"], 1)

    def test_deferred_required_step_completes_despite_paused_batches(self):
        put(self.record, {"steps": {"scaffold": "ok", "subscribe": "ok"},
                          "pending_work": {"batches": [{"name": "sample", "pace": "paused"}]}})
        put(self.project / "AGENTS.md", "# Fixture")
        _, result, _ = self.query()
        setup = result["data"]["setup"]
        self.assertEqual(setup["state"], "complete")
        self.assertEqual(setup["completed_count"], 3)
        self.assertIn({"name": "agents_md", "state": "deferred"}, setup["steps"])

    def test_unparseable_and_invalid_records_are_unknown(self):
        for raw in ("{", "[]", '{"steps":["scaffold"]}', '{"steps":[]}',
                    '{"steps":{"scaffold":"ok"},"pending_work":[]}',
                    '{"steps":{"scaffold":"ok"},"pending_work":{"batches":{}}}'):
            with self.subTest(raw=raw):
                put(self.record, raw)
                code, result, _ = self.query()
                self.assertEqual(code, 0)
                self.assertEqual(result["data"]["setup"]["state"], "unknown")
                self.assertTrue(result["warnings"])
                self.assertIn(self.record.relative_to(self.project).as_posix(), result["depends_on"])

    def test_hook_only_record_matches_doctor_manual_project_handling(self):
        put(self.record, {"steps": {"agents_md": "ok", "acceptance": "failed: fixture"}})
        _, result, _ = self.query()
        self.assertEqual(result["data"]["setup"]["state"], "none")
        findings = doctor.check_setup_state({"paths": doctor.Paths(self.project)})
        self.assertEqual([level for level, _ in findings], ["info"])

    def test_acceptance_does_not_determine_setup_state_or_invent_a_time(self):
        steps = {"scaffold": "ok", "subscribe": "ok", "agents_md": "ok", "acceptance": "failed: fixture"}
        put(self.record, {"steps": steps, "updated_at": "2020-01-01T00:00:00+00:00"})
        _, result, _ = self.query()
        setup = result["data"]["setup"]
        self.assertEqual(setup["state"], "complete")
        self.assertEqual(setup["completed_count"], 3)
        self.assertEqual(setup["last_acceptance"], {"at": None, "ok": False})
        self.assertTrue(any("acceptance time" in warning for warning in result["warnings"]))


class ZeroWriteTests(QueryFixture):
    def setUp(self):
        super().setUp()
        self.plugin = self.root / "plugin"
        shutil.copytree(CORE, self.plugin, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        self.script = self.plugin / "scripts" / "workframe_query.py"
        self.assertTrue(self.script.is_file(), "query must exist before exercising zero writes")
        result = subprocess.run([sys.executable, "-B", str(SCRIPTS / "project_scaffold.py"),
                                 "--project", str(self.project)], env=self.env,
                                capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        put(self.claude_home / "settings.json", {})
        put(self.claude_home / "plugins" / "known_marketplaces.json", {})
        put(self.codex_home / "config.toml", "# isolated user config\n")
        put(self.project / "README.md", "fixture\n")
        subprocess.run(["git", "init", "-q", str(self.project)], env=self.env, check=True, capture_output=True)
        self.watched = {"project": self.project, "state": state_dir_of(self.project),
                        "plugin": self.plugin, "claude": self.claude_home, "codex": self.codex_home}

    def snapshots(self):
        return {name: snapshot(path) for name, path in self.watched.items()}

    def assert_unchanged(self, before):
        for name, path in self.watched.items():
            self.assertEqual(snapshot(path), before[name], f"query wrote to {name}")

    def test_success_failure_capabilities_and_bin_preserve_all_five_trees(self):
        commands = (("--capabilities",), ("project-status", "--project", str(self.project)),
                    ("project-status", "--project", str(self.project), "--schema", "unsupported"),
                    ("project-status", "--project", str(self.project), "--bogus"))
        for script in (self.script, self.plugin / "bin" / "workframe-query"):
            for args in commands:
                with self.subTest(script=script.name, args=args):
                    before = self.snapshots()
                    self.cli(*args, script=script)
                    self.assert_unchanged(before)
        for content in ("{", "[]"):
            put(self.project / ".workframe-config.json", content)
            before = self.snapshots()
            code, value, _ = self.query(script=self.script)
            self.assertEqual((code, value["error"]["code"]), (1, "parse_error"))
            self.assert_unchanged(before)

    def test_codex_home_inside_project_has_no_side_effect_exemption(self):
        inside = self.project / "codex-home"
        put(inside / "config.toml", "# isolated\n")
        self.env["CODEX_HOME"] = str(inside)
        self.watched["codex"] = inside
        before = self.snapshots()
        code, value, _ = self.query(script=self.script)
        self.assertEqual(code, 0)
        self.assertNotIn("side_effects", value)
        self.assert_unchanged(before)

    def test_gates_trust_and_restore_preserve_all_five_trees_after_tracked_touch(self):
        self.commit_fixture()
        self.touch_tracked(self.project / "README.md")
        commands = (("gates", "--project", str(self.project), "--gate", "doctor"),
                    ("gates", "--project", str(self.project), "--gate", "close-check"),
                    ("codex-trust", "--project", str(self.project), "--codex-home", str(self.codex_home),
                     "--codex-exe", sys.executable))
        for script in (self.script, self.plugin / "bin/workframe-query"):
            for command in commands:
                before = self.snapshots()
                code, value, _ = self.cli(*command, script=script)
                self.assertEqual(code, 0, value)
                self.assert_unchanged(before)
        source = {"kind": "directory", "marketplace_id": "fixture", "plugin_name": "core", "plugin_subpath": "plugins/core",
                  "real_path": self.root.as_posix(), "assurance": "registry_binding"}
        for script in (self.plugin / "scripts/workframe_codex_trust.py", self.plugin / "bin/workframe-codex-trust"):
            before = self.snapshots()
            command = [sys.executable, "-B", str(script), "restore", "--project", str(self.project),
                       "--codex-home", str(self.codex_home), "--codex-exe", sys.executable, "--codex-cwd", str(self.project),
                       "--expect-snapshot", "sha256:" + "a" * 64, "--expect-source", json.dumps(source)]
            response = subprocess.run(command, env=self.env, cwd=self.root, capture_output=True, timeout=15)
            value = json.loads(response.stdout.decode("utf-8"))
            self.assertEqual((response.returncode, value["data"]["write_outcome"]), (2, "none"))
            self.assert_unchanged(before)

    def test_each_zero_write_boundary_detects_a_real_injected_write(self):
        original_script = self.script.read_bytes()
        lf_text = original_script.decode("utf-8").replace("\r\n", "\n")
        targets = (self.project / "README.md", self.project / ".git" / "config",
                   state_dir_of(self.project) / "setup-state.json",
                   self.plugin / ".claude-plugin" / "plugin.json",
                   self.claude_home / "settings.json", self.codex_home / "config.toml")
        for newline in ("\n", "\r\n"):
            text = lf_text.replace("\n", newline)
            anchor = "def main(argv=None):" + newline
            self.assertEqual(text.count(anchor), 1)
            for target in targets:
                with self.subTest(newline=repr(newline), target=target.relative_to(self.root).as_posix()):
                    old = target.read_bytes()
                    write = f"    Path({str(target)!r}).write_bytes(b'injected write')" + newline
                    mutated = text.replace(anchor, anchor + write)
                    self.assertEqual(mutated.count(write), 1)
                    self.assertNotEqual(mutated.encode("utf-8"), text.encode("utf-8"))
                    self.script.write_bytes(mutated.encode("utf-8"))
                    before = self.snapshots()
                    try:
                        self.query(script=self.script)
                        self.assertEqual(target.read_bytes(), b"injected write")
                        with self.assertRaises(AssertionError):
                            self.assert_unchanged(before)
                    finally:
                        self.script.write_bytes(original_script)
                        target.write_bytes(old)
                    self.assertEqual(self.script.read_bytes(), original_script)
                    self.assertEqual(target.read_bytes(), old)

    def test_direct_script_suppresses_import_bytecode_without_caller_environment(self):
        before = self.snapshots()
        code, _, _ = self.query(script=self.script)
        self.assertEqual(code, 0)
        self.assertFalse(list(self.plugin.rglob("*.pyc")))
        self.assert_unchanged(before)
        source = self.script.read_text(encoding="utf-8")
        self.assertEqual(source.count("sys.dont_write_bytecode = True"), 1)
        self.script.write_text(source.replace("sys.dont_write_bytecode = True", "sys.dont_write_bytecode = False"),
                               encoding="utf-8", newline="")
        before = self.snapshots()
        code, _, _ = self.query(script=self.script)
        self.assertEqual(code, 0)
        self.assertTrue(list(self.plugin.rglob("*.pyc")), "mutation must really enable import writes")
        with self.assertRaises(AssertionError):
            self.assert_unchanged(before)


    def test_data_topics_preserve_five_trees_for_success_and_failures(self):
        self.commit_fixture()
        self.touch_tracked(self.project / "README.md")
        for script in (self.script, self.plugin / "bin/workframe-query"):
            for topic in ("board", "modules", "stats"):
                args = [topic, "--project", str(self.project)]
                if topic == "stats":
                    args += ["--weeks", "[]"]
                for extra in ([], ["--schema", "unsupported"], ["--bogus"]):
                    with self.subTest(script=script.name, topic=topic, extra=extra):
                        before = self.snapshots()
                        code, value, _ = self.cli(*args, *extra, script=script)
                        self.assertEqual(code, 0 if not extra else 1 if extra[0] == "--schema" else 2, value)
                        self.assert_unchanged(before)
        for content in ("tasks: [broken\n", "tasks: []\n"):
            put(self.project / "projects/board.yaml", content)
            for topic in ("board", "modules", "stats"):
                args = [topic, "--project", str(self.project)] + (["--weeks", "[]"] if topic == "stats" else [])
                before = self.snapshots()
                self.cli(*args, script=self.script)
                self.assert_unchanged(before)


class QueryGateTests(QueryFixture):
    def validation(self):
        spec = importlib.util.spec_from_file_location("query_test_validate", ROOT / "tools" / "validate.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_query_suite_gate_rejects_failed_or_unexecuted_suites(self):
        validator = self.validation()
        self.assertTrue(hasattr(validator, "check_workframe_query_unit_tests_pass"))
        for code, output in ((1, "FAILED (failures=1)"), (0, "Ran 0 tests\n\nOK\n")):
            result = subprocess.CompletedProcess([], code, output, "")
            with self.subTest(code=code, output=output), redirect_stdout(io.StringIO()), patch.object(validator.subprocess, "run", return_value=result):
                passed, message = validator.check_workframe_query_unit_tests_pass()
                self.assertFalse(passed, message)

    def test_windows_wrapper_gate_accepts_B_and_rejects_wrong_dispatch(self):
        validator = self.validation()
        folder = self.root / "plugins" / "core" / "bin"
        target = folder / "workframe-query.cmd"
        target.parent.mkdir(parents=True)
        with redirect_stdout(io.StringIO()), patch.object(validator, "FRAMEWORK_ROOT", self.root), patch.object(
                validator, "_bin_indexed_modes", return_value=({"workframe-query.cmd": "100644"}, None)):
            for flag in ("", "-B "):
                put(target, '@echo off\r\npython ' + flag + '"%~dp0workframe-query" %*\r\nexit /b %ERRORLEVEL%\r\n')
                passed, message = validator.check_all_bin_cmd_wrappers_portable()
                self.assertTrue(passed, message)
            put(target, '@echo off\r\npython -B "%~dp0wrong-target" %*\r\nexit /b %ERRORLEVEL%\r\n')
            passed, message = validator.check_all_bin_cmd_wrappers_portable()
            self.assertFalse(passed, message)


class QuerySummaryTests(unittest.TestCase):
    """Exercise the suite verdict without creating or relocating its fixtures."""
    validation = QueryGateTests.validation
    groups = ("DirectoryTests", "QueryCliTests", "SetupStatusTests", "ZeroWriteTests", "QueryGateTests")

    def setUp(self):
        self.validator = self.validation()

    def transcript(self, summary="OK", unsuccessful=None, count=5):
        lines = ["test_behavior (test_workframe_query." + group + ".test_behavior) ... " +
                 ("skipped 'fixture capability'" if group == unsuccessful else "ok") for group in self.groups]
        return "\n".join(lines) + "\n\nRan " + str(count) + " tests in 0.123s\n\n" + summary + "\n"

    def verdict(self, output, code=0):
        validator = self.validator
        response = subprocess.CompletedProcess([], code, "", output)
        with redirect_stdout(io.StringIO()), patch.object(validator.subprocess, "run", return_value=response):
            return validator.check_workframe_query_unit_tests_pass()

    def test_successful_suite_accepts_plain_and_skipped_summaries(self):
        for summary in ("OK", "OK (skipped=1)", "OK (skipped=12)"):
            with self.subTest(summary=summary):
                passed, message = self.verdict(self.transcript(summary))
                self.assertTrue(passed, message)

    def test_every_behavior_group_needs_an_executed_success(self):
        for group in self.groups:
            with self.subTest(group=group):
                passed, message = self.verdict(self.transcript("OK (skipped=1)", unsuccessful=group))
                self.assertFalse(passed, message)
                self.assertIn(group, message)

    def test_failed_nonzero_empty_and_missing_summaries_stay_red(self):
        cases = [(self.transcript("FAILED (failures=1)"), 1),
                 (self.transcript("FAILED (failures=1)"), 0),
                 (self.transcript(), 1), ("Ran 0 tests in 0.000s\n\nOK\n", 0),
                 (self.transcript(count=0), 0), (self.transcript(summary=""), 0)]
        for output, code in cases:
            with self.subTest(code=code, output=output):
                passed, message = self.verdict(output, code)
                self.assertFalse(passed, message)


class AppServerContextTests(unittest.TestCase):
    def test_frozen_context_reaches_argv_and_child_environment_in_order(self):
        import _codex_appserver as cas
        with patch.object(cas.subprocess, "Popen") as popen, patch.object(cas.AppServer, "request", return_value={}), patch.object(cas.AppServer, "notify"):
            server = cas.AppServer(exe="codex.exe", codex_home="isolated-home", cwd="project/child",
                                   profile="review", config_overrides=["x=1", "x=2"], env={"PATH": "fixture"})
            server.start()
        self.assertEqual(popen.call_args.args[0],
                         ["codex.exe", "--profile", "review", "--config", "x=1", "--config", "x=2", "app-server"])
        self.assertEqual(popen.call_args.kwargs["cwd"], "project/child")
        self.assertEqual(popen.call_args.kwargs["env"]["CODEX_HOME"], "isolated-home")

    def test_initialization_failure_closes_owned_process(self):
        import _codex_appserver as cas
        with patch.object(cas.subprocess, "Popen"), patch.object(cas.AppServer, "request", side_effect=cas.AppServerError("fixture")), patch.object(cas.AppServer, "close") as close:
            server = cas.AppServer(exe="codex.exe")
            with self.assertRaises(cas.AppServerError):
                server.start()
        close.assert_called_once()

    def test_expired_total_deadline_never_sends_request(self):
        import _codex_appserver as cas
        server = cas.AppServer(exe="codex.exe", deadline=0)
        with patch.object(server, "_send") as send, self.assertRaises(cas.AppServerError):
            server.request("config/read")
        send.assert_not_called()


class GatesAdapterTests(unittest.TestCase):
    def test_navigation_does_not_crash_on_malformed_ledger_configuration(self):
        gates = importlib.import_module("_query_gates")
        rows = [{"id": "ledger", "name": "ledger", "findings": [{"level": "warn", "msg": "invalid ledger config"}]}]
        for cfg in ({"close_check": "broken"}, {"close_check": {"ledger": ["invalid"]}}, {}):
            related = gates.related_files(ROOT, cfg, rows)
            value = gates.normalize("close-check", rows, related)
            self.assertEqual(value["checks"][0]["files"], [".workframe-config.json"])
            self.assertIsNone(value["checks"][0]["doc_path"])

    def test_preserves_each_finding_and_reports_nonapplicable_modules_as_skipped(self):
        gates = importlib.import_module("_query_gates")
        rows = [{"id": "modules", "name": "modules", "findings": [{"level": "info", "msg": "no modules"}]},
                {"id": "ledger", "name": "ledger", "findings": [{"level": "warn", "msg": "one"}, {"level": "error", "msg": "two"}]}]
        value = gates.normalize("close-check", rows, {"modules": ["projects/modules/"], "ledger": ["projects/custom-log.md"]})
        self.assertEqual(value["summary"], {"ok": 0, "info": 0, "skipped": 1, "warn": 1, "error": 1})
        self.assertEqual([c["id"] for c in value["checks"]], ["modules", "ledger", "ledger"])
        self.assertEqual(value["checks"][1]["doc_path"], "projects/custom-log.md")
        self.assertEqual(value["checks"][2]["files"], ["projects/custom-log.md"])

    def test_git_read_only_environment_is_restored_and_keeps_existing_overrides(self):
        gates = importlib.import_module("_query_gates")
        env = {"GIT_OPTIONAL_LOCKS": "1", "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "fixture.value", "GIT_CONFIG_VALUE_0": "keep"}
        with patch.dict(os.environ, env, clear=True):
            before = dict(os.environ)
            with gates.read_only_git():
                self.assertEqual(os.environ["GIT_OPTIONAL_LOCKS"], "0")
                self.assertEqual(os.environ["GIT_CONFIG_KEY_0"], "fixture.value")
                self.assertEqual(os.environ["GIT_CONFIG_KEY_1"], "core.fsmonitor")
                self.assertEqual(os.environ["GIT_CONFIG_VALUE_1"], "false")
            self.assertEqual(dict(os.environ), before)

    def test_doctor_safe_codex_callback_is_used_by_real_runner(self):
        seen = []
        def safe(ctx):
            seen.append(ctx["paths"].project)
            return [("warn", "safe context")]
        with patch.object(doctor, "_load_events", return_value=([], [], 0)), patch.object(doctor, "_discover_scopes", return_value=[]), patch.dict(doctor.GROUPS, {"install": ("install", [("codex_trust", "trust", "trust", lambda ctx: self.fail("unsafe diagnosis called"))])}, clear=True):
            rows = doctor.run_all(ROOT, codex_check=safe)
        self.assertEqual(seen, [ROOT])
        self.assertEqual(rows[0]["findings"], [{"level": "warn", "msg": "safe context"}])


class QueryCapabilitiesTests(unittest.TestCase):
    def test_actual_capabilities_process_advertises_read_only_topics_and_unsupported_writes(self):
        result = subprocess.run([sys.executable, "-B", str(QUERY), "--capabilities"],
                                capture_output=True, timeout=10)
        self.assertEqual((result.returncode, result.stderr), (0, b""))
        value = json.loads(result.stdout.decode("utf-8"))
        self.assertEqual(value["schema"], "workframe.query.v1")
        self.assertEqual(value["data"]["topics"], ["project-status", "gates", "codex-trust", "board", "modules", "stats"])
        recovery = value["data"]["codex_trust"]["recovery"]
        self.assertTrue(recovery["automatic_write_supported"])
        self.assertEqual(recovery["supported_codex_versions"], ["0.167.2"])
        self.assertEqual(value["depends_on"], [])
        self.assertIsNone(value["project"])

    def test_context_options_are_not_silently_ignored(self):
        for args in (("--capabilities", "--timeout-ms", "1"),
                     ("project-status", "--project", str(ROOT), "--codex-profile", "fixture"),
                     ("--capabilities", "--gate", "doctor")):
            result = subprocess.run([sys.executable, "-B", str(QUERY), *args], capture_output=True, timeout=10)
            value = json.loads(result.stdout.decode("utf-8"))
            self.assertEqual((result.returncode, value["error"]["code"]), (2, "bad_usage"))


class TrustObservationTests(unittest.TestCase):
    def setUp(self):
        import copy
        self.copy = copy.deepcopy
        self.trust = importlib.import_module("_query_codex_trust")
        self.pid = "core@fixture"
        self.context = {"requested_exe": None, "resolved_exe": "fixture-native", "codex_version": "fixture",
                        "codex_home": (ROOT.parent / "tmp" / "fixture-home").as_posix(),
                        "cwd": ROOT.as_posix(), "profile": None, "context_id": "sha256:" + "a" * 64}
        manifest = json.loads((CORE / "hooks/hooks.codex.manifest.json").read_text(encoding="utf-8"))
        self.listing = {"data": [{"cwd": ROOT.as_posix(), "errors": [], "warnings": [], "hooks": [
            {"pluginId": self.pid, "sourcePath": str(CORE / "hooks/hooks.codex.json"),
             "key": self.pid + ":" + row["key"], "currentHash": "opaque:full/hash:" + row["key"],
             "command": "fixture-command", "eventName": row["event"], "enabled": True,
             "trustStatus": "trusted"} for row in manifest["entries"]]}]}
        self.config = {"config": {"plugins": {self.pid: {"enabled": True}}, "hooks": {"enabled": True}},
                       "origins": {}, "layers": []}
        self.source = {"kind": "directory", "marketplace_id": "fixture", "plugin_name": "core",
                       "plugin_subpath": "plugins/core", "real_path": ROOT.as_posix(),
                       "observed_ref": None, "observed_revision": None, "assurance": "registry_binding"}

    def inspect(self, **changes):
        values = dict(context=self.context, plugin_id=self.pid, listing=self.listing,
                      config=self.config, source=self.source, project_trusted=True)
        values.update(changes)
        return self.trust.inspect(**values)

    def test_six_states_and_disabled_priority(self):
        self.assertEqual(self.inspect()["state"], "ready")
        self.listing["data"][0]["hooks"][0]["trustStatus"] = "modified"
        self.assertEqual(self.inspect()["state"], "needs_trust")
        self.listing["data"][0]["hooks"][0]["currentHash"] = None
        self.assertEqual(self.inspect()["state"], "unverified")
        self.config["config"]["plugins"][self.pid]["enabled"] = False
        self.assertEqual(self.inspect(installed=False)["state"], "disabled_by_user")
        self.assertEqual(self.inspect(python_supported=False)["state"], "version_unsupported")
        self.config["config"]["plugins"][self.pid]["enabled"] = True
        self.listing["data"][0]["hooks"] = []
        self.assertEqual(self.inspect(installed=False)["state"], "not_installed")
        self.assertEqual(self.inspect()["state"], "unverified", "zero hooks is not installation evidence")

    def test_snapshot_and_change_token_ignore_trust_but_bind_hash_source_and_context(self):
        ready = self.inspect()
        self.listing["data"][0]["hooks"][0]["trustStatus"] = "untrusted"
        revoked = self.inspect()
        self.assertEqual(ready["snapshot_id"], revoked["snapshot_id"])
        self.assertEqual(ready["change_token"], revoked["change_token"])
        self.listing["data"][0]["hooks"][0]["currentHash"] += ":upgrade"
        upgraded = self.inspect()
        self.assertNotEqual(ready["snapshot_id"], upgraded["snapshot_id"])
        self.assertNotEqual(ready["change_token"], upgraded["change_token"])
        context = dict(self.context, context_id="sha256:" + "b" * 64)
        other_context = self.inspect(context=context)
        self.assertNotEqual(upgraded["snapshot_id"], other_context["snapshot_id"])
        self.assertEqual(upgraded["change_token"], other_context["change_token"])
        source = dict(self.source, real_path=ROOT.parent.as_posix())
        self.assertNotEqual(upgraded["snapshot_id"], self.inspect(source=source)["snapshot_id"])

    def test_hash_is_opaque_and_complete_key_is_preserved(self):
        result = self.inspect()
        original = self.listing["data"][0]["hooks"][0]
        self.assertEqual(result["hooks"][0]["key"], original["key"])
        self.assertEqual(result["hooks"][0]["current_hash"], original["currentHash"])
        self.assertRegex(result["snapshot_id"], r"^sha256:[0-9a-f]{64}$")

    def test_unknown_status_duplicate_missing_key_and_multiple_roots_fail_closed(self):
        mutations = [("hook_status_unknown", lambda hs: hs[0].update(trustStatus="managed")),
                     ("duplicate_key", lambda hs: hs.append(dict(hs[0]))),
                     ("manifest_mismatch", lambda hs: hs.pop()),
                     ("multiple_roots", lambda hs: hs[0].update(sourcePath=str(ROOT / "other/hooks/hooks.codex.json")))]
        for code, change in mutations:
            listing = self.copy(self.listing)
            change(listing["data"][0]["hooks"])
            value = self.inspect(listing=listing)
            self.assertEqual(value["state"], "unverified", code)
            self.assertIsNone(value["snapshot_id"])
            self.assertIn(code, [p["code"] for p in value["problems"]])

    def test_effective_config_wins_over_user_false_and_policy_is_identified(self):
        self.config["layers"] = [{"name": {"type": "user"}, "config": {"plugins": {self.pid: {"enabled": False}}}, "version": "v1"}]
        self.assertEqual(self.inspect()["state"], "ready")
        self.config["config"]["hooks"]["enabled"] = False
        self.config["origins"]["hooks.enabled"] = {"name": {"type": "system"}, "version": "policy"}
        value = self.inspect()
        self.assertEqual((value["state"], value["effective"]["disabled_origin"]), ("disabled_by_user", "policy"))
        self.assertIn("policy_disabled", [p["code"] for p in value["problems"]])

    def test_project_trust_is_required_and_atomic_writes_are_not_advertised(self):
        value = self.inspect(project_trusted=False)
        self.assertEqual(value["state"], "needs_trust")
        self.assertFalse(value["recovery"]["eligible"])
        self.assertIn("workframe-door", value["recovery"]["reason"])
        self.listing["data"][0]["hooks"][0]["trustStatus"] = "untrusted"
        value = self.inspect()
        self.assertFalse(value["recovery"]["eligible"], "unmeasured conditional writes cannot be enabled by fixture results")


class TrustBoundaryTests(unittest.TestCase):
    def test_overlap_is_rejected_before_any_external_process(self):
        trust = importlib.import_module("_query_codex_trust")
        with patch.object(subprocess, "Popen", side_effect=AssertionError("external process started")):
            data, meta = trust.query(ROOT, {"codex_home": str(ROOT), "codex_exe": str(Path(sys.executable))})
        self.assertEqual(data["state"], "unverified")
        self.assertIn("protected_path_overlap", [p["code"] for p in data["problems"]])
        self.assertEqual(meta.get("side_effects", []), [])

    def test_no_certified_side_effect_adapter_never_starts_even_version_command(self):
        trust = importlib.import_module("_query_codex_trust")
        # The version-only probe now selects a certified adapter. An unknown
        # version must still never reach the app-server, even if native.
        with patch.object(subprocess, "Popen", side_effect=AssertionError("external process started")), patch.object(
                trust, "_read_version", return_value="0.167.3"), patch("_query_codex_runtime.tree_snapshot", return_value={}):
            data, meta = trust.query(CORE, {"codex_home": str(ROOT / "docs"),
                                            "codex_exe": str(Path(sys.executable))})
        self.assertEqual(data["state"], "unverified")
        self.assertIn("side_effect_scope_unknown", [p["code"] for p in data["problems"]])
        self.assertFalse(data["recovery"]["eligible"])


class ColdCodexHomeGateTests(unittest.TestCase):
    """A Codex home the registered build has not initialised is refused before launch.

    The first start of codex-cli 0.167.2 in such a home creates the files below
    (measured on a home holding only config.toml; a second start created none of
    them). They are outside the registered allowance, so the query must not start
    the service in a home that lacks any of them. The plugin sync lock is created
    only while the effective plugins switch is on.
    """
    INITIALISED = ("installation_id", "skills/.system/.codex-system-skills.marker", "state_5.sqlite",
                   "goals_1.sqlite", "memories_1.sqlite", "queue_1.sqlite")
    PLUGIN_SYNC_LOCK = ".tmp/plugins.sync.lock"
    EVERYTHING = INITIALISED + (PLUGIN_SYNC_LOCK,)
    PLUGINS_OFF = 'cli_auth_credentials_store="file"\n[features]\nplugins=false\n'
    PLUGINS_ON = 'cli_auth_credentials_store="file"\n[features]\nplugins=true\n'

    def run_query(self, present, config='cli_auth_credentials_store="file"\n', directories=(),
                  overrides=(), profile=None, layers=()):
        """Query a real temporary home; the service constructor is a recording stub.

        Projects, state and home are compared before and after: a refusal that
        wrote anything anywhere fails here, whichever test calls it.
        """
        import _codex_appserver as cas
        import _query_codex_runtime as runtime
        trust = importlib.import_module("_query_codex_trust")
        started = []

        def refuse_to_serve(**kwargs):
            started.append(kwargs)
            raise cas.AppServerError("fixture server")

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            project, home = root / "project", root / "home"
            put(project / ".workframe-config.json", {"project_name": "cold-home"})
            put(project / ".workframe/state/events.jsonl", "")
            put(project / ".git/index", "fixture index")
            put(home / "config.toml", config)
            for name in present:
                put(home / name, "fixture " + name)
            for name in directories:
                (home / name).mkdir(parents=True)
            extra = []
            for number, content in enumerate(layers):
                put(root / "layers" / f"{number}.toml", content)
                extra.append(root / "layers" / f"{number}.toml")
            before = {"home": snapshot(home), "project": snapshot(project)}
            options = {"codex_home": str(home), "codex_exe": sys.executable,
                       "codex_config": list(overrides), "codex_profile": profile}
            with patch.object(runtime, "runtime_paths_known", return_value=True), patch.object(
                    runtime, "config_layer_paths", side_effect=lambda context, env: [home / "config.toml", *extra]), patch.object(
                    trust, "_read_version", return_value="0.167.2"), patch.object(
                    cas, "AppServer", side_effect=refuse_to_serve), patch.object(
                    subprocess, "Popen", side_effect=AssertionError("external process started")):
                data, meta = trust.query(project, options)
            self.assertEqual(before, {"home": snapshot(home), "project": snapshot(project)})
            return data, meta, len(started)

    def assert_refused_before_launch(self, data, meta, started):
        self.assertEqual(started, 0)
        self.assertEqual(data["state"], "unverified")
        self.assertIsNone(data["snapshot_id"])
        self.assertFalse(data["recovery"]["eligible"])
        self.assertIn("side_effect_scope_unknown", [p["code"] for p in data["problems"]])
        self.assertEqual(meta.get("side_effects", []), [])

    def test_home_missing_any_initialisation_file_never_starts_the_service(self):
        for missing in (None, *self.EVERYTHING):
            present = [name for name in self.EVERYTHING if name != missing]
            with self.subTest(missing=missing):
                data, meta, started = self.run_query(present)
                if missing is None:
                    # Fully initialised: the gate lets the query reach the server.
                    self.assertEqual(started, 1)
                else:
                    self.assert_refused_before_launch(data, meta, started)

    def test_cold_home_with_only_the_user_config_is_refused_without_any_write(self):
        self.assert_refused_before_launch(*self.run_query([]))

    def test_a_directory_in_place_of_an_initialisation_file_does_not_count(self):
        for name in self.EVERYTHING:
            with self.subTest(directory=name):
                self.assert_refused_before_launch(
                    *self.run_query([n for n in self.EVERYTHING if n != name], directories=(name,)))

    def test_non_file_snapshot_rows_are_not_initialisation_files(self):
        # Entry kinds a real tree snapshot can hold; a link needs no privilege here.
        import _query_codex_runtime as runtime
        adapter = runtime.Codex01672()
        context = {"codex_home": "home", "cwd": "cwd", "profile": None}
        warm = {name: ("file", "x") for name in self.EVERYTHING}
        for name in self.EVERYTHING:
            for row in (("link", "target"), ("directory",)):
                with self.subTest(name=name, row=row[0]):
                    with patch.object(runtime, "config_layer_paths", return_value=[]):
                        self.assertEqual(adapter.uninitialised(dict(warm, **{name: row}), context, {}, (), b""), [name])

    def test_plugin_sync_lock_follows_the_effective_plugins_switch(self):
        lockless = self.INITIALISED
        for label, config, overrides, expect_start in (
                ("user off", self.PLUGINS_OFF, (), True),
                ("user on", self.PLUGINS_ON, (), False),
                ("default (unset)", 'cli_auth_credentials_store="file"\n', (), False),
                ("user off, override on", self.PLUGINS_OFF, ("features.plugins=true",), False),
                ("user off, override off then on", self.PLUGINS_OFF,
                 ("features.plugins=false", "features.plugins=true"), False),
                ("user off, override on then off", self.PLUGINS_OFF,
                 ("features.plugins=true", "features.plugins=false"), True),
                ("user on, override off", self.PLUGINS_ON, ("features.plugins=false",), True),
                ("user on, override off then on", self.PLUGINS_ON,
                 ("features.plugins=false", "features.plugins=true"), False),
                ("user off, inline table override on", self.PLUGINS_OFF, ("features={plugins=true}",), False),
                ("user off, unrelated override", self.PLUGINS_OFF, ("model=\"x\"",), True),
                ("user off, unparseable features override", self.PLUGINS_OFF, ("features.plugins=[",), False),
                ("user off, profile override", self.PLUGINS_OFF, ("profile=\"p\"",), False)):
            with self.subTest(label):
                data, meta, started = self.run_query(lockless, config, overrides=overrides)
                if expect_start:
                    self.assertEqual(started, 1)
                else:
                    self.assert_refused_before_launch(data, meta, started)

    def test_a_selected_profile_or_other_layer_keeps_the_lock_required(self):
        lockless = self.INITIALISED
        for label, config, kwargs in (
                ("profile option", self.PLUGINS_OFF, {"profile": "p"}),
                ("default profile in the user config", 'profile="p"\n[features]\nplugins=false\n', {}),
                ("another layer sets plugins on", self.PLUGINS_OFF, {"layers": ("[features]\nplugins=true\n",)}),
                ("another layer sets plugins off", self.PLUGINS_OFF, {"layers": ("[features]\nplugins=false\n",)}),
                ("another layer is unreadable", self.PLUGINS_OFF, {"layers": ("[features\n",)}),
                ("user config is unreadable", 'cli_auth_credentials_store="file"\n[features\n', {})):
            with self.subTest(label):
                self.assert_refused_before_launch(*self.run_query(lockless, config, **kwargs))
        # A layer that does not mention the switch leaves the user's explicit value in force.
        _, _, started = self.run_query(lockless, self.PLUGINS_OFF, layers=('model="x"\n',))
        self.assertEqual(started, 1)


class BadUserCodexConfigTests(unittest.TestCase):
    """An unreadable user config.toml leaves the side-effect paths undetermined.

    Contract 4.2: when the side-effect paths cannot be determined the query
    returns unverified (side_effect_scope_unknown) and no service is started.
    Neither entry point may write anything or attempt a trust write.
    """
    BAD_CONFIGS = (("syntax", b'cli_auth_credentials_store="file"\n[features\n'),
                   ("encoding", b'cli_auth_credentials_store="file"\n# \xff\xfe\n'))

    def run_entry(self, config_bytes, entry):
        import _codex_appserver as cas
        trust = importlib.import_module("_query_codex_trust")
        restore = importlib.import_module("workframe_codex_trust")
        started = []

        def refuse_to_serve(**kwargs):
            started.append(True)
            raise cas.AppServerError("fixture server")

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            project, home = root / "project", root / "home"
            put(project / ".workframe-config.json", {"project_name": "bad-user-config"})
            (home).mkdir()
            (home / "config.toml").write_bytes(config_bytes)
            before = {"home": snapshot(home), "project": snapshot(project)}
            options = {"codex_home": str(home), "codex_exe": sys.executable}
            with patch.object(cas, "AppServer", side_effect=refuse_to_serve), patch.object(
                    subprocess, "Popen", side_effect=AssertionError("external process started")), patch.object(
                    trust, "_read_version", side_effect=AssertionError("version probe ran")):
                if entry == "query":
                    data, meta = trust.query(project, options)
                    result = (data, meta)
                else:
                    source = {"kind": "directory", "marketplace_id": "fixture", "plugin_name": "core",
                              "plugin_subpath": "plugins/core", "real_path": CORE.as_posix(),
                              "observed_ref": None, "observed_revision": None, "assurance": "registry_binding"}
                    out = io.StringIO()
                    with redirect_stdout(out):
                        code = restore.main(["restore", "--project", str(project), "--codex-home", str(home),
                                             "--codex-exe", sys.executable, "--codex-cwd", str(project),
                                             "--expect-snapshot", "sha256:" + "a" * 64,
                                             "--expect-source", json.dumps(source),
                                             "--schema", "workframe.codex-trust-restore.v1"])
                    result = (code, json.loads(out.getvalue()))
            unchanged = before == {"home": snapshot(home), "project": snapshot(project)}
            return result, len(started), unchanged

    def test_query_refuses_without_starting_anything_or_writing(self):
        for name, content in self.BAD_CONFIGS:
            with self.subTest(config=name):
                (data, meta), started, unchanged = self.run_entry(content, "query")
                self.assertEqual((started, unchanged), (0, True))
                self.assertEqual(data["state"], "unverified")
                self.assertIsNone(data["snapshot_id"])
                self.assertFalse(data["recovery"]["eligible"])
                self.assertIn("side_effect_scope_unknown", [p["code"] for p in data["problems"]])
                self.assertEqual(meta.get("side_effects", []), [])

    def test_restore_refuses_without_a_write_attempt(self):
        for name, content in self.BAD_CONFIGS:
            with self.subTest(config=name):
                (code, value), started, unchanged = self.run_entry(content, "restore")
                self.assertEqual((started, unchanged), (0, True))
                # Contract 4.2 fixes exit 0 for a restored target, 3 for an unconfirmed write and
                # does not name the code for an undeterminable side-effect scope.
                self.assertIn(code, (1, 2))
                data = value["data"]
                self.assertEqual((data["result"], data["write_outcome"]), ("not_restored", "none"))
                self.assertEqual((data["attempted_keys"], data["verified_keys"]), ([], []))
                self.assertIsNone(data["snapshot_after"])
                self.assertIsNotNone(data["error"])
                self.assertEqual(value["side_effects"], [])


class TrustRestoreTests(TrustObservationTests):
    def test_unsupported_python_protocol_uses_the_unsupported_restore_exit(self):
        restore = importlib.import_module("workframe_codex_trust")
        before = self.trust.reject(self.trust.empty(self.context), "protocol_unsupported", "Python unsupported", "version_unsupported")
        code, value = restore.restore_observation(before, "sha256:" + "a" * 64, self.source)
        self.assertEqual((code, value["write_outcome"], value["attempted_keys"]), (2, "none", []))

    def test_ready_noop_and_mismatched_expectations_never_write(self):
        restore = importlib.import_module("workframe_codex_trust")
        import _codex_appserver as cas
        ready = self.inspect()
        with patch.object(cas.AppServer, "config_batch_write", side_effect=AssertionError("unexpected write")):
            code, value = restore.restore_observation(ready, ready["snapshot_id"], self.source)
            self.assertEqual((code, value["result"], value["write_outcome"]), (0, "unchanged", "none"))
            self.assertEqual(value["verified_keys"], [h["key"] for h in ready["hooks"]])
            self.assertEqual(value["attempted_keys"], [])
            for expected, source, reason in (("sha256:" + "f" * 64, self.source, "snapshot_mismatch"),
                                              (ready["snapshot_id"], dict(self.source, marketplace_id="other"), "source_mismatch")):
                code, value = restore.restore_observation(ready, expected, source)
                self.assertEqual((code, value["write_outcome"], (value.get("error") or {}).get("code")), (1, "none", reason))

    def test_unmeasured_write_protocol_returns_unsupported_with_no_attempted_keys(self):
        restore = importlib.import_module("workframe_codex_trust")
        self.listing["data"][0]["hooks"][0]["trustStatus"] = "untrusted"
        before = self.inspect()
        code, value = restore.restore_observation(before, before["snapshot_id"], self.source)
        self.assertEqual((code, value["result"], (value.get("error") or {}).get("code")), (2, "not_restored", "protocol_unsupported"))
        self.assertEqual(value["write_outcome"], "none")
        self.assertEqual(value["attempted_keys"], [])
        self.assertEqual(value["remaining_keys"], [before["hooks"][0]["key"]])
        self.assertFalse(value["restart_required"])


class CertifiedRuntimeTests(unittest.TestCase):
    def setUp(self):
        TrustObservationTests.setUp(self)
        self.pid = "core@workframe"
        self.source["marketplace_id"] = "workframe"
        self.config["config"]["plugins"] = {self.pid: {"enabled": True}}
        for hook in self.listing["data"][0]["hooks"]:
            hook["pluginId"] = self.pid
            hook["key"] = hook["key"].replace("core@fixture:", self.pid + ":", 1)
            hook["currentHash"] = hook["currentHash"].partition("/")[0] + "/sha256:" + "b" * 64
        self.context["codex_version"] = "0.167.2"
        self.user = {"name": {"type": "user", "profile": None,
                              "file": self.context["codex_home"] + "/config.toml"},
                     "version": "opaque-user-version", "config": {
                         "marketplaces": {"workframe": {"source_type": "local", "source": str(ROOT)}},
                         "plugins": {self.pid: {"enabled": False}}}}
        self.config["layers"] = [self.user]
        self.config["config"]["marketplaces"] = self.copy(self.user["config"]["marketplaces"])
        self.config["config"]["projects"] = {str(ROOT): {"trust_level": "trusted"}}

    inspect = TrustObservationTests.inspect

    def server(self):
        from unittest.mock import Mock
        server = Mock()
        server.config_read.return_value = self.config
        server.hooks_list.return_value = self.listing
        return server

    def test_only_certified_exact_version_selects_an_adapter(self):
        adapter = self.trust.runtime_adapter(self.context)
        self.assertIsNotNone(adapter)
        for version in (None, "0.167.1", "0.167.3", "0.167.2-beta", "99.0.0"):
            self.assertIsNone(self.trust.runtime_adapter(dict(self.context, codex_version=version)))

    def test_runtime_observes_effective_config_registry_binding_and_complete_hooks(self):
        adapter, server = self.trust.runtime_adapter(self.context), self.server()
        value = adapter.observe(server, self.context, ROOT)
        self.assertEqual(value["state"], "ready", value)
        self.assertEqual(value["plugin"]["source"], self.source)
        self.assertEqual(value["hooks"], self.inspect()["hooks"])
        server.config_read.assert_called_once_with(True, cwd=self.context["cwd"])
        server.hooks_list.assert_called_once_with([self.context["cwd"]])
        self.listing["data"][0]["hooks"][0]["trustStatus"] = "untrusted"
        value = adapter.observe(server, self.context, ROOT)
        self.assertEqual((value["state"], value["recovery"]["eligible"]), ("needs_trust", True))

    def test_registry_mismatch_and_response_cwd_cannot_become_ready(self):
        server, adapter = self.server(), self.trust.runtime_adapter(self.context)
        self.user["config"]["marketplaces"]["workframe"]["source"] = str(ROOT.parent)
        value = adapter.observe(server, self.context, ROOT)
        self.assertEqual(value["state"], "unverified")
        self.assertIn("source_unverified", [p["code"] for p in value["problems"]])
        self.listing["data"][0]["cwd"] = str(ROOT.parent)
        value = adapter.observe(server, self.context, ROOT)
        self.assertIn("context_unverifiable", [p["code"] for p in value["problems"]])

    def test_untrusted_project_isolation_or_omitted_user_enablement_requires_door(self):
        import _codex_trust as ct
        self.config["config"]["projects"][str(ROOT)]["trust_level"] = "untrusted"
        self.listing["data"][0]["hooks"] = []
        for enabled in (False, None):
            self.config["config"]["plugins"] = {self.pid: {"enabled": enabled}} if enabled is not None else {}
            self.user["config"]["plugins"] = {self.pid: {"enabled": enabled}} if enabled is not None else {}
            with patch.object(ct, "project_plugin", return_value=(ct.TRUSTED, self.pid, "")):
                server = self.server()
                value = self.trust.runtime_adapter(self.context).observe(server, self.context, ROOT)
            self.assertEqual((value["state"], value["recovery"]["eligible"]), ("needs_trust", False), value)
            self.assertEqual(value["recovery"]["required_action"], "repair_install")
            self.assertIn("project_not_trusted", [p["code"] for p in value["problems"]])
            self.assertIsNone(value["snapshot_id"], "No target may be invented from an empty runtime list")
            server.config_batch_write.assert_not_called()

    def test_isolation_exception_does_not_override_explicit_project_or_policy_disable(self):
        import _codex_trust as ct
        self.config["config"]["projects"][str(ROOT)]["trust_level"] = "untrusted"
        self.config["config"]["plugins"][self.pid]["enabled"] = False
        self.listing["data"][0]["hooks"] = []
        for declaration, origin, expected_origin in ((ct.DISABLED, "project", "user"), (ct.TRUSTED, "system", "policy")):
            # Real config/read origin keys flatten the full plugin id without
            # TOML quotes; retain the quoted legacy fixture form as well.
            for key in ("plugins." + self.pid + ".enabled", 'plugins.' + json.dumps(self.pid) + '.enabled'):
                self.config["origins"] = {key: {"name": {"type": origin}}}
                with patch.object(ct, "project_plugin", return_value=(declaration, self.pid, "")):
                    value = self.trust.runtime_adapter(self.context).observe(self.server(), self.context, ROOT)
                self.assertEqual((value["state"], value["effective"]["disabled_origin"]),
                                 ("disabled_by_user", expected_origin), value)

    def test_missing_installation_is_not_hidden_by_a_project_declaration(self):
        import _codex_trust as ct
        self.user["config"] = {}
        self.config["config"]["marketplaces"] = {}
        self.listing["data"][0]["hooks"] = []
        with patch.object(ct, "project_plugin", return_value=(ct.TRUSTED, self.pid, "")):
            value = self.trust.runtime_adapter(self.context).observe(self.server(), self.context, ROOT)
        self.assertEqual(value["state"], "not_installed", value)
        self.assertFalse(value["recovery"]["eligible"])
        self.assertIsNone(value["snapshot_id"])

    def test_other_market_cache_does_not_hide_a_missing_target_installation(self):
        import _codex_trust as ct
        self.user["config"] = {}
        self.config["config"]["marketplaces"] = {}
        self.listing["data"][0]["hooks"] = []
        home = Path(self.context["codex_home"])
        foreign = home / "plugins/cache/another-market/core/1.0/.codex-plugin/plugin.json"
        def cache_rows(path, pattern):
            if path == home / "plugins/cache":
                return iter([foreign])
            if path == home / "plugins/cache/workframe/core":
                return iter([])
            return iter([])
        with patch.object(ct, "project_plugin", return_value=(ct.TRUSTED, self.pid, "")), patch.object(Path, "glob", cache_rows):
            value = self.trust.runtime_adapter(self.context).observe(self.server(), self.context, ROOT)
        self.assertEqual(value["state"], "not_installed", value)

    def test_hook_policy_origin_supports_flat_and_quoted_full_keys(self):
        hook = self.listing["data"][0]["hooks"][0]
        hook["enabled"] = False
        self.config["config"]["hooks"]["state"] = {hook["key"]: {"enabled": False}}
        for part in (hook["key"], json.dumps(hook["key"])):
            self.config["origins"] = {"hooks.state." + part + ".enabled": {"name": {"type": "system"}}}
            value = self.trust.runtime_adapter(self.context).observe(self.server(), self.context, ROOT)
            self.assertEqual((value["state"], value["effective"]["disabled_origin"]), ("disabled_by_user", "policy"))
            self.assertIn("policy_disabled", [p["code"] for p in value["problems"]])

    def recovery(self, server=None, expected_snapshot=None, expected_source=None, storage_status="verified"):
        import _query_codex_runtime as runtime
        restore = importlib.import_module("workframe_codex_trust")
        adapter, server = self.trust.runtime_adapter(self.context), server or self.server()
        before = adapter.observe(server, self.context, ROOT)
        with patch.object(runtime, "trust_storage_status", return_value=storage_status, create=True):
            return restore.restore_observation(before, expected_snapshot or before["snapshot_id"],
                                               expected_source or (before["plugin"] or {}).get("source") or self.source,
                                               adapter=adapter, server=server, project=ROOT)

    def accept_trust(self, edits, **kwargs):
        """Fixture models 0.167.2's persisted split independently of the adapter."""
        for edit in edits:
            key = json.loads(edit["keyPath"][len("hooks.state."):-len(".trusted_hash")])
            declaration, content = edit["value"].split("/")
            self.user["config"].setdefault("hooks", {}).setdefault("state", {})[key] = {
                "trusted_hash": declaration, "trusted_content_hash": content}
            for hook in self.listing["data"][0]["hooks"]:
                if hook["key"] == key:
                    hook["trustStatus"] = "trusted"

    def test_restore_empty_runtime_hook_target_is_refused_without_a_batch(self):
        self.listing["data"][0]["hooks"] = []
        server = self.server()
        code, value = self.recovery(server)
        self.assertEqual((code, value["result"], value["write_outcome"]), (1, "not_restored", "none"))
        self.assertEqual(value["error"]["code"], "unverified")
        self.assertEqual(value["attempted_keys"], [])
        self.assertEqual(value["verified_keys"], [])
        server.config_batch_write.assert_not_called()

    def test_restore_writes_one_conditional_user_batch_and_verifies_every_hash(self):
        self.listing["data"][0]["hooks"][0]["trustStatus"] = "modified"
        server = self.server()
        def accept(*args, **kwargs):
            self.accept_trust(*args, **kwargs)
            return {"status": "ok"}
        server.config_batch_write.side_effect = accept
        code, value = self.recovery(server)
        self.assertEqual((code, value["result"], value["write_outcome"]), (0, "restored", "confirmed"))
        first = self.listing["data"][0]["hooks"][0]
        server.config_batch_write.assert_called_once_with(
            [{"keyPath": 'hooks.state.' + json.dumps(first["key"]) + '.trusted_hash',
              "mergeStrategy": "upsert", "value": first["currentHash"]}],
            expected_version="opaque-user-version", file_path=self.user["name"]["file"], reload_user_config=True)
        self.assertEqual(value["attempted_keys"], [first["key"]])
        self.assertEqual(set(value["verified_keys"]), {h["key"] for h in self.listing["data"][0]["hooks"]})
        self.assertEqual(value["remaining_keys"], [])
        self.assertTrue(value["restart_required"])

    def test_restore_checks_each_config_field_even_when_runtime_reports_trusted(self):
        first = self.listing["data"][0]["hooks"][0]
        for field in ("trusted_hash", "trusted_content_hash"):
            for value in (None, "wrong"):
                with self.subTest(field=field, value=value):
                    first["trustStatus"] = "untrusted"
                    server = self.server()
                    def accept(edits, **kwargs):
                        self.accept_trust(edits, **kwargs)
                        row = self.user["config"]["hooks"]["state"][first["key"]]
                        if value is None:
                            row.pop(field)
                        else:
                            row[field] = value
                    server.config_batch_write.side_effect = accept
                    code, result = self.recovery(server)
                    self.assertEqual((code, result["result"]), (1, "not_restored"))
                    self.assertEqual(result["error"]["code"], "readback_mismatch")
                    self.assertEqual(result["verified_keys"], [])

    def test_restore_refusals_and_ready_never_send_a_write(self):
        server = self.server()
        code, value = self.recovery(server)
        self.assertEqual((code, value["result"]), (0, "unchanged"))
        self.listing["data"][0]["hooks"][0]["trustStatus"] = "untrusted"
        for snapshot_id, source, error in (("sha256:" + "f" * 64, None, "snapshot_mismatch"),
                                           (None, dict(self.source, marketplace_id="other"), "source_mismatch")):
            code, value = self.recovery(server, snapshot_id, source)
            self.assertEqual((code, value["write_outcome"], (value.get("error") or {}).get("code")), (1, "none", error))
        server.config_batch_write.assert_not_called()

    def test_storage_target_uses_the_observed_user_layer_and_one_content_hash(self):
        adapter, server = self.trust.runtime_adapter(self.context), self.server()
        before = adapter.observe(server, self.context, ROOT)
        config_file = Path(self.user["name"]["file"])
        expected = config_file.parent / ".hook-trust" / expected_hook_trust_name(str(config_file), self.pid)
        self.assertTrue(callable(getattr(adapter, "storage_target", None)), "Recovery needs a bound storage target")
        self.assertEqual(adapter.storage_target(self.context, before), (expected, "sha256:" + "b" * 64))
        for other in ("opaque", "declaration/sha256:" + "c" * 64):
            dirty = self.copy(before)
            dirty["hooks"][0]["current_hash"] = other
            self.assertIsNone(adapter.storage_target(self.context, dirty))

    def test_restore_requires_storage_readback_before_any_key_is_verified(self):
        for status in ("missing", "content_mismatch", "link"):
            with self.subTest(status=status):
                self.listing["data"][0]["hooks"][0]["trustStatus"] = "untrusted"
                server = self.server()
                def accept(*args, **kwargs):
                    self.listing["data"][0]["hooks"][0]["trustStatus"] = "trusted"
                server.config_batch_write.side_effect = accept
                code, value = self.recovery(server, storage_status=status)
                self.assertEqual((code, value["result"], value["write_outcome"]), (1, "not_restored", "partial"), value)
                self.assertEqual((value.get("error") or {}).get("code"), "readback_mismatch")
                self.assertIn(status, value["error"]["message"])
                self.assertEqual(value["verified_keys"], [])
                self.assertEqual(set(value["remaining_keys"]), {hook["key"] for hook in self.listing["data"][0]["hooks"]})

    def test_missing_or_ambiguous_user_version_refuses_without_write(self):
        self.listing["data"][0]["hooks"][0]["trustStatus"] = "untrusted"
        for versions in (None, "", "ambiguous"):
            server = self.server()
            self.user["version"] = versions
            self.config["layers"] = [self.user, self.copy(self.user)] if versions == "ambiguous" else [self.user]
            code, value = self.recovery(server)
            self.assertEqual((code, value["write_outcome"]), (1 if versions == "ambiguous" else 2, "none"), value)
            server.config_batch_write.assert_not_called()

    def test_write_rejection_is_none_but_lost_response_or_readback_is_unconfirmed(self):
        import _codex_appserver as cas
        self.listing["data"][0]["hooks"][0]["trustStatus"] = "untrusted"
        server = self.server()
        server.config_batch_write.side_effect = cas.AppServerRpcError("conflict")
        code, value = self.recovery(server)
        self.assertEqual((code, value["write_outcome"]), (1, "none"))
        server = self.server()
        server.config_batch_write.side_effect = cas.AppServerError("PRIVATE_DETAIL")
        code, value = self.recovery(server)
        self.assertEqual((code, value["result"], value["write_outcome"], value["state_after"]),
                         (3, "write_unconfirmed", "unknown", "unverified"))
        self.assertIn("写入结果未确认", value["error"]["message"])
        self.assertNotIn("PRIVATE_DETAIL", json.dumps(value))
        server = self.server()
        server.config_read.side_effect = [self.config, cas.AppServerError("lost readback")]
        code, value = self.recovery(server)
        self.assertEqual((code, value["result"], value["state_after"]), (3, "write_unconfirmed", "unverified"))

    def test_accepted_write_with_wrong_hash_or_incomplete_readback_is_not_success(self):
        self.listing["data"][0]["hooks"][0]["trustStatus"] = "untrusted"
        server = self.server()
        code, value = self.recovery(server)
        self.assertEqual((code, value["result"], value["write_outcome"]), (1, "not_restored", "partial"))
        self.assertIn(self.listing["data"][0]["hooks"][0]["key"], value["remaining_keys"])
        def altered(*args, **kwargs):
            self.listing["data"][0]["hooks"][0].update(trustStatus="trusted", currentHash="different/hash")
        server.config_batch_write.side_effect = altered
        code, value = self.recovery(server)
        self.assertEqual((code, value["result"]), (1, "not_restored"))

    # What an initialised 0.167.2 home holds before any query starts the service.
    WARM_HOME = {name: ("file", "fixture") for name in ColdCodexHomeGateTests.INITIALISED
                 + (ColdCodexHomeGateTests.PLUGIN_SYNC_LOCK,)}

    def query_transport(self, *, snapshot_rows=({}, {}), operation=None, config_rows=(b"", b"")):
        from unittest.mock import MagicMock
        import _query_codex_runtime as runtime
        import _codex_appserver as cas
        isolated_context = Path(sys.executable).resolve().parent
        self.user["name"]["file"] = (isolated_context / "config.toml").as_posix()
        server, owner = self.server(), MagicMock()
        owner.__enter__.return_value = server
        options = {"codex_home": str(isolated_context), "codex_exe": sys.executable,
                   "codex_profile": "review", "codex_config": ["x=1", "x=2"]}
        with patch.object(runtime, "runtime_paths_known", return_value=True), patch.object(
                runtime, "tree_snapshot", side_effect=[{**self.WARM_HOME, **rows} for rows in snapshot_rows]), patch.object(
                runtime, "config_bytes", side_effect=list(config_rows), create=True), patch.object(
                self.trust, "_read_version", return_value="0.167.2"), patch.object(cas, "AppServer", return_value=owner) as factory:
            data, meta = self.trust.query(ROOT, options, operation=operation)
        owner.__exit__.assert_called_once()
        self.assertEqual(factory.call_args.kwargs["profile"], "review")
        self.assertEqual(factory.call_args.kwargs["config_overrides"], ("x=1", "x=2"))
        return data, meta, server

    def test_query_executes_operation_on_its_owned_connection(self):
        seen = []
        def operation(before, adapter, server):
            seen.append((before["snapshot_id"], server))
            return "owned operation result"
        data, meta, server = self.query_transport(operation=operation)
        self.assertEqual(data["state"], "ready", data)
        self.assertEqual(seen, [(data["snapshot_id"], server)])
        self.assertEqual(meta["_operation_result"], "owned operation result")
        self.assertEqual(meta["side_effects"], ["codex_app_server_logs"])

    def test_query_file_audit_cannot_report_ready_after_an_auth_write(self):
        data, meta, _ = self.query_transport(snapshot_rows=({}, {"auth.json": ("file", "unexpected")}))
        self.assertEqual(data["state"], "unverified")
        self.assertIsNone(data["snapshot_id"])
        self.assertIn("side_effect_scope_unknown", [problem["code"] for problem in data["problems"]])
        self.assertIn("_audit_error", meta)

    def test_query_file_audit_rejects_hook_trust_changes_without_a_write(self):
        data, meta, _ = self.query_transport(snapshot_rows=({}, {
            ".hook-trust/": ("directory",), ".hook-trust/fixture": ("file", "unexpected")}))
        self.assertEqual(data["state"], "unverified")
        self.assertIsNone(data["snapshot_id"])
        self.assertIn("side_effect_scope_unknown", [problem["code"] for problem in data["problems"]])
        self.assertIn("_audit_error", meta)

    def test_query_recovery_audit_permits_only_the_bound_storage_creation(self):
        restore = importlib.import_module("workframe_codex_trust")
        runtime = importlib.import_module("_query_codex_runtime")
        config_file = Path(sys.executable).resolve().parent / "config.toml"
        name = ".hook-trust/" + expected_hook_trust_name(str(config_file), self.pid)
        content = "sha256:" + "b" * 64
        stored = {".hook-trust/": ("directory",), name: ("file", hashlib.sha256(content.encode()).hexdigest())}
        data, meta, _ = self.query_transport(snapshot_rows=({}, stored))
        self.assertEqual(data["state"], "unverified", "The exact same creation is forbidden in a read-only query")
        self.assertIn("_audit_error", meta)
        def operation(before, adapter, server):
            server.config_batch_write.side_effect = self.accept_trust
            return restore.restore_observation(before, before["snapshot_id"], before["plugin"]["source"],
                                               adapter=adapter, server=server, project=ROOT)
        for valid in (True, False):
            with self.subTest(valid=valid):
                self.listing["data"][0]["hooks"][0]["trustStatus"] = "untrusted"
                first = self.listing["data"][0]["hooks"][0]
                declaration, content_hash = first["currentHash"].split("/")
                persisted = ('[hooks.state.' + json.dumps(first["key"]) + ']\ntrusted_hash="' + declaration +
                             '"\ntrusted_content_hash="' + content_hash + '"\n').encode()
                with patch.object(runtime, "trust_storage_status", return_value="verified", create=True):
                    data, meta, _ = self.query_transport(snapshot_rows=({}, stored if valid else {}), operation=operation,
                                                         config_rows=(b"", persisted))
                self.assertEqual(meta["_operation_result"][0], 0)
                self.assertEqual("_audit_error" not in meta, valid, meta)
                if not valid:
                    self.assertIsNone(data["snapshot_id"])
                    stream = io.StringIO()
                    with patch.object(restore, "read_object", return_value={"version": "fixture"}), patch.object(
                            Path, "is_file", return_value=True), patch.object(restore.doctor, "_has_legacy", return_value=False), patch.object(
                            restore.trust, "query", return_value=(data, meta)), redirect_stdout(stream):
                        code = restore.main(["restore", "--project", str(ROOT), "--codex-home", str(ROOT / "docs"),
                            "--codex-exe", sys.executable, "--codex-cwd", str(ROOT), "--expect-snapshot", "sha256:" + "a" * 64,
                            "--expect-source", json.dumps(self.source)])
                    value = json.loads(stream.getvalue())["data"]
                    self.assertEqual((code, value["result"], value["write_outcome"]), (3, "write_unconfirmed", "unknown"))
                    self.assertEqual(value["verified_keys"], [])
                    self.assertEqual(set(value["remaining_keys"]), {hook["key"] for hook in self.listing["data"][0]["hooks"]})

    def test_confirmed_write_rechecks_config_fields_after_server_shutdown(self):
        first = self.listing["data"][0]["hooks"][0]
        pending = {first["key"]: first["currentHash"]}
        declaration, content = first["currentHash"].split("/")
        header = '[hooks.state.' + json.dumps(first["key"]) + ']\n'
        complete = (header + 'trusted_hash="' + declaration + '"\ntrusted_content_hash="' + content + '"\n').encode()
        def operation(before, adapter, server):
            adapter.attempted = pending
            return 0, {"write_outcome": "confirmed"}
        for after in (complete, header.encode(), complete.replace(content.encode(), b"wrong"),
                      complete.replace(declaration.encode(), b"wrong"), b"invalid=["):
            with self.subTest(after=after):
                data, meta, _ = self.query_transport(operation=operation, config_rows=(after, after))
                self.assertEqual("_audit_error" not in meta, after == complete, meta)
                if after != complete:
                    self.assertIsNone(data["snapshot_id"])

    def test_restore_scope_preserves_types_in_other_user_hook_fields(self):
        runtime = importlib.import_module("_query_codex_runtime")
        restore = importlib.import_module("workframe_codex_trust")
        first = self.listing["data"][0]["hooks"][0]
        declaration, content = first["currentHash"].split("/")
        old = b'[hooks.state."file:user-hook"]\nenabled=false\n'
        after = (old.replace(b'false', b'0') + ('[hooks.state.' + json.dumps(first["key"]) +
                 ']\ntrusted_hash="' + declaration + '"\ntrusted_content_hash="' + content + '"\n').encode())
        config_file = Path(sys.executable).resolve().parent / "config.toml"
        name = ".hook-trust/" + expected_hook_trust_name(str(config_file), self.pid)
        stored = {".hook-trust/": ("directory",), name: ("file", hashlib.sha256(content.encode()).hexdigest())}
        first["trustStatus"] = "untrusted"
        def operation(before, adapter, server):
            def accept(edits, **kwargs):
                self.accept_trust(edits, **kwargs)
                self.user["config"]["hooks"]["state"]["file:user-hook"] = {"enabled": 0}
            server.config_batch_write.side_effect = accept
            return restore.restore_observation(before, before["snapshot_id"], before["plugin"]["source"],
                                               adapter=adapter, server=server, project=ROOT)
        with patch.object(runtime, "trust_storage_status", return_value="verified"):
            data, meta, _ = self.query_transport(operation=operation, config_rows=(old, after), snapshot_rows=({}, stored))
        self.assertEqual(meta["_operation_result"][0], 0, "The runtime readback alone confirms only the target")
        self.assertIn("_audit_error", meta, "A type change in another user hook must prevent certification")
        stream = io.StringIO()
        with patch.object(restore, "read_object", return_value={"version": "fixture"}), patch.object(
                Path, "is_file", return_value=True), patch.object(restore.doctor, "_has_legacy", return_value=False), patch.object(
                restore.trust, "query", return_value=(data, meta)), redirect_stdout(stream):
            code = restore.main(["restore", "--project", str(ROOT), "--codex-home", str(ROOT / "docs"),
                "--codex-exe", sys.executable, "--codex-cwd", str(ROOT), "--expect-snapshot", "sha256:" + "a" * 64,
                "--expect-source", json.dumps(self.source)])
        value = json.loads(stream.getvalue())["data"]
        self.assertEqual((code, value["result"], value["verified_keys"]), (3, "write_unconfirmed", []))

    def test_linked_storage_is_refused_before_starting_any_external_process(self):
        home = Path(sys.executable).resolve().parent
        storage = home / ".hook-trust"
        for linked in (storage, storage / ("a" * 64)):
            for directory_link in (False, True):
                with self.subTest(linked=linked, directory_link=directory_link), patch.object(
                        Path, "is_symlink", lambda path: not directory_link and path == linked), patch.object(
                        self.trust, "is_dir_link", lambda path: directory_link and path == linked), patch.object(
                        Path, "is_dir", lambda path: path == storage or path.exists()), patch.object(
                        Path, "iterdir", return_value=iter([linked] if linked != storage else [])), patch.object(
                        subprocess, "Popen", side_effect=AssertionError("external process started")):
                    data, meta = self.trust.query(ROOT, {"codex_home": str(home), "codex_exe": sys.executable})
                    self.assertEqual(data["state"], "unverified")
                    self.assertIn("protected_path_overlap", [row["code"] for row in data["problems"]])
                    self.assertNotIn("side_effects", meta)

    def test_query_audits_config_target_bytes_even_if_its_link_is_unchanged(self):
        linked = {"config.toml": ("link", "external-config.toml")}
        data, meta, _ = self.query_transport(snapshot_rows=(linked, linked), config_rows=(b'model="before"', b'model="after"'))
        self.assertEqual(data["state"], "unverified")
        self.assertIsNone(data["snapshot_id"])
        self.assertIn("_audit_error", meta)

    def test_rejected_batch_keeps_exit_one_when_a_concurrent_config_change_is_audited(self):
        import _codex_appserver as cas
        restore = importlib.import_module("workframe_codex_trust")
        self.listing["data"][0]["hooks"][0]["trustStatus"] = "untrusted"
        before = self.inspect()
        server = self.server()
        server.config_batch_write.side_effect = cas.AppServerRpcError("version conflict")
        code, result = self.recovery(server)
        self.assertEqual((code, result["write_outcome"]), (1, "none"))
        metadata = {"_operation_result": (code, result), "_audit_error": "Concurrent config change"}
        stream = io.StringIO()
        with patch.object(restore, "read_object", return_value={"version": "fixture"}), patch.object(
                Path, "is_file", return_value=True), patch.object(restore.doctor, "_has_legacy", return_value=False), patch.object(
                restore.trust, "query", return_value=(before, metadata)), redirect_stdout(stream):
            code = restore.main(["restore", "--project", str(ROOT), "--codex-home", str(ROOT / "docs"),
                "--codex-exe", sys.executable, "--codex-cwd", str(ROOT), "--expect-snapshot", before["snapshot_id"],
                "--expect-source", json.dumps(self.source)])
        value = json.loads(stream.getvalue())["data"]
        self.assertEqual((code, value["result"], value["write_outcome"]), (1, "not_restored", "none"))
        self.assertTrue(value["attempted_keys"], "A request can be sent and definitively rejected")


class RuntimeSideEffectTests(unittest.TestCase):
    def runtime(self):
        return importlib.import_module("_query_codex_runtime")

    def test_versioned_file_allowance_requires_unchanged_database_and_wal(self):
        runtime = self.runtime()
        before = {"state_5.sqlite": ("file", "db"), "state_5.sqlite-wal": ("file", "wal"),
                  "state_5.sqlite-shm": ("file", "before"), "config.toml": ("file", "config")}
        after = dict(before, **{"state_5.sqlite-shm": ("file", "after"),
                              ".tmp/git-fixture/HEAD": ("file", "temp"),
                              "tmp/arg0-windows/codex-arg0fixture/codex.exe": ("file", "arg0"),
                              ".sandbox/sandbox.2026-11-12.log": ("file", "runtime log"),
                              "log/app.log": ("file", "log"), "logs_2.sqlite-wal": ("file", "log db")})
        self.assertEqual(runtime.side_effect_violations(before, after), [])
        for path in ("state_5.sqlite", "state_5.sqlite-wal", "config.toml", "auth.json",
                     "plugins/cache/fixture/core/1.0/hooks/hooks.codex.json", ".tmp/marketplaces/fixture/source"):
            with self.subTest(path=path):
                dirty = dict(after, **{path: ("file", "unauthorized")})
                self.assertIn(path, runtime.side_effect_violations(before, dirty))
                if path in ("state_5.sqlite", "state_5.sqlite-wal"):
                    self.assertIn("state_5.sqlite-shm", runtime.side_effect_violations(before, dirty))
        self.assertTrue(runtime.side_effect_violations(before, after, version="0.167.3"))
        missing_db = {"missing.sqlite-shm": ("file", "shm")}
        self.assertEqual(runtime.side_effect_violations({}, missing_db), ["missing.sqlite-shm"])

    def test_configuration_allowance_only_accepts_the_requested_trusted_hashes(self):
        runtime = self.runtime()
        before = b'model="same"\n[hooks.state."core@fixture:key"]\ntrusted_hash="old"\n'
        declaration, content = "sha256:" + "a" * 64, "sha256:" + "b" * 64
        after = before.replace(b'"old"', json.dumps(declaration).encode()) + ('trusted_content_hash="' + content + '"\n').encode()
        attempted = {"core@fixture:key": declaration + "/" + content}
        self.assertTrue(runtime.trust_only_config_change(before, after, attempted))
        self.assertFalse(runtime.trust_only_config_change(before, after.replace(b'"same"', b'"other"'), attempted))
        self.assertFalse(runtime.trust_only_config_change(before, after, {"other@fixture:key": declaration + "/" + content}))
        self.assertFalse(runtime.trust_only_config_change(before, b"invalid=[", attempted))

    def test_two_field_audit_allows_only_exact_additions_or_target_updates(self):
        runtime = self.runtime()
        probe = importlib.import_module("tools.probe_codex_trust_runtime")
        declaration, content = "sha256:" + "a" * 64, "sha256:" + "b" * 64
        pending = {"core@fixture:key": declaration + "/" + content}
        header = 'model="same"\n[hooks.state."core@fixture:key"]\nenabled=true\n'
        fields = ('trusted_hash="' + declaration + '"\ntrusted_content_hash="' + content + '"\n')
        after = (header + fields).encode()
        for previous in ("", 'trusted_hash="old"\n', 'trusted_content_hash="old"\n',
                         'trusted_hash="old"\ntrusted_content_hash="old"\n'):
            before = (header + previous).encode()
            with self.subTest(previous=previous):
                self.assertTrue(runtime.trust_only_config_change(before, after, pending))
                self.assertTrue(probe.config_matches(before, after, pending))
                self.assertTrue(runtime.trust_only_config_change(before, before, pending), "Partial batches may leave a key unchanged")
        for dirty in (after.replace(declaration.encode(), content.encode()),
                      after.replace(content.encode(), declaration.encode()),
                      after.replace(b'enabled=true', b'enabled=false'),
                      after.replace(b'core@fixture:key', b'core@fixture:other'),
                      after.replace(b'core@fixture:key', b'other@fixture:key'),
                      after + b'[hooks.state."core@fixture:other"]\ntrusted_content_hash="wrong"\n',
                      after + b'[hooks.state."other@fixture:key"]\ntrusted_hash="wrong"\n',
                      after.replace(content.encode(), (content + "\n").encode())):
            with self.subTest(dirty=dirty):
                self.assertFalse(runtime.trust_only_config_change(header.encode(), dirty, pending))
                self.assertFalse(probe.config_matches(header.encode(), dirty, pending))
        for field in ("trusted_hash", "trusted_content_hash"):
            kept = "".join(line + "\n" for line in fields.splitlines() if not line.startswith(field + "="))
            with self.subTest(deleted=field):
                self.assertFalse(runtime.trust_only_config_change(after, (header + kept).encode(), pending))
                self.assertFalse(probe.config_matches(after, (header + kept).encode(), pending))

    def test_configuration_readback_requires_both_persisted_hash_components(self):
        import tomllib
        runtime = self.runtime()
        function = getattr(runtime, "trust_config_readback", None)
        self.assertTrue(callable(function), "Recovery must verify both persisted trust fields")
        declaration, content = "sha256:" + "a" * 64, "sha256:" + "b" * 64
        pending = {"core@fixture:key": declaration + "/" + content}
        header = '[hooks.state."core@fixture:key"]\n'
        fields = ('trusted_hash="' + declaration + '"\n', 'trusted_content_hash="' + content + '"\n')
        self.assertTrue(function(tomllib.loads(header + "".join(fields)), pending))
        for raw in (b"", b"invalid=[", header.encode(), (header + fields[0]).encode(),
                    (header + fields[1]).encode(), (header + "".join(fields)).replace(content, declaration).encode(),
                    (header + "".join(fields)).replace(declaration, content).encode(),
                    (header + "".join(fields)).replace("core@fixture:key", "core@fixture:other").encode()):
            with self.subTest(raw=raw):
                try:
                    config = tomllib.loads(raw.decode())
                except ValueError:
                    config = None
                self.assertFalse(function(config, pending))

    def test_configuration_audits_preserve_unrelated_boolean_integer_and_float_types(self):
        runtime = self.runtime()
        probe = importlib.import_module("tools.probe_codex_trust_runtime")
        declaration, content = "sha256:" + "a" * 64, "sha256:" + "b" * 64
        pending = {"core@fixture:key": declaration + "/" + content}
        seed = ('custom_count=1\ncustom_ratio=1.0\nflags=[true, 1, 1.0]\n'
                '[hooks.state."file:user-hook"]\nenabled=false\n'
                '[hooks.state."core@fixture:key"]\nenabled=true\nnested={flag=true}\n')
        complete = seed + 'trusted_hash="' + declaration + '"\ntrusted_content_hash="' + content + '"\n'
        self.assertTrue(runtime.trust_only_config_change(seed.encode(), complete.encode(), pending))
        self.assertTrue(probe.config_matches(seed.encode(), complete.encode(), pending))
        for old, new in (("custom_count=1", "custom_count=true"), ("custom_count=1", "custom_count=1.0"),
                         ("custom_ratio=1.0", "custom_ratio=1"), ("enabled=false", "enabled=0"),
                         ("enabled=true", "enabled=1"), ("flag=true", "flag=1"),
                         ("[true, 1, 1.0]", "[1, 1, 1.0]"), ("[true, 1, 1.0]", "[true, 1.0, 1.0]")):
            with self.subTest(old=old, new=new):
                dirty = complete.replace(old, new).encode()
                self.assertFalse(runtime.trust_only_config_change(seed.encode(), dirty, pending))
                self.assertFalse(probe.config_matches(seed.encode(), dirty, pending))

    def test_exact_existing_storage_may_only_change_to_the_target_content(self):
        runtime = self.runtime()
        probe = importlib.import_module("tools.probe_codex_trust_runtime")
        name, content = ".hook-trust/" + "a" * 64, "sha256:" + "b" * 64
        old = {".hook-trust/": ("directory",), name: ("file", "old")}
        after = {".hook-trust/": ("directory",), name: ("file", hashlib.sha256(content.encode()).hexdigest())}
        self.assertEqual(runtime.side_effect_violations(old, after, trust_storage=(name, content)), [])
        self.assertEqual(probe.home_violations(old, after, trust_storage=(name, content)), [])
        self.assertEqual(runtime.side_effect_violations(old, after), [name])
        self.assertEqual(probe.home_violations(old, after), [name])

    def test_read_only_queries_reject_hook_trust_creation_modification_and_removal(self):
        runtime = self.runtime()
        stored = {".hook-trust/": ("directory",), ".hook-trust/fixture": ("file", "old")}
        for before, after in (({}, stored), (stored, {}),
                              (stored, dict(stored, **{".hook-trust/fixture": ("file", "new")}))):
            with self.subTest(before=before, after=after):
                changed = sorted(name for name in set(before) | set(after) if before.get(name) != after.get(name))
                self.assertEqual(runtime.side_effect_violations(before, after), changed)

    def test_hook_trust_filename_matches_an_independent_utf8_little_endian_vector(self):
        function = getattr(self.runtime(), "hook_trust_filename", None)
        self.assertTrue(callable(function), "The exact plugin/config filename formula is required")
        config_file = "C:\\fixture\\配置\\codex-home\\config.toml"
        self.assertEqual(function(config_file, "core@fixture"),
                         "dee57dec797e63c310b22bb8ba4d57123631d65a8a062a2fabb2d498b45b0e84")
        probe = importlib.import_module("tools.probe_codex_trust_runtime")
        self.assertEqual(probe.storage_expectation(config_file, "core@fixture", {"key": "declaration/sha256:" + "b" * 64}),
                         (".hook-trust/dee57dec797e63c310b22bb8ba4d57123631d65a8a062a2fabb2d498b45b0e84", "sha256:" + "b" * 64))
        for config, plugin_id in ((config_file, "core@another-market"), (config_file.replace("\\", "/"), "core@fixture"),
                                  ("\\\\?\\" + config_file, "core@fixture")):
            with self.subTest(config=config, plugin_id=plugin_id):
                expected = expected_hook_trust_name(config, plugin_id)
                self.assertEqual(function(config, plugin_id), expected)
                self.assertEqual(probe.storage_expectation(config, plugin_id, {"key": "decl/sha256:" + "b" * 64})[0],
                                 ".hook-trust/" + expected)

    def test_recovery_storage_allowance_is_creation_only_and_exact(self):
        runtime = self.runtime()
        config_file = "C:\\fixture\\配置\\codex-home\\config.toml"
        name = ".hook-trust/dee57dec797e63c310b22bb8ba4d57123631d65a8a062a2fabb2d498b45b0e84"
        content = "sha256:" + "b" * 64
        row = ("file", hashlib.sha256(content.encode("utf-8")).hexdigest())
        stored = {".hook-trust/": ("directory",), name: row}
        allowance = {"trust_storage": (name, content)}
        self.assertEqual(runtime.side_effect_violations({}, stored), sorted(stored))
        self.assertEqual(runtime.side_effect_violations({}, stored, **allowance), [])
        self.assertEqual(runtime.side_effect_violations({".hook-trust/": ("directory",)}, stored, **allowance), [])
        self.assertEqual(runtime.side_effect_violations(stored, stored, **allowance), [])
        self.assertTrue(runtime.side_effect_violations({}, stored, version="0.167.3", **allowance))
        wrong_names = [expected_hook_trust_name(config_file, "other@fixture"),
                       expected_hook_trust_name(config_file, "core@another-market"),
                       expected_hook_trust_name("C:\\FIXTUR~1\\codex-home\\config.toml", "core@fixture"),
                       expected_hook_trust_name("\\\\?\\" + config_file, "core@fixture"),
                       expected_hook_trust_name(config_file.replace("\\", "/"), "core@fixture")]
        for filename in wrong_names:
            wrong = ".hook-trust/" + filename
            with self.subTest(wrong=wrong):
                self.assertIn(wrong, runtime.side_effect_violations({}, {".hook-trust/": ("directory",), wrong: row}, **allowance))
                self.assertIn(wrong, runtime.side_effect_violations({}, dict(stored, **{wrong: row}), **allowance))
        for before, after, forbidden in (
                ({}, {".hook-trust/": ("directory",)}, ".hook-trust/"),
                ({}, {name: row}, name),
                ({}, {".hook-trust/": ("directory",), name: ("file", "wrong")}, name),
                ({}, {".hook-trust/": ("directory",), name: ("link", "outside")}, name),
                ({}, {".hook-trust/": ("link", "outside"), name: row}, ".hook-trust/"),
                (stored, {".hook-trust/": ("directory",)}, name),
                (stored, {}, ".hook-trust/"),
                (stored, {".hook-trust/": ("directory",), name: ("file", "changed")}, name),
                ({".hook-trust/": ("directory",), name: ("file", "old")},
                 {".hook-trust/": ("directory",), name: ("file", "wrong")}, name),
                ({}, {"../.hook-trust/" + name.rpartition("/")[2]: row}, "../.hook-trust/" + name.rpartition("/")[2])):
            with self.subTest(before=before, after=after):
                self.assertIn(forbidden, runtime.side_effect_violations(before, after, **allowance))
        old = {".hook-trust/": ("directory",), name: ("file", "old")}
        self.assertEqual(runtime.side_effect_violations(old, old, **allowance), [], "An unchanged old baseline is not this entry's write")

    def test_storage_readback_compares_exact_bytes_and_rejects_links(self):
        runtime = self.runtime()
        function = getattr(runtime, "trust_storage_status", None)
        self.assertTrue(callable(function), "Recovery must read the bound storage file")
        target = CORE / ".hook-trust" / ("a" * 64)
        content = "sha256:" + "b" * 64
        for raw, status in ((content.encode(), "verified"), ((content + "\n").encode(), "content_mismatch"),
                            (b"other", "content_mismatch")):
            with self.subTest(status=status, raw=raw), patch.object(Path, "is_symlink", return_value=False), patch.object(
                    runtime, "is_dir_link", return_value=False), patch.object(Path, "is_dir", return_value=True), patch.object(
                    Path, "is_file", return_value=True), patch.object(Path, "read_bytes", return_value=raw) as read:
                self.assertEqual(function((target, content)), status)
                read.assert_called_once()
        with patch.object(Path, "is_symlink", return_value=False), patch.object(runtime, "is_dir_link", return_value=False), patch.object(
                Path, "is_file", return_value=False):
            self.assertEqual(function((target, content)), "missing")
        for linked, directory_link in ((target, False), (target.parent, False), (target.parent, True)):
            with self.subTest(linked=linked, directory_link=directory_link), patch.object(
                    Path, "is_symlink", lambda path: not directory_link and path == linked), patch.object(
                    runtime, "is_dir_link", lambda path: directory_link and path == linked), patch.object(
                    Path, "read_bytes", side_effect=AssertionError("followed link")):
                self.assertEqual(function((target, content)), "link")

    def test_independent_probe_storage_allowance_has_the_same_narrow_boundary(self):
        probe = importlib.import_module("tools.probe_codex_trust_runtime")
        name = ".hook-trust/" + "a" * 64
        content = "sha256:" + "b" * 64
        row = ("file", hashlib.sha256(content.encode()).hexdigest())
        stored = {".hook-trust/": ("directory",), name: row}
        allowance = {"trust_config": True, "trust_storage": (name, content)}
        self.assertEqual(probe.home_violations({}, stored), sorted(stored))
        self.assertEqual(probe.home_violations({}, stored, **allowance), [])
        self.assertIn(name, probe.home_violations({}, {".hook-trust/": ("directory",), name: ("file", "wrong")}, **allowance))
        self.assertIn(name, probe.home_violations(stored, {}, **allowance))
        old = {".hook-trust/": ("directory",), name: ("file", "old")}
        self.assertEqual(probe.home_violations(old, stored, **allowance), [])
        other = ".hook-trust/" + "c" * 64
        self.assertIn(other, probe.home_violations({}, dict(stored, **{other: row}), **allowance))
        for path in (name, ".hook-trust/"):
            self.assertIn(path, probe.home_violations({}, dict(stored, **{path: ("link", "outside")}), **allowance))

    def test_real_runtime_probe_checks_effects_independently(self):
        probe = importlib.import_module("tools.probe_codex_trust_runtime")
        before = {"state_5.sqlite": ("file", "db"), "state_5.sqlite-wal": ("file", "wal")}
        after = dict(before, **{"state_5.sqlite-shm": ("file", "shm")})
        self.assertEqual(probe.home_violations(before, after), [])
        after["state_5.sqlite-wal"] = ("file", "changed")
        self.assertIn("state_5.sqlite-shm", probe.home_violations(before, after))
        self.assertIn("state_5.sqlite-wal", probe.home_violations(before, after))
        self.assertIn("auth.json", probe.home_violations({}, {"auth.json": ("file", "secret")}))
        for path in (".hook-trust/", ".hook-trust/fixture"):
            self.assertIn(path, probe.home_violations({}, {path: ("file", "unexpected")}))


class GatesCliTests(QueryFixture):
    def test_connection_layout_and_parse_errors_use_the_gates_envelope(self):
        for gate in ("doctor", "close-check"):
            code, value, _ = self.cli("gates", "--project", self.project, "--gate", gate)
            self.assertEqual((code, value["error"]["code"], value["depends_on"]), (1, "not_connected", []))
        self.connect()
        old = self.project / legacy_rel("state")
        old.mkdir(parents=True)
        code, value, _ = self.cli("gates", "--project", self.project, "--gate", "close-check")
        self.assertEqual((code, value["error"]["code"]), (1, "legacy_layout"))

    def test_normal_and_empty_gates_do_not_write_project_or_git_index(self):
        self.connect()
        put(self.project / "README.md", "fixture\n")
        self.git("init", "-q")
        self.commit_fixture()
        self.touch_tracked(self.project / "README.md")
        before = snapshot(self.root)
        for gate in ("doctor", "close-check"):
            code, value, _ = self.cli("gates", "--project", self.project, "--gate", gate)
            self.assertEqual(code, 0, value)
            self.assertEqual(value["depends_on"], [])
            self.assertEqual(value["data"]["gate"], gate)
            self.assertEqual(sum(value["data"]["summary"].values()), len(value["data"]["checks"]))
            self.assertEqual(snapshot(self.root), before)
            if gate == "close-check":
                self.assertEqual(value["data"]["summary"]["skipped"], 1)

    def test_broken_project_marker_is_a_parse_error_and_no_files_change(self):
        put(self.project / ".workframe-config.json", "{")
        before = snapshot(self.root)
        code, value, _ = self.cli("gates", "--project", self.project, "--gate", "doctor")
        self.assertEqual((code, value["error"]["code"], value["depends_on"]), (1, "parse_error", []))
        self.assertEqual(snapshot(self.root), before)


class TrustCliTests(QueryFixture):
    def setUp(self):
        super().setUp()
        self.connect()
        self.codex_home.mkdir(parents=True)
        put(self.codex_home / "config.toml", "# isolated\n")
        self.options = ("--codex-home", self.codex_home, "--codex-exe", sys.executable)

    def test_real_query_classifies_unsupported_scope_and_redacts_config_values(self):
        before = snapshot(self.root)
        code, value, stderr = self.cli("codex-trust", "--project", self.project, *self.options,
                                      "--codex-config", 'model_providers.fixture.api_key="PRIVATE_FIXTURE_VALUE"')
        self.assertEqual((code, stderr, value["data"]["state"]), (0, "", "unverified"))
        self.assertIn("side_effect_scope_unknown", [p["code"] for p in value["data"]["problems"]])
        self.assertNotIn("PRIVATE_FIXTURE_VALUE", json.dumps(value))
        self.assertNotIn("side_effects", value, "no background service started")
        self.assertIn(".codex/config.toml", value["depends_on"])
        self.assertEqual(snapshot(self.root), before)

    def test_project_config_errors_and_usage_errors_do_not_write(self):
        put(self.project / ".codex/config.toml", "broken = [")
        before = snapshot(self.root)
        code, value, _ = self.cli("codex-trust", "--project", self.project, *self.options)
        self.assertEqual((code, value["error"]["code"]), (1, "parse_error"))
        self.assertIn(".codex/config.toml", value["depends_on"])
        self.assertEqual(snapshot(self.root), before)
        code, value, _ = self.cli("codex-trust", "--project", self.project, *self.options, "--timeout-ms", "30001")
        self.assertEqual((code, value["error"]["code"]), (2, "bad_usage"))
        self.assertEqual(snapshot(self.root), before)

    def test_inside_home_and_linked_config_are_rejected_before_external_launch(self):
        inside = self.project / "codex-home"
        inside.mkdir()
        before = snapshot(self.root)
        code, value, _ = self.cli("codex-trust", "--project", self.project, "--codex-home", inside, "--codex-exe", sys.executable)
        self.assertEqual((code, value["data"]["state"]), (0, "unverified"))
        self.assertIn("protected_path_overlap", [p["code"] for p in value["data"]["problems"]])
        self.assertEqual(snapshot(self.root), before)
        linked_home = self.root / "linked-home"
        linked_home.mkdir()
        try:
            (linked_home / "config.toml").symlink_to(self.project / ".workframe-config.json")
        except OSError as exc:
            self.skipTest("File symlinks unavailable on this host: " + str(exc))
        before = snapshot(self.root)
        code, value, _ = self.cli("codex-trust", "--project", self.project, "--codex-home", linked_home, "--codex-exe", sys.executable)
        self.assertEqual((code, value["data"]["state"]), (0, "unverified"))
        self.assertIn("protected_path_overlap", [p["code"] for p in value["data"]["problems"]])
        self.assertEqual(snapshot(self.root), before)

    def test_restore_cli_has_its_own_envelope_and_does_not_write(self):
        source = {"kind": "directory", "marketplace_id": "fixture", "plugin_name": "core", "plugin_subpath": "plugins/core",
                  "real_path": self.root.as_posix(), "observed_ref": None, "observed_revision": None, "assurance": "registry_binding"}
        before = snapshot(self.root)
        for script in (SCRIPTS / "workframe_codex_trust.py", CORE / "bin/workframe-codex-trust"):
            command = [sys.executable, "-B", str(script), "restore", "--project", str(self.project),
                       *map(str, self.options), "--codex-cwd", str(self.project), "--expect-snapshot", "sha256:" + "a" * 64,
                       "--expect-source", json.dumps(source), "--schema", "workframe.codex-trust-restore.v1"]
            result = subprocess.run(command, cwd=self.root, env=self.env, capture_output=True, timeout=15)
            value = json.loads(result.stdout.decode("utf-8"))
            self.assertEqual(value["schema"], "workframe.codex-trust-restore.v1")
            self.assertEqual(value["operation"], "restore")
            self.assertEqual((result.returncode, value["data"]["write_outcome"], value["data"]["error"]["code"]),
                             (2, "none", "protocol_unsupported"))
            self.assertEqual(value["data"]["attempted_keys"], [])
            self.assertEqual(snapshot(self.root), before)


class QueryPathAliasTests(unittest.TestCase):
    """Read-only Windows aliases of the existing checkout need no temp fixture."""

    def windows_aliases(self):
        if os.name != "nt":
            self.skipTest("Windows path aliases require Windows")
        import ctypes
        from ctypes import wintypes
        get_short = ctypes.windll.kernel32.GetShortPathNameW
        get_short.argtypes = (wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD)
        get_short.restype = wintypes.DWORD
        long_root = ROOT.resolve()
        size = get_short(str(long_root), None, 0)
        self.assertGreater(size, 0, "the existing checkout must have a readable Windows path")
        buffer = ctypes.create_unicode_buffer(size)
        self.assertEqual(get_short(str(long_root), buffer, size), size - 1)
        short_root = Path(buffer.value)
        if short_root == long_root:
            self.skipTest("8.3 aliases are unavailable for the existing checkout")
        prefix = "\\\\?\\"
        return ((short_root, long_root), (long_root, short_root),
                (Path(prefix + str(short_root)), long_root),
                (short_root, Path(prefix + str(long_root))),
                (Path(str(short_root).swapcase()), Path(str(long_root).swapcase())))

    def test_board_paths_normalize_both_windows_aliases(self):
        import _query_board as board
        from workframe_query import QueryError
        for project, root in self.windows_aliases():
            with self.subTest(project=project, board_root=root):
                with patch.object(board, "board_path", return_value=root / "custom/Tasks.yaml"):
                    self.assertEqual(board.dependencies(project),
                                     [".workframe-config.json", "custom/Tasks.yaml"])
                    with patch.object(Path, "read_bytes", return_value=b"tasks:\n- id: alias-task\n"):
                        depends_on = []
                        self.assertEqual(board.read_board(project, [], depends_on, error=QueryError),
                                         [{"id": "alias-task"}])
                    self.assertEqual(depends_on, ["custom/Tasks.yaml"])

    def test_stats_board_group_accepts_windows_short_project(self):
        from datetime import datetime, timezone
        import _query_board as board
        import _query_stats as stats
        from workframe_query import QueryError
        project, root = self.windows_aliases()[0]
        with patch.object(board, "board_path", return_value=root / "custom/Tasks.yaml"), \
                patch.object(Path, "read_bytes", return_value=b"tasks:\n- id: alias-task\n"), \
                patch.object(stats, "read_events", return_value=([], 0)):
            result, _ = stats.run(project, [], [], datetime(2026, 11, 12, tzinfo=timezone.utc),
                                  30, [], error=QueryError)
        self.assertEqual(result["board"], {"state": "ok", "tasks_by_status": [{"key": "unknown", "count": 1}],
                                           "tasks_by_role": [{"key": "unknown", "count": 1}]})
        self.assertEqual(result["completion"], {"state": "ok", "weeks": [], "without_date": 0})

    def test_modules_ledger_accepts_windows_short_project(self):
        import _query_modules as modules
        from workframe_query import QueryError
        project, _ = self.windows_aliases()[0]
        stale = importlib.import_module("check-stale-modules")
        with patch.object(modules, "_mapping_file", return_value={}), \
                patch.object(stale, "load_stale", return_value={"submodules": {}}), \
                patch.object(modules, "_directories", return_value=[]), \
                patch.object(modules, "_markdown", return_value=[]), \
                patch.object(Path, "is_file", return_value=True), \
                patch.object(Path, "read_text", return_value="# Alias ledger\n"):
            result = modules.run(project, {}, [], ["projects/History.md"], error=QueryError)
        self.assertEqual(result["ledgers"], [{"kind": "ledger", "title": "Alias ledger",
                                             "path": "projects/History.md", "type": None, "status": None,
                                             "updated": None, "meta_error": None}])

    def test_board_still_rejects_paths_outside_project(self):
        import _query_board as board
        from workframe_query import QueryError
        with patch.object(board, "board_path", return_value=ROOT.parent / "outside.yaml"):
            with self.assertRaises(QueryError) as caught:
                board.read_board(ROOT, [], [], error=QueryError)
        self.assertEqual((caught.exception.code, caught.exception.detail),
                         ("parse_error", {"file": ".workframe-config.json", "line": 1}))

    def test_modules_still_rejects_paths_outside_project(self):
        import _query_modules as modules
        from workframe_query import QueryError
        with self.assertRaises(QueryError) as caught:
            modules.run(ROOT, {}, [], ["../outside.md"], error=QueryError)
        self.assertEqual((caught.exception.code, caught.exception.detail),
                         ("parse_error", {"file": ".workframe-config.json", "line": 1}))


class TopicBoardUnitTests(unittest.TestCase):
    """Pure normalization tests; filesystem/CLI coverage stays in QueryFixture."""

    def board_module(self):
        try:
            return importlib.import_module("_query_board")
        except ImportError:
            self.fail("board query adapter must be implemented")

    def test_capabilities_advertises_board(self):
        import workframe_query as query
        self.assertIn("board", query.TOPICS)

    def test_board_defaults_and_unknown_status_are_preserved(self):
        board = self.board_module()
        warnings = []
        data = board.normalize_tasks([{"id": "A"}, {"id": "B", "status": "review"}], warnings)
        self.assertEqual(data["statuses"], [
            {"key": key, "order": i} for i, key in enumerate(
                ("pending", "in_progress", "pending_qa", "completed", "blocked", "cancelled", "unknown", "review"))])
        task = data["tasks"][0]
        self.assertEqual((task["id"], task["title"], task["status"]), ("A", "", "unknown"))
        self.assertEqual(task["tags"], [])
        self.assertEqual(task["depends_on"], [])
        for field in ("priority", "assigned_to", "module", "notes", "description", "blocked_reason",
                      "cancel_reason", "actual_output", "signoff_text", "created_at", "updated_at",
                      "completed_at", "cancelled_at"):
            self.assertIsNone(task[field], field)

    def test_dates_distinguish_calendar_dates_from_instants(self):
        from datetime import date, datetime, timedelta, timezone
        board = self.board_module()
        warnings = []
        data = board.normalize_tasks([{"id": "A", "created_at": date(2026, 11, 2),
                                       "updated_at": datetime(2026, 11, 2, 9, tzinfo=timezone(timedelta(hours=-8))),
                                       "completed_at": "2026-11-02T17:00:00Z", "cancelled_at": "2026-02-30"}], warnings)
        task = data["tasks"][0]
        self.assertEqual(task["created_at"], "2026-11-02")
        self.assertEqual(task["updated_at"], "2026-11-02T09:00:00-08:00")
        self.assertEqual(task["completed_at"], "2026-11-02T17:00:00+00:00")
        self.assertIsNone(task["cancelled_at"])
        self.assertTrue(any("cancelled_at" in warning for warning in warnings))

    def test_naive_timestamp_is_not_silently_given_a_timezone(self):
        board = self.board_module()
        warnings = []
        task = board.normalize_tasks([{"id": "A", "completed_at": "2026-11-02T12:30:00"}], warnings)["tasks"][0]
        self.assertIsNone(task["completed_at"])
        self.assertTrue(any("completed_at" in warning for warning in warnings))

    def test_mapping_signoff_keeps_every_key_list_item_and_line(self):
        board = self.board_module()
        first = "first line " + "x" * 160
        second = "second line 第二行"
        signoff = {"reviewer": "qa", "future_key": {"items": ["one", "two"], "detail": first + "\n" + second}}
        text = board.normalize_tasks([{"id": "A", "qa_signoff": signoff}], [])["tasks"][0]["signoff_text"]
        for token in ("reviewer", "qa", "future_key", "items", "one", "two", "detail", first, second):
            self.assertIn(token, text)
        self.assertEqual(board.normalize_tasks([{"id": "B", "qa_signoff": "legacy\nrecord"}], [])["tasks"][0]["signoff_text"],
                         "legacy\nrecord")

    def test_complete_task_fields_are_not_lost(self):
        board = self.board_module()
        values = {"id": "A", "title": "标题", "status": "blocked", "priority": "P1", "assigned_to": "dev",
                  "module": "basic/sub", "tags": ["tag"], "depends_on": ["B"], "notes": "note\nline",
                  "description": "description", "blocked_reason": "why", "cancel_reason": "cancel",
                  "actual_output": "artifact"}
        result = board.normalize_tasks([values], [])["tasks"][0]
        for field, value in values.items():
            self.assertEqual(result[field], value, field)


class TopicBoardCliTests(QueryFixture):
    def board(self):
        return self.cli("board", "--project", self.project)

    def test_board_yaml_and_fast_loader_warning(self):
        self.connect()
        put(self.project / "projects/board.yaml", "tasks:\n- id: A\n  created_at: 2026-11-02\n  qa_signoff: {reviewer: qa}\n")
        code, value, stderr = self.board()
        self.assertEqual((code, stderr), (0, ""))
        self.assertEqual(value["data"]["tasks"][0]["created_at"], "2026-11-02")
        self.assertIn("projects/board.yaml", value["depends_on"])
        import _query_board as board
        import yaml
        with patch.object(yaml, "CSafeLoader", None, create=True):
            warnings = []
            result = board.run(self.project, warnings, [], error=__import__("workframe_query").QueryError)
        self.assertEqual(result["tasks"][0]["id"], "A")
        self.assertTrue(any("SafeLoader" in message for message in warnings))

    def test_empty_board(self):
        self.connect()
        put(self.project / "projects/board.yaml", "tasks: []\n")
        code, value, _ = self.board()
        self.assertEqual((code, value["data"]["tasks"]), (0, []))
        self.assertEqual(len(value["data"]["statuses"]), 6)

    def test_bad_board_keeps_dependency_and_line(self):
        self.connect()
        put(self.project / "projects/board.yaml", "tasks:\n- id: [broken\n")
        code, value, _ = self.board()
        self.assertEqual((code, value["error"]["code"]), (1, "parse_error"))
        self.assertEqual(value["error"]["detail"]["file"], "projects/board.yaml")
        self.assertEqual(value["error"]["detail"]["line"], 3)
        self.assertIn("projects/board.yaml", value["depends_on"])

    def test_invalid_task_shapes_are_parse_errors(self):
        self.connect()
        for tasks in ([None], [{"title": "missing id"}], [{"id": "A"}, {"id": "A"}]):
            put(self.project / "projects/board.yaml", {"tasks": tasks})
            code, value, _ = self.board()
            self.assertEqual((code, value["error"]["code"]), (1, "parse_error"))

    def test_unconnected_legacy_and_missing_board(self):
        code, value, _ = self.board()
        self.assertEqual((code, value["error"]["code"]), (1, "not_connected"))
        self.connect()
        code, value, _ = self.board()
        self.assertEqual((code, value["error"]["code"]), (1, "not_found"))
        (self.project / legacy_rel("state")).mkdir(parents=True)
        code, value, _ = self.board()
        self.assertEqual((code, value["error"]["code"]), (1, "legacy_layout"))

    def test_configured_board_is_used_without_writes(self):
        put(self.project / ".workframe-config.json", {"paths": {"board": "custom/tasks.yaml"}})
        put(self.project / "custom/tasks.yaml", {"tasks": [{"id": "custom"}]})
        self.git("init", "-q")
        self.commit_fixture()
        self.touch_tracked(self.project / "custom/tasks.yaml")
        before = snapshot(self.root)
        plugin_before = snapshot(CORE)
        code, value, _ = self.board()
        self.assertEqual((code, value["data"]["tasks"][0]["id"]), (0, "custom"))
        self.assertIn("custom/tasks.yaml", value["depends_on"])
        self.assertEqual(snapshot(self.root), before)
        self.assertEqual(snapshot(CORE), plugin_before)


class QueryReadRegressionTests(unittest.TestCase):
    """Exercise real readers with byte fixtures, without filesystem setup."""

    def configured_board(self, bom, *, missing=False, section="close_check"):
        project = ROOT / "docs"
        config = {section: {"board": "custom/actual.yaml"},
                  "signoff": {"self_signoff_enabled": False}}
        raw = (bytes([239, 187, 191]) if bom else b"") + json.dumps(config).encode("utf-8")
        original_text, original_bytes = Path.read_text, Path.read_bytes

        def read_text(path, *args, **kwargs):
            if path == project / ".workframe-config.json":
                return raw.decode(kwargs.get("encoding", "utf-8"))
            return original_text(path, *args, **kwargs)

        def read_bytes(path):
            if path == project / "custom/actual.yaml":
                if missing:
                    raise FileNotFoundError(str(path))
                return b"tasks:\n- id: ACTUAL-1\n  status: completed\n- id: ACTUAL-2\n  status: completed\n"
            if path == project / "projects/board.yaml":
                return b"tasks:\n- id: DEFAULT-STALE\n  status: pending\n"
            return original_bytes(path)

        return project, patch.object(Path, "read_text", read_text), patch.object(Path, "read_bytes", read_bytes)

    def test_bom_custom_board_success_and_first_failure_keep_actual_dependencies(self):
        import _query_board as board
        from workframe_query import QueryError
        for bom in (False, True):
            for missing in (False, True):
                with self.subTest(bom=bom, missing=missing):
                    project, text_patch, bytes_patch = self.configured_board(bom, missing=missing)
                    with text_patch, bytes_patch:
                        deps = board.dependencies(project)
                        self.assertEqual(deps, [".workframe-config.json", "custom/actual.yaml"])
                        if missing:
                            with self.assertRaises(QueryError) as failure:
                                board.run(project, [], deps, error=QueryError)
                            self.assertEqual((failure.exception.code, failure.exception.detail),
                                             ("not_found", {"file": "custom/actual.yaml"}))
                        else:
                            value = board.run(project, [], deps, error=QueryError)
                            self.assertEqual([task["id"] for task in value["tasks"]], ["ACTUAL-1", "ACTUAL-2"])
                        self.assertEqual(deps, [".workframe-config.json", "custom/actual.yaml"])

    def test_bom_custom_board_stats_success_and_first_failure(self):
        from datetime import datetime, timezone
        import _query_stats as stats
        from workframe_query import QueryError
        for bom in (False, True):
            for missing in (False, True):
                with self.subTest(bom=bom, missing=missing):
                    project, text_patch, bytes_patch = self.configured_board(bom, missing=missing)
                    with text_patch, bytes_patch, patch.object(stats, "read_events", return_value=([], 0)):
                        deps = stats.dependencies(project)
                        self.assertEqual(deps, [".workframe-config.json", "custom/actual.yaml", ".workframe/state/"])
                        value, _ = stats.run(project, [], deps, datetime(2026, 11, 12, tzinfo=timezone.utc),
                                             30, [], error=QueryError)
                        if missing:
                            for key in ("board", "completion"):
                                self.assertEqual(value[key]["state"], "error")
                                self.assertEqual((value[key]["error"]["code"], value[key]["error"]["detail"]),
                                                 ("not_found", {"file": "custom/actual.yaml"}))
                        else:
                            self.assertEqual(value["board"]["tasks_by_status"], [{"key": "completed", "count": 2}])
                            self.assertEqual(value["completion"], {"state": "ok", "weeks": [], "without_date": 2})
                        self.assertEqual(value["events"]["state"], "ok")

    def test_bom_shared_reader_preserves_paths_fallback_and_explicit_signoff_false(self):
        guard = importlib.import_module("signoff-guard")
        for bom in (False, True):
            with self.subTest(bom=bom):
                project, text_patch, _ = self.configured_board(bom, section="paths")
                with text_patch:
                    self.assertEqual(guard.board_path(project), (project / "custom/actual.yaml").resolve())
                    self.assertFalse(guard.self_signoff_enabled(project))

    def test_decode_error_lines_accept_bom_and_all_newline_forms(self):
        from workframe_query import QueryError, read_object
        for bom in (b"", bytes([239, 187, 191])):
            for newline in (b"\n", b"\r", b"\r\n"):
                for name, prefix in ((".workframe-config.json", b"{"), ("config.toml", b"# header")):
                    with self.subTest(bom=bool(bom), newline=newline, name=name):
                        raw = bom + prefix + newline + b"value=" + bytes([255])
                        def read_text(path, **kwargs):
                            return raw.decode(kwargs["encoding"])
                        with patch.object(Path, "read_text", read_text):
                            with self.assertRaises(QueryError) as failure:
                                read_object(Path(name), name)
                        self.assertEqual(failure.exception.code, "parse_error")
                        self.assertEqual(failure.exception.detail, {"file": name, "line": 2})


class TopicModulesUnitTests(unittest.TestCase):
    def module_adapter(self):
        try:
            return importlib.import_module("_query_modules")
        except ImportError:
            self.fail("modules query adapter must be implemented")

    def test_capabilities_advertises_modules(self):
        self.assertIn("modules", __import__("workframe_query").TOPICS)

    def test_metadata_and_first_heading_fallback(self):
        adapter = self.module_adapter()
        warnings = []
        doc = adapter.document_text("---\ntitle: Metadata\ntype: concept\nstatus: draft\nupdated: 2026-11-02\n---\n# Heading\n",
                                    "projects/modules/basic/shared/guide.md", "shared", warnings)
        self.assertEqual(doc, {"kind": "shared", "title": "Metadata", "path": "projects/modules/basic/shared/guide.md",
                               "type": "concept", "status": "draft", "updated": "2026-11-02", "meta_error": None})
        doc = adapter.document_text("```md\n# Fake\n```\n# Real\n", "plain.md", "other", [])
        self.assertEqual(doc["title"], "Real")

    def test_bad_metadata_is_isolated_and_retains_a_title(self):
        adapter = self.module_adapter()
        for source in ("---\ntitle: [broken\n---\n# Fallback\n", "---\n- list\n---\n# Fallback\n",
                       "---\ntitle: unterminated\n# Fallback\n", "---\nx: !!python/object:evil {}\n---\n# Fallback\n"):
            with self.subTest(source=source):
                doc = adapter.document_text(source, "broken.md", "research", [])
                self.assertEqual(doc["title"], "Fallback")
                self.assertIsNotNone(doc["meta_error"])
                for key in ("type", "status", "updated"):
                    self.assertIsNone(doc[key])

    def test_frontmatter_comments_and_fences_cannot_supply_the_body_title(self):
        adapter = self.module_adapter()
        for prefix in ("description: |\n# not-a-heading", "description: |\n  ```md\n  # not-a-heading",
                       "# not-a-heading\ntitle: ''", "{}", ""):
            for newline in ("\n", "\r", "\r\n"):
                with self.subTest(prefix=prefix, newline=newline):
                    source = ("---\n" + prefix + "\n---\n```md\n# fenced bait\n```\n# Correct Body\n").replace("\n", newline)
                    doc = adapter.document_text(source, "api-surface.md", "api-surface", [])
                    self.assertEqual(doc["title"], "Correct Body")
                    if prefix:
                        self.assertIsNone(doc["meta_error"])

    def test_closed_damaged_frontmatter_still_exposes_body_heading(self):
        adapter = self.module_adapter()
        doc = adapter.document_text("---\n# metadata bait\ntitle: [broken\n---\n# Fallback\n",
                                    "broken.md", "research", [])
        self.assertEqual(doc["title"], "Fallback")
        self.assertIsNotNone(doc["meta_error"])
        for key in ("type", "status", "updated"):
            self.assertIsNone(doc[key])

    def test_metadata_handles_bom_crlf_and_embedded_delimiters(self):
        adapter = self.module_adapter()
        source = chr(0xFEFF) + '---\r\ntitle: "a---b"\r\nstatus: draft\r\n---\r\n# Body\r\n'
        doc = adapter.document_text(source, "guide.md", "other", [])
        self.assertEqual((doc["title"], doc["status"], doc["meta_error"]), ("a---b", "draft", None))

    def test_stale_reason_shape_preserves_unknown_kinds_and_deduplicates_files(self):
        adapter = self.module_adapter()
        result = adapter.stale_details({"reasons": ["code_changed:src/a.py", "git_diff_at_session_start:src/a.py",
                                                     "lock_timeout_during_index_rebuild", "future_kind:opaque"]})
        self.assertEqual(result, {"reasons": [
            {"kind": "code_changed", "path": "src/a.py", "message": "code_changed:src/a.py"},
            {"kind": "git_diff_at_session_start", "path": "src/a.py", "message": "git_diff_at_session_start:src/a.py"},
            {"kind": "lock_timeout_during_index_rebuild", "path": None, "message": "lock_timeout_during_index_rebuild"},
            {"kind": "future_kind", "path": None, "message": "future_kind:opaque"}], "files": ["src/a.py"]})


class TopicModulesCliTests(QueryFixture):
    def modules(self):
        return self.cli("modules", "--project", self.project)

    def test_tree_documents_requirements_ledgers_and_stale_are_read_only(self):
        put(self.project / ".workframe-config.json", {"close_check": {"ledger": "projects/history.md"}})
        base = self.project / "projects/modules/basic"
        sub = base / "sub"
        put(base / "module.yaml", "name: basic\nstatus: active\n")
        put(base / "overview.md", "# Basic\n")
        put(base / "shared/guide.md", "# Shared guide\n")
        put(sub / "submodule.yaml", "name: sub\nstatus: active\n")
        put(sub / "overview.md", "# Sub\n")
        put(sub / "current-state/architecture.md", "---\ntype: current-state\n---\n# Architecture\n")
        put(sub / "research/broken.md", "---\nx: [broken\n---\n# Still visible\n")
        put(sub / "requirements/feature/meta.yaml", "slug: feature\ntitle: Feature\nstatus: active\n")
        put(sub / "requirements/feature/main/prd.md", "---\nstatus: draft\ndescription: Main requirement\n---\n# PRD\n")
        put(self.project / "projects/history.md", "# Ledger\n")
        put(self.project / "projects/specs/plans/approach.md", "# Plan\n")
        stale = state_dir_of(self.project) / "stale-modules.yaml"
        put(stale, "submodules:\n  basic/sub:\n    reasons:\n      - code_changed:src/a.py\n")
        before = snapshot(self.root)
        plugin_before = snapshot(CORE)
        code, value, stderr = self.modules()
        self.assertEqual((code, stderr), (0, ""))
        basic = value["data"]["basics"][0]
        result = basic["subs"][0]
        self.assertEqual((basic["name"], basic["status"], result["name"], result["error"]), ("basic", "active", "sub", None))
        self.assertEqual(result["stale"]["files"], ["src/a.py"])
        broken = next(doc for doc in result["docs"] if doc["path"].endswith("broken.md"))
        self.assertEqual(broken["title"], "Still visible")
        self.assertIsNotNone(broken["meta_error"])
        self.assertEqual(result["requirements"], [{"slug": "feature", "title": "Feature", "status": "active", "subs": [
            {"slug": "main", "prd_path": "projects/modules/basic/sub/requirements/feature/main/prd.md",
             "status": "draft", "description": "Main requirement"}]}])
        self.assertEqual(value["data"]["ledgers"][0]["kind"], "ledger")
        self.assertEqual(value["data"]["cross_module"][0]["kind"], "plan")
        self.assertIn("projects/modules/", value["depends_on"])
        self.assertIn(stale.relative_to(self.project).as_posix(), value["depends_on"])
        self.assertIn("projects/history.md", value["depends_on"])
        self.assertEqual(snapshot(self.root), before)
        self.assertEqual(snapshot(CORE), plugin_before)

    def test_invalid_submodule_does_not_hide_its_documents(self):
        self.connect()
        sub = self.project / "projects/modules/basic/sub"
        put(sub / "submodule.yaml", "name: [broken\n")
        put(sub / "overview.md", "# Preserved\n")
        code, value, _ = self.modules()
        self.assertEqual(code, 0)
        result = value["data"]["basics"][0]["subs"][0]
        self.assertEqual((result["name"], result["path"]), ("sub", "projects/modules/basic/sub"))
        self.assertEqual(result["error"]["code"], "parse_error")
        self.assertEqual(result["docs"][0]["title"], "Preserved")

    def test_empty_unconnected_legacy_and_bad_stale(self):
        code, value, _ = self.modules()
        self.assertEqual((code, value["error"]["code"]), (1, "not_connected"))
        self.connect()
        code, value, _ = self.modules()
        self.assertEqual((code, value["data"]), (0, {"basics": [], "ledgers": [], "cross_module": []}))
        put(state_dir_of(self.project) / "stale-modules.yaml", "submodules: [broken\n")
        code, value, _ = self.modules()
        self.assertEqual((code, value["error"]["code"]), (1, "parse_error"))
        self.assertIn("stale-modules.yaml", value["error"]["detail"]["file"])
        self.assertIn(value["error"]["detail"]["file"], value["depends_on"])


class TopicStatsUnitTests(unittest.TestCase):
    WEEKS = [{"date": "2026-11-02", "start": "2026-11-02T08:00:00Z", "end": "2026-11-09T08:00:00Z"},
             {"date": "2026-10-26", "start": "2026-10-26T07:00:00Z", "end": "2026-11-02T08:00:00Z"}]

    def stats_module(self):
        try:
            return importlib.import_module("_query_stats")
        except ImportError:
            self.fail("stats query adapter must be implemented")

    def test_capabilities_advertises_stats(self):
        self.assertIn("stats", __import__("workframe_query").TOPICS)

    def test_weeks_use_supplied_dst_boundaries_and_date_only_labels(self):
        stats = self.stats_module()
        weeks = stats.parse_weeks(json.dumps(self.WEEKS), error=__import__("workframe_query").QueryError)
        tasks = [{"id": "start", "status": "completed", "completed_at": "2026-11-02T08:00:00Z", "assigned_to": "dev"},
                 {"id": "before", "status": "completed", "completed_at": "2026-11-02T07:59:59Z", "assigned_to": "qa"},
                 {"id": "end", "status": "completed", "completed_at": "2026-11-09T08:00:00Z"},
                 {"id": "calendar", "status": "completed", "completed_at": "2026-11-02"},
                 {"id": "missing", "status": "completed"}, {"id": "bad", "status": "completed", "completed_at": "bad"},
                 {"id": "cancelled", "status": "cancelled", "assigned_to": "cancelled-role"},
                 {"id": "active", "status": "in_progress", "assigned_to": "dev"}]
        warnings = []
        board, completion = stats.board_stats(tasks, weeks, warnings)
        self.assertEqual(completion, {"state": "ok", "weeks": [{"week_start": "2026-11-02", "count": 2},
                                                                  {"week_start": "2026-10-26", "count": 1}], "without_date": 2})
        self.assertEqual(dict((row["key"], row["count"]) for row in board["tasks_by_status"]), {"completed": 6, "in_progress": 1})
        self.assertNotIn("cancelled-role", [row["key"] for row in board["tasks_by_role"]])
        self.assertTrue(any("bad.completed_at" in warning for warning in warnings))

    def test_empty_weeks_have_zero_counts(self):
        stats = self.stats_module()
        weeks = stats.parse_weeks(json.dumps(self.WEEKS), error=__import__("workframe_query").QueryError)
        board, completion = stats.board_stats([], weeks, [])
        self.assertEqual(board, {"state": "ok", "tasks_by_status": [], "tasks_by_role": []})
        self.assertEqual([row["count"] for row in completion["weeks"]], [0, 0])

    def test_bad_weeks_fail_as_usage_errors(self):
        stats = self.stats_module()
        query = __import__("workframe_query")
        variants = (None, "broken", "{}", '[{"date":"bad"}]', json.dumps(list(reversed(self.WEEKS))),
                    json.dumps([dict(self.WEEKS[0], start="2026-11-02T08:00:00")]),
                    json.dumps([dict(self.WEEKS[0], end=self.WEEKS[0]["start"])]))
        for value in variants:
            with self.subTest(value=value):
                with self.assertRaises(query.QueryError) as caught:
                    stats.parse_weeks(value, error=query.QueryError)
                self.assertEqual((caught.exception.code, caught.exception.exit_code), ("bad_usage", 2))

    def test_event_window_usage_and_signal_sources_follow_registry(self):
        from datetime import datetime, timezone
        stats = self.stats_module()
        now = datetime(2026, 11, 12, 12, tzinfo=timezone.utc)
        rows = [{"ts": "2026-11-12T11:00:00Z", "type": kind, "skill": skill} for kind, skill in (
            ("skill_used", "one"), ("skill_invoked", "one"), ("skill_invoked", "only-invoked"),
            ("user_correction", ""), ("turn_failed", ""), ("task_blocked", ""))]
        rows += [{"ts": "2026-11-05T12:00:00Z", "type": "skill_used", "skill": "two"},
                 {"ts": "2026-11-05T11:59:59Z", "type": "skill_used", "skill": "old"},
                 {"ts": "2026-11-13T00:00:00Z", "type": "skill_used", "skill": "future"},
                 {"ts": "bad", "type": "skill_used", "skill": "invalid"}]
        result, expires = stats.event_stats(rows, 2, now, 7)
        self.assertEqual(result["skill_usage"], [{"skill": "one", "count": 1}, {"skill": "two", "count": 1}])
        self.assertEqual(result["range"], {"days": 7, "since": "2026-11-05T12:00:00+00:00", "until": "2026-11-12T12:00:00+00:00"})
        self.assertEqual(result["unparsed_lines"], 3)
        self.assertEqual(result["signals"], {"user_correction": {"count": 1, "sources": ["claude", "codex"]},
                                              "turn_failed": {"count": 1, "sources": ["claude"]},
                                              "task_blocked": {"count": 1, "sources": ["claude", "codex"]}})
        self.assertEqual(expires.isoformat(), "2026-11-12T12:00:01+00:00")

    def test_event_parse_helper_counts_non_objects_and_bad_json(self):
        self.stats_module()
        events, malformed, total = doctor.parse_event_lines(["# header", "", '{"__schema__":"v3"}',
                                                            '{"ts":"a","type":"skill_used"}', "[]", "bad"])
        self.assertEqual(events, [{"ts": "a", "type": "skill_used"}])
        self.assertEqual((malformed, total), ([5, 6], 4))


class TopicStatsCliTests(QueryFixture):
    def stats(self, *extra):
        return self.cli("stats", "--project", self.project, "--weeks", json.dumps(TopicStatsUnitTests.WEEKS), *extra)

    def test_bad_board_does_not_hide_events_and_expiry_is_emitted(self):
        from datetime import datetime, timezone
        self.connect()
        put(self.project / "projects/board.yaml", "tasks: [broken\n")
        event = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "type": "skill_used", "skill": "one"}
        state = state_dir_of(self.project)
        line = json.dumps(event)
        put(state / "events.jsonl", line + "\n")
        put(state / "events.1.spill.jsonl", line + "\n")
        put(state / "events.2.spill.jsonl.merged.20261112", line + "\n" + json.dumps(dict(event, skill="two")) + "\nbad\n")
        before = snapshot(self.root)
        code, value, stderr = self.stats()
        self.assertEqual((code, stderr), (0, ""))
        data = value["data"]
        for key in ("board", "completion"):
            self.assertEqual(data[key]["state"], "error")
            self.assertEqual(data[key]["error"]["code"], "parse_error")
            self.assertNotIn("weeks", data[key])
            self.assertNotIn("tasks_by_status", data[key])
        self.assertEqual(data["events"]["skill_usage"], [{"skill": "one", "count": 1}, {"skill": "two", "count": 1}])
        self.assertEqual(data["events"]["unparsed_lines"], 1)
        self.assertIn(state.relative_to(self.project).as_posix() + "/", value["depends_on"])
        self.assertIn("projects/board.yaml", value["depends_on"])
        self.assertEqual(value["valid_until"], "2026-11-09T08:00:00+00:00")
        self.assertEqual(snapshot(self.root), before)

    def test_bad_event_encoding_does_not_hide_board_or_completion(self):
        self.connect()
        put(self.project / "projects/board.yaml", {"tasks": []})
        state = state_dir_of(self.project)
        state.mkdir(parents=True)
        (state / "events.jsonl").write_bytes(bytes([255]))
        code, value, _ = self.stats()
        self.assertEqual(code, 0)
        self.assertEqual(value["data"]["board"]["state"], "ok")
        self.assertEqual(value["data"]["completion"]["state"], "ok")
        self.assertEqual(value["data"]["events"]["state"], "error")
        self.assertNotIn("skill_usage", value["data"]["events"])
        self.assertEqual(set(value["depends_on"]), {".workframe-config.json", "projects/board.yaml", ".workframe/state/",
                                                     ".claude/workframe-state/", ".claude/agent-memory/", ".claude/memory-archive/"})

    def test_stats_usage_and_topic_specific_arguments(self):
        self.connect()
        for args in (("stats",), ("stats", "--weeks", "bad"), ("stats", "--weeks", "[]", "--days", "14"),
                     ("board", "--weeks", "[]"), ("modules", "--days", "30")):
            code, value, _ = self.cli(*args, "--project", self.project)
            self.assertEqual((code, value["error"]["code"]), (2, "bad_usage"))


class QueryMatrixCliTests(QueryFixture):
    """Contract input matrix through fresh CLI processes and real directories."""

    LEGACY = {".claude/workframe-state/", ".claude/agent-memory/", ".claude/memory-archive/"}
    PROJECT_INPUTS = {".workframe-config.json", ".workframe/state/setup-state.json",
                      ".claude/settings.json", ".claude/settings.local.json", ".codex/config.toml",
                      "AGENTS.md", "./", ".git", ".git/"}

    def known_dependencies(self, topic, *, board="projects/board.yaml", ledger=None, ancestor=False):
        if topic in ("project-status", "codex-trust"):
            paths = self.PROJECT_INPUTS | self.LEGACY
            return paths | ({"../.workframe-config.json"} if ancestor else set())
        paths = {".workframe-config.json"} | self.LEGACY
        if topic == "board":
            return paths | {board}
        if topic == "stats":
            return paths | {board, ".workframe/state/"}
        return paths | {"projects/modules/", "projects/specs/", ".workframe/state/stale-modules.yaml",
                        "projects/changelog.md"} | ({ledger} if ledger else set())

    def matrix_query(self, topic, project, *extra):
        args = ("--weeks", "[]") if topic == "stats" else ()
        return self.cli(topic, "--project", project, *args, *extra)

    def test_first_failure_dependency_sets_for_unconnected_legacy_and_bad_marker(self):
        self.connect()  # Bound ancestor search to this known parent.
        for topic in ("project-status", "codex-trust", "board", "modules", "stats"):
            for mode in ("unconnected", "legacy", "bad-marker"):
                with self.subTest(topic=topic, mode=mode):
                    project = self.project / (topic + "-" + mode)
                    project.mkdir()
                    if mode != "unconnected":
                        put(project / ".workframe-config.json", "{\ninvalid\n" if mode == "bad-marker" else {})
                    if mode == "legacy":
                        (project / ".claude/workframe-state").mkdir(parents=True)
                    before = snapshot(self.root)
                    code, value, stderr = self.matrix_query(topic, project)
                    self.assertEqual(stderr, "")
                    self.assertEqual(set(value["depends_on"]), self.known_dependencies(
                        topic, ancestor=mode == "unconnected" and topic in ("project-status", "codex-trust")))
                    if topic == "project-status" and mode != "bad-marker":
                        self.assertEqual(code, 0, value)
                        self.assertEqual(value["data"]["layout"], "legacy" if mode == "legacy" else None)
                    else:
                        expected = {"unconnected": "not_connected", "legacy": "legacy_layout", "bad-marker": "parse_error"}[mode]
                        self.assertEqual((code, value["error"]["code"]), (1, expected))
                        if mode == "bad-marker":
                            self.assertEqual(value["error"]["detail"], {"file": ".workframe-config.json", "line": 2})
                    self.assertEqual(snapshot(self.root), before)

    def test_custom_board_bom_cli_matrix_with_a_stale_default_board(self):
        for bom in (False, True):
            for missing in (False, True):
                project = self.project / f"bom-{bom}-missing-{missing}"
                config = json.dumps({"close_check": {"board": "custom/actual.yaml"}}).encode("utf-8")
                put(project / "projects/board.yaml", {"tasks": [{"id": "DEFAULT-STALE", "status": "pending"}]})
                (project / ".workframe-config.json").write_bytes((bytes([239, 187, 191]) if bom else b"") + config)
                if not missing:
                    put(project / "custom/actual.yaml", {"tasks": [{"id": "ACTUAL-1", "status": "completed"},
                                                                 {"id": "ACTUAL-2", "status": "completed"}]})
                for topic in ("board", "stats"):
                    with self.subTest(bom=bom, missing=missing, topic=topic):
                        before = snapshot(self.root)
                        code, value, stderr = self.matrix_query(topic, project)
                        self.assertEqual(stderr, "")
                        self.assertEqual(set(value["depends_on"]), self.known_dependencies(topic, board="custom/actual.yaml"))
                        if topic == "board":
                            if missing:
                                self.assertEqual((code, value["error"]["code"], value["error"]["detail"]),
                                                 (1, "not_found", {"file": "custom/actual.yaml"}))
                            else:
                                self.assertEqual(code, 0, value)
                                self.assertEqual([task["id"] for task in value["data"]["tasks"]], ["ACTUAL-1", "ACTUAL-2"])
                        else:
                            self.assertEqual(code, 0, value)
                            if missing:
                                for group in ("board", "completion"):
                                    self.assertEqual(value["data"][group]["state"], "error")
                                    self.assertEqual(value["data"][group]["error"]["detail"], {"file": "custom/actual.yaml"})
                            else:
                                self.assertEqual(value["data"]["board"]["tasks_by_status"], [{"key": "completed", "count": 2}])
                        self.assertEqual(snapshot(self.root), before)

    def test_parse_failures_keep_complete_dependencies_and_exact_locations(self):
        # Project parsing requires a valid launch context, even without a server.
        self.codex_home.mkdir(parents=True)
        trust_options = ("--codex-home", self.codex_home, "--codex-exe", sys.executable)
        # json reports line 2 for the unquoted name; tomllib reports the end of a
        # two-line document; PyYAML's safe loader reports line 2 for an unterminated
        # flow sequence in a document without a final newline.
        cases = (("project-status", ".claude/settings.json", "{\nbroken"),
                 ("project-status", ".codex/config.toml", "# header\nbroken = ["),
                 ("codex-trust", ".codex/config.toml", "# header\nbroken = ["),
                 ("board", "projects/board.yaml", "tasks:\n  - id: [broken"),
                 ("modules", ".workframe/state/stale-modules.yaml", "submodules:\n  bad: ["))
        for index, (topic, rel, text) in enumerate(cases):
            with self.subTest(topic=topic, rel=rel):
                project = self.project / str(index)
                put(project / ".workframe-config.json", {"close_check": {"ledger": "custom/log.md"}})
                put(project / rel, text)
                before = snapshot(self.root)
                options = trust_options if topic == "codex-trust" else ()
                code, value, stderr = self.matrix_query(topic, project, *options)
                self.assertEqual((code, stderr, value["error"]["code"]), (1, "", "parse_error"))
                self.assertEqual(value["error"]["detail"]["file"], rel)
                self.assertEqual(value["error"]["detail"]["line"], 2)
                self.assertEqual(set(value["depends_on"]), self.known_dependencies(topic, ledger="custom/log.md"))
                self.assertEqual(snapshot(self.root), before)

    def test_stats_complete_empty_and_nonzero_cli_data(self):
        from datetime import datetime, timedelta, timezone
        weeks = [{"date": "2026-11-02", "start": "2026-11-02T08:00:00Z", "end": "2026-11-09T08:00:00Z"}]
        for empty in (True, False):
            with self.subTest(empty=empty):
                project = self.project / str(empty)
                put(project / ".workframe-config.json", {})
                tasks = [] if empty else [{"id": "done", "status": "completed", "assigned_to": "dev", "completed_at": "2026-11-03"},
                                         {"id": "undated", "status": "completed", "assigned_to": "dev"},
                                         {"id": "pending", "status": "pending", "assigned_to": "qa"},
                                         {"id": "cancelled", "status": "cancelled", "assigned_to": "excluded"}]
                put(project / "projects/board.yaml", {"tasks": tasks})
                if not empty:
                    at = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(timespec="seconds")
                    put(project / ".workframe/state/events.jsonl", json.dumps({"ts": at, "type": "skill_used", "skill": "fixture"}) + "\n")
                before = snapshot(self.root)
                code, value, stderr = self.cli("stats", "--project", project, "--weeks", json.dumps(weeks))
                self.assertEqual((code, stderr), (0, ""))
                self.assertEqual(set(value["depends_on"]), self.known_dependencies("stats"))
                data = value["data"]
                self.assertEqual(data["board"], {"state": "ok", "tasks_by_status": [] if empty else
                                 [{"key": "completed", "count": 2}, {"key": "pending", "count": 1}],
                                 "tasks_by_role": [] if empty else [{"key": "dev", "count": 2}, {"key": "qa", "count": 1}]})
                self.assertEqual(data["completion"], {"state": "ok", "weeks": [{"week_start": "2026-11-02", "count": 0 if empty else 1}],
                                                       "without_date": 0 if empty else 1})
                until = datetime.fromisoformat(value["generated_at"])
                self.assertEqual(data["events"], {"state": "ok", "range": {"days": 30,
                                 "since": (until - timedelta(days=30)).isoformat(timespec="seconds"), "until": value["generated_at"]},
                                 "skill_usage": [] if empty else [{"skill": "fixture", "count": 1}], "unparsed_lines": 0,
                                 "signals": {"user_correction": {"count": 0, "sources": ["claude", "codex"]},
                                             "turn_failed": {"count": 0, "sources": ["claude"]},
                                             "task_blocked": {"count": 0, "sources": ["claude", "codex"]}}})
                self.assertEqual(snapshot(self.root), before)

    def test_both_gates_handle_real_legacy_and_bad_marker_with_no_dependencies(self):
        for gate in ("doctor", "close-check"):
            for mode in ("legacy", "bad-marker"):
                with self.subTest(gate=gate, mode=mode):
                    project = self.project / (gate + mode)
                    put(project / ".workframe-config.json", "{\ninvalid" if mode == "bad-marker" else {})
                    if mode == "legacy":
                        (project / ".claude/workframe-state").mkdir(parents=True)
                    before = snapshot(self.root)
                    code, value, stderr = self.matrix_query("gates", project, "--gate", gate)
                    self.assertEqual((code, stderr, value["error"]["code"], value["depends_on"]),
                                     (1, "", "legacy_layout" if mode == "legacy" else "parse_error", []))
                    self.assertEqual(snapshot(self.root), before)

    def test_nonempty_close_check_keeps_a_real_error_finding_and_navigation(self):
        self.connect()
        rel = "projects/modules/basic/sub/submodule.yaml"
        put(self.project / rel, "name: sub\ncode_paths:\n  - missing.py\n")
        before = snapshot(self.root)
        code, value, stderr = self.matrix_query("gates", self.project, "--gate", "close-check")
        self.assertEqual((code, stderr, value["depends_on"]), (0, "", []))
        checks = [row for row in value["data"]["checks"] if row["id"] == "code-paths"]
        self.assertTrue(any(row["level"] == "error" and "missing.py" in row["message"] for row in checks), checks)
        self.assertTrue(all(rel in row["files"] for row in checks))
        self.assertGreaterEqual(value["data"]["summary"]["error"], 1)
        self.assertEqual(snapshot(self.root), before)

    def test_marker_only_codex_trust_has_no_target_and_no_writes(self):
        self.connect()
        self.codex_home.mkdir(parents=True)
        before = snapshot(self.root)
        code, value, stderr = self.matrix_query("codex-trust", self.project,
                                               "--codex-home", self.codex_home, "--codex-exe", sys.executable)
        self.assertEqual((code, stderr, value["data"]["state"]), (0, "", "unverified"))
        self.assertEqual(value["data"]["hooks"], [])
        self.assertIsNone(value["data"]["snapshot_id"])
        self.assertFalse(value["data"]["recovery"]["eligible"])
        self.assertEqual(set(value["depends_on"]), self.known_dependencies("codex-trust"))
        self.assertEqual(snapshot(self.root), before)

    def test_restore_preflight_input_matrix_refuses_without_writes(self):
        self.codex_home.mkdir(parents=True)
        source = {"kind": "directory", "marketplace_id": "fixture", "plugin_name": "core", "plugin_subpath": "plugins/core",
                  "real_path": CORE.as_posix(), "observed_ref": None, "observed_revision": None, "assurance": "registry_binding"}
        for mode, expected in (("empty", "protocol_unsupported"), ("unconnected", "not_connected"),
                               ("legacy", "legacy_layout"), ("bad-marker", "parse_error"), ("bad-project-toml", "parse_error")):
            with self.subTest(mode=mode):
                project = self.project / mode
                project.mkdir()
                if mode != "unconnected":
                    put(project / ".workframe-config.json", "{\ninvalid" if mode == "bad-marker" else {})
                if mode == "legacy":
                    (project / ".claude/workframe-state").mkdir(parents=True)
                if mode == "bad-project-toml":
                    put(project / ".codex/config.toml", "# header\nbroken = [")
                before = snapshot(self.root)
                argv = [sys.executable, "-B", str(SCRIPTS / "workframe_codex_trust.py"), "restore", "--project", str(project),
                        "--codex-home", str(self.codex_home), "--codex-exe", sys.executable, "--codex-cwd", str(project),
                        "--expect-snapshot", "sha256:" + "a" * 64, "--expect-source", json.dumps(source),
                        "--schema", "workframe.codex-trust-restore.v1"]
                result = subprocess.run(argv, cwd=self.root, env=self.env, capture_output=True, timeout=15)
                value = json.loads(result.stdout.decode("utf-8"))
                self.assertEqual((result.returncode, result.stderr), (2 if mode == "empty" else 1, b""))
                self.assertEqual(value["schema"], "workframe.codex-trust-restore.v1")
                self.assertNotIn("depends_on", value)
                self.assertEqual(value["data"]["error"]["code"], expected)
                self.assertEqual(value["data"]["write_outcome"], "none")
                self.assertEqual(value["data"]["attempted_keys"], [])
                if mode in ("bad-marker", "bad-project-toml"):
                    self.assertEqual(value["data"]["error"]["detail"]["file"],
                                     ".workframe-config.json" if mode == "bad-marker" else ".codex/config.toml")
                    self.assertEqual(value["data"]["error"]["detail"]["line"], 2)
                self.assertEqual(snapshot(self.root), before)


class QueryTopicEntryPointTests(unittest.TestCase):
    def test_restore_unsupported_schema_is_usage_error_without_a_write(self):
        import workframe_codex_trust as restore
        output = io.StringIO()
        with redirect_stdout(output):
            code = restore.main(["restore", "--project", str(ROOT / "docs"), "--expect-snapshot", "sha256:" + "a" * 64,
                                 "--expect-source", "{}", "--schema", "workframe.codex-trust-restore.v999"])
        value = json.loads(output.getvalue())
        self.assertEqual((code, value["data"]["error"]["code"]), (2, "schema_unsupported"))
        self.assertEqual(value["data"]["error"]["detail"], {"supported": ["workframe.codex-trust-restore.v1"]})
        self.assertEqual(value["data"]["write_outcome"], "none")
        self.assertEqual(value["data"]["attempted_keys"], [])
        self.assertNotIn("depends_on", value)

    def test_capabilities_first_failures_have_no_project_dependencies(self):
        import workframe_query as query
        for args, code, error in ((["--capabilities", "--codex-profile", "fixture"], 2, "bad_usage"),
                                  (["--capabilities", "--schema", "workframe.query.v999"], 1, "schema_unsupported")):
            output = io.StringIO()
            with redirect_stdout(output):
                actual = query.main(args)
            value = json.loads(output.getvalue())
            self.assertEqual((actual, value["error"]["code"], value["depends_on"]), (code, error, []))
            if error == "schema_unsupported":
                self.assertEqual(value["error"]["detail"], {"supported": ["workframe.query.v1"]})

    def test_capabilities_bad_plugin_manifest_has_location_and_empty_dependencies(self):
        import workframe_query as query
        original = Path.read_text
        def read_text(path, *args, **kwargs):
            if path == CORE / ".claude-plugin/plugin.json":
                return "{\ninvalid"
            return original(path, *args, **kwargs)
        output = io.StringIO()
        with patch.object(Path, "read_text", read_text), redirect_stdout(output):
            code = query.main(["--capabilities"])
        value = json.loads(output.getvalue())
        self.assertEqual((code, value["error"]["code"], value["depends_on"]), (1, "parse_error", []))
        self.assertEqual(value["error"]["detail"], {"file": "executing plugin manifest", "line": 2})

    def test_gates_failure_dependencies_stay_empty(self):
        result = subprocess.run([sys.executable, "-B", str(QUERY), "gates", "--project", str(ROOT / "docs"),
                                 "--gate", "doctor"], cwd=ROOT, capture_output=True, timeout=15)
        value = json.loads(result.stdout.decode("utf-8"))
        self.assertEqual((result.returncode, value["error"]["code"]), (1, "not_connected"))
        self.assertEqual(value["depends_on"], [])

    def test_legacy_failures_watch_the_layout_inputs(self):
        import workframe_query as query
        for topic in ("board", "modules", "stats"):
            with self.subTest(topic=topic):
                args = [topic, "--project", str(ROOT / "docs")]
                if topic == "stats":
                    args += ["--weeks", "[]"]
                output = io.StringIO()
                with patch.object(Path, "is_file", return_value=True), patch.object(query, "read_object", return_value={}), \
                        patch.object(doctor, "_has_legacy", return_value=True), redirect_stdout(output):
                    code = query.main(args)
                value = json.loads(output.getvalue())
                self.assertEqual((code, value["error"]["code"]), (1, "legacy_layout"))
                for kind in ("state", "memory", "archive"):
                    self.assertIn(legacy_rel(kind) + "/", value["depends_on"])

    def test_bad_event_encoding_identifies_the_file_and_line(self):
        import _query_stats as stats
        import workframe_query as query
        project = Path("fixture")
        for prefix in (b"", bytes([239, 187, 191])):
            for ending in (b"\n", b"\r\n", b"\r"):
                with self.subTest(prefix=prefix, ending=ending):
                    caught = None
                    raw = prefix + b'{}' + ending + bytes([255])
                    with patch.object(Path, "glob", return_value=[]), patch.object(Path, "read_bytes", return_value=raw):
                        try:
                            stats.read_events(project / ".workframe/state", project, error=query.QueryError)
                        except Exception as exc:
                            caught = exc
                    self.assertEqual(getattr(caught, "code", None), "parse_error")
                    self.assertEqual(caught.detail, {"file": ".workframe/state/events.jsonl", "line": 2})

    def test_requirement_reads_one_snapshot_before_parsing(self):
        import _query_modules as modules
        import workframe_query as query
        project = Path("fixture")
        sub = project / "projects/modules/basic/sub"
        req = sub / "requirements/feature"
        child = req / "main"
        good = "---\nstatus: draft\ndescription: First snapshot\n---\n# PRD\n"
        bad = "---\ndescription: [broken\n---\n# PRD\n"
        with patch.object(modules, "_directories", side_effect=[[req], [child]]), \
                patch.object(modules, "_mapping_file", return_value={"title": "Feature", "status": "active"}), \
                patch.object(Path, "is_file", return_value=True), patch.object(Path, "read_text", side_effect=[good, bad]) as reader:
            try:
                result = modules._requirements(sub, project, [], query.QueryError)
            except Exception as exc:
                result = type(exc).__name__
        self.assertIsInstance(result, list)
        self.assertEqual(result[0]["subs"][0]["description"], "First snapshot")
        self.assertEqual(reader.call_count, 1)

    def test_board_and_stats_unconnected_directory_still_return_json(self):
        for topic in ("board", "stats"):
            for utf8 in (False, True):
                with self.subTest(topic=topic, utf8=utf8):
                    env = dict(os.environ)
                    if utf8:
                        env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
                    command = [sys.executable, "-B", str(QUERY), topic, "--project", str(ROOT / "docs")]
                    if topic == "stats":
                        command += ["--weeks", "[]"]
                    result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, timeout=15)
                    self.assertTrue(result.stdout, result.stderr)
                    value = json.loads(result.stdout.decode("utf-8"))
                    self.assertEqual((result.returncode, value["error"]["code"]), (1, "not_connected"))
                    self.assertEqual(result.stderr, b"")

    def test_board_reader_import_keeps_existing_stream_objects(self):
        source = ("import importlib,json,sys; sys.path.insert(0," + repr(str(SCRIPTS)) + "); "
                  "before=(sys.stdout,sys.stderr); importlib.import_module('signoff-guard'); "
                  "print(json.dumps({'same':before==(sys.stdout,sys.stderr)}))")
        result = subprocess.run([sys.executable, "-B", "-c", source], cwd=ROOT, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout.decode("utf-8")), {"same": True})


class QueryFailureProbeUnitTests(unittest.TestCase):
    def test_probe_distinguishes_assertion_failure_error_and_survival(self):
        try:
            probe = importlib.import_module("probe_query_topics")
        except ImportError:
            self.fail("fine-grained query failure probe must exist")
        class Passing(unittest.TestCase):
            def runTest(self):
                self.assertEqual(1, 1)
        class Failing(unittest.TestCase):
            def runTest(self):
                self.assertEqual(1, 2)
        class Crashing(unittest.TestCase):
            def runTest(self):
                raise ValueError("probe self error")
        self.assertEqual(probe.outcome(Passing()), "survived")
        self.assertEqual(probe.outcome(Failing()), "killed")
        self.assertEqual(probe.outcome(Crashing()), "error")


class QueryBenchmarkUnitTests(unittest.TestCase):
    def benchmark(self):
        try:
            return importlib.import_module("benchmark_workframe_query")
        except ImportError:
            self.fail("repeatable real-command query benchmark must exist")

    def test_summary_uses_nearest_rank_and_reports_every_overrun(self):
        benchmark = self.benchmark()
        self.assertEqual(benchmark.summarize([1, 2, 3, 1001]),
                         {"n": 4, "median_ms": 2.5, "p95_ms": 1001, "max_ms": 1001, "over_1000_ms": 1})
        self.assertEqual(benchmark.summarize([1000])["over_1000_ms"], 0)

    def test_measure_includes_process_wait_and_decodes_json(self):
        benchmark = self.benchmark()
        command = [sys.executable, "-B", "-c", 'import time; time.sleep(0.12); print("{\\"data\\":{}}")']
        record, value = benchmark.measure(command, dict(os.environ), ROOT)
        self.assertEqual(value, {"data": {}})
        self.assertEqual(record["exit_code"], 0)
        self.assertGreaterEqual(record["elapsed_ms"], 110)
        self.assertGreaterEqual(record["decode_ms"], 0)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    unittest.main(verbosity=2)
