#!/usr/bin/env python3
"""模块记忆事件、容量及迁出证据的标准库回归测试。夹具只写临时项目。"""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
REPO_ROOT = Path(__file__).resolve().parents[1]
CORE = REPO_ROOT / "plugins" / "core"
sys.path.insert(0, str(CORE / "scripts"))
import workframe_doctor as doctor  # noqa: E402
from _state_io import memory_dir_of, state_dir_of  # noqa: E402

EVENT_CLI = CORE / "bin" / "workframe-event"
LEDGER_CLI = CORE / "eval-cases" / "memory-pipeline" / "scripts" / "assert_three_ledgers.py"
OLD_KEY = "dev:2026-11-01:原始事实键必须完整保留其内容及序号后缀-2"
OLD_TS = "2026-11-01T01:00:00+00:00"
MOVED_TS = "2026-11-02T01:00:00+00:00"


def write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def run_cli(script, project, *args):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
    return subprocess.run([sys.executable, "-B", str(script), "--project", str(project), *args]
                          if script == LEDGER_CLI else
                          [sys.executable, "-B", str(script), *args, "--project", str(project),
                           "--harness", "codex"],
                          cwd=str(REPO_ROOT), env=env, capture_output=True, text=True,
                          encoding="utf-8", timeout=30)


class ProjectCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="wf-module-events-")
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name)
        self.state = state_dir_of(self.project)

    def events(self):
        return [json.loads(line) for line in (self.state / "events.jsonl").read_text(
            encoding="utf-8").splitlines() if line.strip()]


class EventCliTests(ProjectCase):
    def test_module_correction_keeps_metadata_and_omits_key(self):
        module = "基础模块" * 25 + "/子模块"
        target = "projects/modules/basic/sub/shared/" + "完整路径" * 30 + ".md"
        snapshot = "纠正后的事实" * 20
        sidecar = self.state / "memory-index.json"
        write_text(sidecar, '{"entries":{"original":{"protected":true}}}')
        original = sidecar.read_bytes()
        result = run_cli(EVENT_CLI, self.project, "user-correction", "--scope", "module",
                         "--module", module, "--target-path", target,
                         "--summary", snapshot, "--entry-key", OLD_KEY, "--role", "dev")
        self.assertEqual(result.returncode, 0, result.stderr)
        event, = self.events()
        self.assertEqual(event["module"], module)
        self.assertEqual(event["target_path"], target)
        self.assertEqual(event["summary"], snapshot[:80])
        self.assertEqual(event["scope"], "module")
        self.assertEqual(event["harness"], "codex")
        self.assertNotIn("entry_key", event)
        self.assertNotIn("role", event)
        self.assertEqual(sidecar.read_bytes(), original)
        self.assertFalse(memory_dir_of(self.project).exists())

    def test_module_correction_requires_module(self):
        result = run_cli(EVENT_CLI, self.project, "user-correction", "--scope", "module",
                         "--target-path", "projects/modules/basic/shared/fact.md",
                         "--summary", "正确事实")
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("--module", result.stderr)
        self.assertFalse((self.state / "events.jsonl").exists())

    def test_module_correction_requires_target_path(self):
        result = run_cli(EVENT_CLI, self.project, "user-correction", "--scope", "module",
                         "--module", "basic", "--summary", "正确事实")
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("--target-path", result.stderr)
        self.assertFalse((self.state / "events.jsonl").exists())

    def test_literal_role_placeholders_are_rejected(self):
        for scope in ("role", "<role>"):
            with self.subTest(scope=scope):
                result = run_cli(EVENT_CLI, self.project, "user-correction", "--scope", scope,
                                 "--summary", "不可把占位符写成真实作用域")
                self.assertEqual(result.returncode, 2, result.stdout)
                self.assertFalse((self.state / "events.jsonl").exists())

    def test_custom_role_still_generates_its_original_namespace(self):
        result = run_cli(EVENT_CLI, self.project, "user-correction", "--scope", "database-dev",
                         "--role", "database-dev", "--summary", "执行时先确认数据库版本")
        self.assertEqual(result.returncode, 0, result.stderr)
        event, = self.events()
        self.assertTrue(event["entry_key"].startswith("database-dev:"), event)
        self.assertEqual(event["role"], "database-dev")

    def test_main_correction_keeps_omitting_role_and_key(self):
        result = run_cli(EVENT_CLI, self.project, "user-correction", "--scope", "main",
                         "--summary", "用户协作偏好", "--role", "dev", "--entry-key", OLD_KEY)
        self.assertEqual(result.returncode, 0, result.stderr)
        event, = self.events()
        self.assertNotIn("entry_key", event)
        self.assertNotIn("role", event)

    def test_moved_out_cli_preserves_source_key_paths_and_full_snapshot(self):
        target = "projects/modules/basic/sub/shared/" + "完整路径" * 30 + ".md"
        archive = ".workframe/agent-memory/dev/notes-archive.md"
        snapshot = "保留原文中的步骤和参数" * 25
        result = run_cli(EVENT_CLI, self.project, "memory-moved-out", "--scope", "dev",
                         "--entry-key", OLD_KEY, "--archive-path", archive,
                         "--target-path", target, "--summary", snapshot)
        self.assertEqual(result.returncode, 0, result.stderr)
        event, = self.events()
        self.assertEqual(event["type"], "memory_moved_out")
        self.assertEqual(event["scope"], "dev")
        self.assertEqual(event["entry_key"], OLD_KEY)
        self.assertEqual(event["archive_path"], archive)
        self.assertEqual(event["target_path"], target)
        self.assertEqual(event["summary"], snapshot)
        self.assertFalse((self.state / "memory-index.json").exists())

    def test_moved_out_requires_each_piece_of_trace(self):
        fields = {"--scope": "dev", "--entry-key": OLD_KEY, "--archive-path": "dev/notes-archive.md",
                  "--target-path": "projects/modules/basic/shared/fact.md", "--summary": "事实快照"}
        for missing in fields:
            with self.subTest(missing=missing):
                args = [value for key, value in fields.items() if key != missing for value in (key, value)]
                result = run_cli(EVENT_CLI, self.project, "memory-moved-out", *args)
                self.assertEqual(result.returncode, 2, result.stdout)
                self.assertIn(missing, result.stderr)
                self.assertFalse((self.state / "events.jsonl").exists())

    def test_moved_out_rejects_key_from_another_scope(self):
        result = run_cli(EVENT_CLI, self.project, "memory-moved-out", "--scope", "qa",
                         "--entry-key", OLD_KEY, "--archive-path", "qa/notes-archive.md",
                         "--target-path", "projects/modules/basic/shared/fact.md", "--summary", "事实")
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("entry", result.stderr)
        self.assertFalse((self.state / "events.jsonl").exists())


class CapacityTests(ProjectCase):
    def check_capacity(self):
        paths = doctor.Paths(self.project)
        return doctor.check_memory_capacity({"paths": paths, "scopes": doctor._discover_scopes(paths)})

    def module_file(self, slug, text, marker=True):
        directory = self.project / "projects" / "modules" / slug
        write_text(directory / "module-memory.md", text)
        if marker:
            write_text(directory / ("submodule.yaml" if "/" in slug else "module.yaml"), "name: fixture\n")
        return directory

    def test_basic_and_submodule_overflow_warn_without_role_memory_directory(self):
        for slug in ("basic", "basic/sub"):
            self.module_file(slug, "字" * 1501)
        results = self.check_capacity()
        warnings = [message for level, message in results if level == "warn"]
        self.assertEqual(len(warnings), 2, results)
        self.assertTrue(any("basic/module-memory.md" in msg for msg in warnings), results)
        self.assertTrue(any("basic/sub/module-memory.md" in msg for msg in warnings), results)
        self.assertTrue(all("1501" in msg and "1500" in msg for msg in warnings), results)

    def test_exact_limit_and_python_len_unicode_are_in_budget(self):
        self.module_file("basic", "字" * 1500)
        self.module_file("basic/sub", "😀" * 1500)
        results = self.check_capacity()
        self.assertFalse(any(level in ("warn", "error") for level, _ in results), results)
        self.assertTrue(any("1500" in message for _, message in results), results)

    def test_only_valid_module_levels_are_scanned(self):
        self.module_file("basic", "字", marker=True)
        self.module_file("basic/sub", "字", marker=True)
        self.module_file("unregistered", "字" * 1501, marker=False)
        self.module_file("basic/shared", "字" * 1501, marker=False)
        self.module_file("basic/sub/requirements", "字" * 1501, marker=True)
        results = self.check_capacity()
        self.assertFalse(any(level in ("warn", "error") for level, _ in results), results)

    def test_role_and_shared_limits_remain_active(self):
        memory = memory_dir_of(self.project)
        write_text(memory / "dev" / "MEMORY.md", "字" * 8001)
        write_text(memory / "shared" / "MEMORY.md", "字" * 4001)
        results = self.check_capacity()
        self.assertEqual(sum(level == "warn" for level, _ in results), 2, results)

    def check_header(self):
        paths = doctor.Paths(self.project)
        return doctor.check_memory_header_stale({"paths": paths, "scopes": doctor._discover_scopes(paths)})

    def test_stale_header_phrases_reported_as_info_per_scope(self):
        memory = memory_dir_of(self.project)
        write_text(memory / "dev" / "MEMORY.md",
                   "# dev MEMORY\n\n> 先过归属分流：业务知识落 modules/ 文档、此处至多留指针\n\n## 区\n\n- 条目\n")
        write_text(memory / "shared" / "MEMORY.md",
                   "# 共享\n\n> 业务知识落 modules/ 文档、此处至多留一行指针\n\n## 区\n")
        write_text(memory / "qa" / "MEMORY.md", "# qa\n\n> 总量 ≤ 100 行\n\n## 区\n")
        write_text(memory / "pm" / "MEMORY.md", "# pm\n\n> 总量 ≤100 行；超出由 librarian 生成候选\n\n## 区\n")
        results = self.check_header()
        infos = [msg for level, msg in results if level == "info"]
        self.assertEqual(len(infos), 4, results)
        self.assertTrue(any(m.startswith("pm/") and "≤100 行" in m for m in infos), results)
        self.assertFalse(any(level in ("warn", "error", "ok") for level, _ in results), results)
        self.assertTrue(any(m.startswith("dev/") and "至多留指针" in m for m in infos), results)
        self.assertTrue(any(m.startswith("shared/") and "至多留一行指针" in m and "shared-memory-template" in m
                            for m in infos), results)
        self.assertTrue(any(m.startswith("qa/") and "≤ 100 行" in m for m in infos), results)

    def test_current_header_and_phrase_in_body_are_not_reported(self):
        memory = memory_dir_of(self.project)
        write_text(memory / "dev" / "MEMORY.md",
                   "# dev MEMORY\n\n> 先过归属分流：业务知识落 modules/ 权威文档，此处只收跨模块执行经验\n\n"
                   "## 区\n\n- 条目里提到「至多留指针」与「≤ 100 行」不算文件头\n")
        results = self.check_header()
        self.assertEqual([level for level, _ in results], ["ok"], results)

    def test_doctor_accepts_module_correction_with_empty_sidecar(self):
        event = {"ts": OLD_TS, "type": "user_correction", "scope": "module", "module": "basic/sub",
                 "target_path": "projects/modules/basic/sub/shared/fact.md", "summary": "正确事实"}
        write_text(self.state / "events.jsonl", json.dumps(event, ensure_ascii=False) + "\n")
        write_text(self.state / "memory-index.json", '{"__schema__":"workframe.memory-index.v2","entries":{}}')
        paths = doctor.Paths(self.project)
        events, malformed, total = doctor._load_events(paths)
        ctx = {"paths": paths, "scopes": [], "events": events, "malformed": malformed, "events_total": total}
        results = doctor.check_events_parse(ctx) + doctor.check_sidecar_health(ctx)
        self.assertFalse(any(level in ("warn", "error") for level, _ in results), results)


class LedgerTests(ProjectCase):
    def original(self, **changes):
        return dict({"type": "user_correction", "scope": "dev", "entry_key": OLD_KEY,
                     "ts": OLD_TS, "summary": "原始事实"}, **changes)

    def moved(self, **changes):
        return dict({"type": "memory_moved_out", "scope": "dev", "entry_key": OLD_KEY,
                     "ts": MOVED_TS, "archive_path": ".workframe/agent-memory/dev/notes-archive.md",
                     "target_path": "projects/modules/basic/sub/shared/fact.md", "summary": "事实快照"},
                    **changes)

    def ledger(self, events):
        write_text(self.state / "memory-index.json", '{"__schema__":"workframe.memory-index.v2","entries":{}}')
        write_text(self.state / "events.jsonl", "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events))
        return run_cli(LEDGER_CLI, self.project)

    def assert_dangling(self, result):
        self.assertGreater(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("D: 事件 entry_key 不在 sidecar", result.stdout)

    def test_empty_project_has_no_violations(self):
        result = self.ledger([])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_original_without_migration_evidence_stays_dangling(self):
        self.assert_dangling(self.ledger([self.original()]))

    def test_exact_source_key_with_later_migration_is_accepted(self):
        for event_type in ("user_correction", "memory_promoted", "memory_migrated"):
            with self.subTest(event_type=event_type):
                result = self.ledger([self.original(type=event_type), self.moved()])
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("memory_moved_out", result.stdout)

    def test_cli_migration_is_consumed_by_real_ledger(self):
        self.assert_dangling(self.ledger([self.original()]))
        result = run_cli(EVENT_CLI, self.project, "memory-moved-out", "--scope", "dev",
                         "--entry-key", OLD_KEY, "--archive-path", "dev/notes-archive.md",
                         "--target-path", "projects/modules/basic/sub/module-memory.md",
                         "--summary", "完整事实快照")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        result = run_cli(LEDGER_CLI, self.project)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_other_key_in_same_scope_does_not_excuse_original(self):
        self.assert_dangling(self.ledger([self.original(), self.moved(entry_key=OLD_KEY + "另一个")]))

    def test_wrong_source_scope_does_not_excuse_original(self):
        self.assert_dangling(self.ledger([self.original(), self.moved(scope="qa")]))

    def test_migration_before_original_does_not_excuse_it(self):
        self.assert_dangling(self.ledger([self.moved(ts="2026-10-31T01:00:00+00:00"), self.original()]))

    def test_same_second_later_line_is_valid_migration_order(self):
        result = self.ledger([self.original(), self.moved(ts=OLD_TS)])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_same_second_earlier_line_is_not_migration_evidence(self):
        self.assert_dangling(self.ledger([self.moved(ts=OLD_TS), self.original()]))

    def test_migration_of_one_key_does_not_cover_second_key(self):
        result = self.ledger([self.original(), self.original(entry_key=OLD_KEY + "另一个"), self.moved()])
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(OLD_KEY + "另一个", result.stdout)

    def test_migration_before_key_is_reintroduced_does_not_excuse_new_event(self):
        self.assert_dangling(self.ledger([self.original(), self.moved(), self.original(
            ts="2026-11-03T01:00:00+00:00")]))

    def test_incomplete_migration_does_not_excuse_original(self):
        for field in ("archive_path", "target_path", "summary", "ts"):
            with self.subTest(field=field):
                invalid = self.moved()
                invalid.pop(field)
                self.assert_dangling(self.ledger([self.original(), invalid]))

    def decayed(self, **changes):
        return dict({"type": "memory_decayed", "scope": "dev", "entry_key": OLD_KEY, "ts": MOVED_TS,
                     "summary": "归纳进 dev:2026-10-01:另一条：原文", "source": "notes.md",
                     "age_days": 30, "provenance": "inferred"}, **changes)

    def test_later_decay_of_exact_key_is_accepted(self):
        for event_type in ("user_correction", "memory_promoted", "memory_migrated"):
            with self.subTest(event_type=event_type):
                result = self.ledger([self.original(type=event_type), self.decayed()])
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("memory_decayed", result.stdout)

    def test_decay_does_not_excuse_other_key_scope_or_earlier_event(self):
        for name, events in (
                ("other key", [self.original(), self.decayed(entry_key=OLD_KEY + "另一个")]),
                ("other scope", [self.original(), self.decayed(scope="qa")]),
                ("decay before original", [self.decayed(ts="2026-10-31T01:00:00+00:00"), self.original()]),
                ("same second, earlier line", [self.decayed(ts=OLD_TS), self.original()]),
                ("key reintroduced after decay", [self.original(), self.decayed(),
                                                  self.original(ts="2026-11-03T01:00:00+00:00")]),
                ("decay without ts", [self.original(), {k: v for k, v in self.decayed().items() if k != "ts"}]),
                ("original scope differs from key namespace", [self.original(scope="qa"), self.decayed()]),
                ("both scopes qa, key in dev namespace", [self.original(scope="qa"), self.decayed(scope="qa")])):
            with self.subTest(name):
                self.assert_dangling(self.ledger(events))

    def test_decay_missing_snapshot_does_not_excuse_original(self):
        for field, bad in (("summary", None), ("summary", " "), ("source", None), ("provenance", None),
                           ("age_days", None), ("age_days", "30"), ("age_days", True)):
            with self.subTest(field=field, value=bad):
                invalid = self.decayed()
                if bad is None:
                    invalid.pop(field)
                else:
                    invalid[field] = bad
                self.assert_dangling(self.ledger([self.original(), invalid]))

    def test_same_second_later_line_is_valid_decay_order(self):
        result = self.ledger([self.original(), self.decayed(ts=OLD_TS)])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_every_promotion_mark_needs_its_event(self):
        key = "dev:2026-09-11:合并示例条目正文"
        line = "- 2026-09-01：合并示例条目正文（2026-09-11 从 dev notes 提升）（2026-11-07 从 dev notes 提升，并进）\n"
        write_text(self.project / ".workframe" / "agent-memory" / "dev" / "MEMORY.md", "# dev\n\n" + line)
        write_text(self.state / "memory-index.json", json.dumps({"__schema__": "workframe.memory-index.v2", "entries": {
            key: {"scope": "dev", "created_at": "2026-09-11", "provenance": "user-confirmed", "protected": False,
                  "source": "notes.md"}}}, ensure_ascii=False))

        def promoted(day):
            return json.dumps({"type": "memory_promoted", "scope": "dev", "role": "dev", "entry_key": key,
                               "ts": f"{day}T01:00:00+00:00", "summary": "s", "source": "notes.md"},
                              ensure_ascii=False) + "\n"
        for name, days, want_ok in (("both marks have events", ("2026-09-11", "2026-11-07"), True),
                                    ("appended mark lacks its event", ("2026-09-11",), False),
                                    ("original mark lacks its event", ("2026-11-07",), False)):
            with self.subTest(name):
                write_text(self.state / "events.jsonl", "".join(promoted(d) for d in days))
                result = run_cli(LEDGER_CLI, self.project)
                if want_ok:
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                else:
                    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                    self.assertIn("A: dev 提升条目", result.stdout)

    def test_same_day_event_of_another_scope_does_not_cover(self):
        # 别的角色域当天的事件替不了本域；qa 那条是落点为 skill 的提升（不带 entry_key，D 不查它）
        key, other = "dev:2026-11-07:第一条示例正文", None
        write_text(self.project / ".workframe" / "agent-memory" / "dev" / "MEMORY.md",
                   "# dev\n\n- 2026-11-01：第一条示例正文（2026-11-07 从 dev notes 提升）\n")
        write_text(self.state / "memory-index.json", json.dumps({"__schema__": "workframe.memory-index.v2", "entries": {
            key: {"scope": "dev", "created_at": "2026-11-07", "provenance": "inferred", "protected": False,
                  "source": "notes.md"}}}, ensure_ascii=False))

        def promoted(scope, k):
            ev = {"type": "memory_promoted", "scope": scope, "role": scope,
                  "ts": "2026-11-07T01:00:00+00:00", "summary": "s", "source": "notes.md"}
            if k:
                ev["entry_key"] = k
            return json.dumps(ev, ensure_ascii=False) + "\n"
        write_text(self.state / "events.jsonl", promoted("qa", other))
        result = run_cli(LEDGER_CLI, self.project)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("A: dev 提升条目(2026-11-07)无 memory_promoted 事件", result.stdout)
        write_text(self.state / "events.jsonl", promoted("qa", other) + promoted("dev", key))
        result = run_cli(LEDGER_CLI, self.project)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_module_correction_without_key_needs_no_sidecar(self):
        result = self.ledger([{"type": "user_correction", "scope": "module", "ts": OLD_TS,
                               "module": "basic/sub", "target_path": "projects/modules/basic/sub/shared/fact.md",
                               "summary": "权威事实"}])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_module_promotion_without_key_needs_no_sidecar(self):
        result = self.ledger([{"type": "memory_promoted", "scope": "module", "ts": OLD_TS,
                               "role": "dev", "source": "notes.md",
                               "summary": "basic/sub → projects/modules/basic/sub/module-memory.md"}])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


def load_ledger_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("assert_three_ledgers_under_test", LEDGER_CLI)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DAY = "2026-11-07"


class LedgerKeyMatchTests(ProjectCase):
    """A / A' 按条目的 key 对账：行 → sidecar 碰撞族 → 按 key 计数，不足部分只认日期级证据。"""

    def ledger(self, lines, keys, events, spill=None):
        memory = memory_dir_of(self.project)
        for scope in {"dev", "qa"} | set(lines):
            write_text(memory / scope / "MEMORY.md",
                       "# m\n\n" + "".join(line + "\n" for line in lines.get(scope, [])))
        write_text(self.state / "memory-index.json", json.dumps({
            "__schema__": "workframe.memory-index.v2",
            "entries": {k: {"scope": k.split(":", 1)[0], "created_at": k.split(":", 2)[1], "provenance": "inferred",
                            "protected": False, "source": "notes.md"} for k in keys}}, ensure_ascii=False))
        write_text(self.state / "events.jsonl", "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events))
        for name, evs in (spill or {}).items():
            write_text(self.state / name, "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in evs))
        return run_cli(LEDGER_CLI, self.project)

    @staticmethod
    def ev(key=None, ts=f"{DAY}T01:00:00+00:00", scope="dev", kind="memory_promoted", **extra):
        event = dict({"type": kind, "scope": scope, "role": scope, "ts": ts, "summary": "s",
                      "source": "notes.md"}, **extra)
        if key:
            event["entry_key"] = key
        return event

    def assert_ok(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def assert_red(self, result, needle):
        self.assertGreater(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(needle, result.stdout)

    def test_event_on_another_utc_day_matches_by_key(self):
        key = f"dev:{DAY}:跨日示例正文"
        line = f"- 2026-11-01：跨日示例正文（{DAY} 从 dev notes 提升）"
        for ts in ("2026-11-08T03:00:00+00:00", "2026-11-20T03:00:00+00:00"):
            with self.subTest(ts=ts):
                self.assert_ok(self.ledger({"dev": [line]}, [key], [self.ev(key, ts=ts)]))

    def test_sibling_entry_event_does_not_cover(self):
        k1, k2 = f"dev:{DAY}:第一条示例正文", f"dev:{DAY}:第二条示例正文"
        lines = {"dev": [f"- 2026-11-01：第一条示例正文（{DAY} 从 dev notes 提升）",
                         f"- 2026-11-02：第二条示例正文（{DAY} 从 dev notes 提升）"]}
        self.assert_ok(self.ledger(lines, [k1, k2], [self.ev(k1), self.ev(k2)]))
        self.assert_red(self.ledger(lines, [k1, k2], [self.ev(k2), self.ev(k2)]), "第一条示例正文")

    def test_unkeyed_event_after_spec_date_is_not_evidence(self):
        key = f"dev:{DAY}:无键示例正文"
        line = f"- 2026-11-01：无键示例正文（{DAY} 从 dev notes 提升）"
        for summary in ("→ plugins/core/skills/x/SKILL.md", "无键示例正文"):
            with self.subTest(summary=summary):
                self.assert_red(self.ledger({"dev": [line]}, [key], [self.ev(summary=summary)]),
                                "A: dev 提升条目")

    def test_legacy_unkeyed_event_is_date_level_evidence_only(self):
        line = "- 2026-07-01：旧条目正文（2026-07-19 从 dev notes 提升）"
        result = self.ledger({"dev": [line]}, [], [self.ev(ts="2026-07-20T01:00:00+00:00")])
        self.assert_ok(result)
        self.assertIn("未解析", result.stdout)
        for name, event in (("outside window", self.ev(ts="2026-07-22T01:00:00+00:00")),
                            ("other scope", self.ev(ts="2026-07-19T01:00:00+00:00", scope="qa"))):
            with self.subTest(name):
                self.assert_red(self.ledger({"dev": [line]}, [], [event]), "A: dev 提升条目(2026-07-19)")

    def test_one_legacy_event_covers_one_mark_only(self):
        lines = {"dev": ["- 2026-07-01：旧条目一（2026-07-19 从 dev notes 提升）",
                         "- 2026-07-02：旧条目二（2026-07-19 从 dev notes 提升）"]}
        self.assert_red(self.ledger(lines, [], [self.ev(ts="2026-07-19T01:00:00+00:00")]), "A: dev 提升条目")

    def test_collision_family_counts_per_group(self):
        prefix = "同前缀条目" * 4
        base = f"dev:{DAY}:{prefix}"
        lines = {"dev": [f"- 2026-11-01：{prefix}甲（{DAY} 从 dev notes 提升）",
                         f"- 2026-11-02：{prefix}乙（{DAY} 从 dev notes 提升）"]}
        keys = [base, base + "-2"]
        self.assert_ok(self.ledger(lines, keys, [self.ev(base), self.ev(base + "-2")]))
        self.assert_red(self.ledger(lines, keys, [self.ev(base)]), "组内标注 2 个")
        # 已知限制：族内后缀从行文本分不出归属，一条漏记、另一条多记时总数相等，判不出来
        self.assert_ok(self.ledger(lines, keys, [self.ev(base), self.ev(base)]))

    def test_fragment_taken_with_line_date_resolves(self):
        line = f"- 2026-09-17：[亲测 X] 带日期前缀的正文（{DAY} 从 dev notes 提升）"
        key = f"dev:{DAY}:" + "".join(f"2026-09-17：[亲测 X] 带日期前缀的正文".split())[:20]
        self.assert_ok(self.ledger({"dev": [line]}, [key], [self.ev(key)]))
        self.assert_red(self.ledger({"dev": [line]}, [key], []), "组内标注 1 个")

    def test_collision_suffix_is_told_apart_from_text(self):
        ledger = load_ledger_module()
        cases = (("dev:d:正文TASK-1", {"dev:d:正文TASK-1"}, "dev:d:正文TASK-1"),
                 ("dev:d:正文TASK-1-2", {"dev:d:正文TASK-1", "dev:d:正文TASK-1-2"}, "dev:d:正文TASK-1"),
                 ("dev:d:正文TASK-1", {"dev:d:正文TASK", "dev:d:正文TASK-1"}, "dev:d:正文TASK-1"),
                 ("dev:d:正文-12", {"dev:d:正文", "dev:d:正文-12"}, "dev:d:正文"),
                 ("dev:d:正文-2", {"dev:d:正文-2"}, "dev:d:正文-2"))
        for key, live, want in cases:
            with self.subTest(key=key, live=sorted(live)):
                self.assertEqual(ledger.family_base(key, live), want)

    SPILL_KEY = f"dev:{DAY}:溢出示例正文"
    SPILL_LINE = f"- 2026-11-01：溢出示例正文（{DAY} 从 dev notes 提升）"

    def test_unmerged_spill_events_are_read(self):
        self.assert_ok(self.ledger({"dev": [self.SPILL_LINE]}, [self.SPILL_KEY], [],
                                   spill={"events.4242.spill.jsonl": [self.ev(self.SPILL_KEY)]}))

    def test_merged_spill_archives_are_not_read_twice(self):
        # 并回后的 spill 改名留档，内容已进主文件；这里主文件没有它，证明留档文件不被当成事件源
        self.assert_red(self.ledger({"dev": [self.SPILL_LINE]}, [self.SPILL_KEY], [], spill={
            "events.4242.spill.jsonl.merged.20261107-010000": [self.ev(self.SPILL_KEY)]}), "A: dev 提升条目")

    def test_migration_mark_needs_its_own_migrated_event(self):
        k1, k2 = f"dev:{DAY}:迁入示例一", f"dev:{DAY}:迁入示例二"
        lines = {"dev": [f"- 2026-11-01：迁入示例一（{DAY} 从 auto-memory 迁移）",
                         f"- 2026-11-02：迁入示例二（{DAY} 从 auto-memory 迁移）"]}

        def mig(key):
            return self.ev(key, kind="memory_migrated", source="auto-memory", from_ref="x.md")
        self.assert_ok(self.ledger(lines, [k1, k2], [mig(k1), mig(k2)]))
        self.assert_red(self.ledger(lines, [k1, k2], [mig(k1), mig(k1)]), "A': dev 搬迁条目")
        # 冒充搬迁：事件类型写成 memory_promoted
        self.assert_red(self.ledger(lines, [k1, k2], [mig(k1), self.ev(k2)]), "A': dev 搬迁条目")

    def scope_move(self, migrated_extra):
        source_key, dest_key = "qa:2026-11-05:改域示例正文", f"dev:{DAY}:改域示例正文"
        line = f"- 2026-11-01：改域示例正文（2026-11-05 从 qa notes 提升）（{DAY} 从 qa 迁移）"
        events = [self.ev(source_key, ts="2026-11-05T01:00:00+00:00", scope="qa"),
                  self.ev(dest_key, kind="memory_migrated", source="qa/MEMORY.md", from_ref="qa/MEMORY.md",
                          **dict({"summary": "改域示例正文"}, **migrated_extra))]
        return source_key, self.ledger({"dev": [line]}, [dest_key], events)

    def test_scope_move_traces_source_key(self):
        source_key, result = self.scope_move({"from_entry_key": "qa:2026-11-05:改域示例正文"})
        self.assert_ok(result)
        self.assertIn("from_entry_key", result.stdout)
        self.assertNotIn("未解析", result.stdout)

    def test_scope_move_without_source_key_is_unresolved_not_green(self):
        _, result = self.scope_move({})
        self.assert_ok(result)
        self.assertIn("A: 未解析 dev 搬迁前的提升标注(2026-11-05)", result.stdout)
        self.assertIn("未解析：更晚有来源同域", result.stdout)

    def test_unkeyed_scope_move_of_another_entry_does_not_excuse(self):
        source_key, result = self.scope_move({"summary": "完全不同的另一条"})
        self.assert_red(result, f"D: 事件 entry_key 不在 sidecar：{source_key}")

    def test_scope_move_naming_another_key_does_not_excuse(self):
        source_key, result = self.scope_move({"from_entry_key": "qa:2026-11-05:别的条目"})
        self.assert_red(result, f"D: 事件 entry_key 不在 sidecar：{source_key}")
        self.assertIn("A: dev 提升条目", result.stdout)

    def test_short_key_does_not_claim_a_longer_entry(self):
        k1 = f"dev:{DAY}:安装"
        for longer in ("安装器", "安装/配置", "安装，然后配置", "安装（2号方案）", "安装(1)", "安装（2026-11-07版）"):
            for kind, mark in (("memory_promoted", "从 dev notes 提升"), ("memory_migrated", "从 auto-memory 迁移")):
                with self.subTest(longer=longer, kind=kind):
                    k2 = f"dev:{DAY}:" + "".join(longer.split())[:20]
                    lines = {"dev": [f"- 2026-11-01：安装（{DAY} {mark}）", f"- 2026-11-02：{longer}（{DAY} {mark}）"]}
                    self.assert_ok(self.ledger(lines, [k1, k2], [self.ev(k1, kind=kind), self.ev(k2, kind=kind)]))
                    self.assert_red(self.ledger(lines, [k1, k2], [self.ev(k2, kind=kind), self.ev(k2, kind=kind)]),
                                    "- 2026-11-01：安装（")

    def test_short_key_from_first_half_of_the_line_still_resolves(self):
        # 摘要只取了正文前半句、后面还跟着括号说明：这是合法的既有 key，按 key 计数、不看日期
        key = "dev:2026-04-09:多分类标签型prompt设计三条沉淀"
        line = (f"- 2026-04-09：多分类标签型 prompt 设计三条沉淀（来源：意向标签质检 prompt 评估）"
                f"（{DAY} 从 dev notes 提升）")
        for ts in (f"{DAY}T01:00:00+00:00", "2026-11-09T01:00:00+00:00"):
            with self.subTest(ts=ts):
                result = self.ledger({"dev": [line]}, [key], [self.ev(key, ts=ts)])
                self.assert_ok(result)
                self.assertNotIn("未解析", result.stdout)

    def test_nineteen_char_key_before_a_plain_parenthesis(self):
        short = "十九个字的短摘要正文一二三四五六七八九"
        self.assertEqual(len(short), 19)
        k1, k2 = f"dev:{DAY}:{short}", f"dev:{DAY}:{short}（"
        lines = {"dev": [f"- 2026-11-01：{short}（{DAY} 从 dev notes 提升）",
                         f"- 2026-11-02：{short}（附注）（{DAY} 从 dev notes 提升）"]}
        self.assert_ok(self.ledger(lines, [k1, k2], [self.ev(k1), self.ev(k2)]))
        # 两行在 20 字截断后片段相同，是真正的碰撞：只能按组计数（已知限制），缺一条仍能报出
        self.assert_red(self.ledger(lines, [k1, k2], [self.ev(k2)]), "组内标注 2 个")

    def test_spill_vanishing_mid_read_is_reread_or_refused(self):
        from unittest import mock
        ledger = load_ledger_module()
        main = self.state / "events.jsonl"
        spill = self.state / "events.4242.spill.jsonl"
        event_line = json.dumps(self.ev(f"dev:{DAY}:并回中的事件"), ensure_ascii=False) + "\n"
        original = Path.read_text

        for name, persistent in (("merged mid-read", False), ("keeps vanishing", True)):
            with self.subTest(name):
                write_text(main, "")
                write_text(spill, event_line)
                state = {"merged": False}

                def flaky(path, *args, **kwargs):
                    if path.name == spill.name and (persistent or not state["merged"]):
                        if not state["merged"]:   # 照 merge_spills 的顺序：先摘牌、再追加到主文件
                            spill.unlink()
                            write_text(main, event_line)
                            state["merged"] = True
                        raise FileNotFoundError(2, "No such file", str(path))
                    return original(path, *args, **kwargs)

                with mock.patch.object(Path, "read_text", flaky), \
                        mock.patch.object(Path, "glob", lambda p, pat: iter([spill] if spill.exists() or persistent
                                                                             else [])):
                    events, _, errors = ledger.read_events(main, ".spill.jsonl")
                if persistent:
                    self.assertTrue(any("读时消失" in e for e in errors), errors)
                else:
                    self.assertEqual(errors, [])
                    self.assertEqual(len(events), 1)

    def test_early_unkeyed_hop_does_not_excuse_a_later_traceable_promotion(self):
        # 先从 auto-memory 迁入 dev（合法省略 from_entry_key），在 dev 提升，再迁到 shared（指名 dev key）
        dev_key, shared_key = "dev:2026-11-06:可追溯中段提升", "shared:2026-11-01:可追溯中段提升"
        line = ("- 2026-11-01：可追溯中段提升（2026-11-06 从 auto-memory 迁移）（2026-11-06 从 dev notes 提升，并进）"
                f"（{DAY} 从 dev 迁移）")
        events = [self.ev(dev_key, ts="2026-11-06T01:00:00+00:00", kind="memory_migrated", source="auto-memory",
                          from_ref="x.md", summary="可追溯中段提升"),
                  self.ev(shared_key, scope="shared", kind="memory_migrated", source="dev/MEMORY.md",
                          from_ref="dev/MEMORY.md", summary="可追溯中段提升", from_entry_key=dev_key)]
        result = self.ledger({"shared": [line]}, [shared_key], events)
        self.assert_red(result, "A: shared 提升条目(2026-11-06)")
        self.assertNotIn("A: 未解析", result.stdout)
        self.assert_ok(self.ledger({"shared": [line]}, [shared_key],
                                   events + [self.ev(dev_key, ts="2026-11-06T02:00:00+00:00")]))

    def test_promotion_appended_after_same_day_migration_needs_its_event(self):
        key = f"dev:{DAY}:同日先迁后升正文"
        line = f"- 2026-11-01：同日先迁后升正文（{DAY} 从 auto-memory 迁移）（{DAY} 从 dev notes 提升，并进）"
        mig = self.ev(key, kind="memory_migrated", source="auto-memory", from_ref="x.md")
        self.assert_ok(self.ledger({"dev": [line]}, [key], [mig, self.ev(key)]))
        result = self.ledger({"dev": [line]}, [key], [mig])
        self.assert_red(result, "A: dev 提升条目")
        self.assertNotIn("未解析", result.stdout)

    def test_migration_without_destination_key_does_not_excuse_source(self):
        source_key = "qa:2026-11-05:改域示例正文"
        promoted = self.ev(source_key, ts="2026-11-05T01:00:00+00:00", scope="qa")
        moved = self.ev(None, kind="memory_migrated", source="qa/MEMORY.md", from_ref="qa/MEMORY.md",
                        summary="改域示例正文", from_entry_key=source_key)
        self.assert_red(self.ledger({}, [], [promoted, moved]), f"D: 事件 entry_key 不在 sidecar：{source_key}")
        # 对照：补上目标 key，脚本改报目标 key 悬空，源 key 被解释掉
        result = self.ledger({}, [], [promoted, dict(moved, entry_key=f"dev:{DAY}:改域示例正文")])
        self.assert_red(result, f"D: 事件 entry_key 不在 sidecar：dev:{DAY}:改域示例正文")
        self.assertNotIn(f"VIOLATION D: 事件 entry_key 不在 sidecar：{source_key}", result.stdout)

    def test_unreadable_event_file_is_not_an_empty_file(self):
        for name in ("events.jsonl", "events.4242.spill.jsonl"):
            with self.subTest(name=name):
                self.ledger({}, [], [])
                path = self.state / name
                path.unlink(missing_ok=True)
                path.mkdir()          # 目录读成文件必抛 OSError，跨平台都成立
                try:
                    self.assert_red(run_cli(LEDGER_CLI, self.project), "E: 事件文件读不了")
                finally:
                    path.rmdir()

    def test_since_keeps_historical_gaps_as_info(self):
        key = "dev:2026-10-01:历史缺口示例正文"
        line = f"- 2026-09-01：历史缺口示例正文（2026-10-01 从 dev notes 提升）（{DAY} 从 dev notes 提升，并进）"
        self.ledger({"dev": [line]}, [key], [self.ev(key)])
        result = run_cli(LEDGER_CLI, self.project, "--since", DAY)
        self.assert_ok(result)
        self.assertIn("早于 --since", result.stdout)
        # 反过来缺的是本期那次：事件只有 10 月那条，本期标注必须报违例
        self.ledger({"dev": [line]}, [key], [self.ev(key, ts="2026-10-01T01:00:00+00:00")])
        self.assert_red(run_cli(LEDGER_CLI, self.project, "--since", DAY), "A: dev 提升条目")

    def test_multi_hop_move_with_one_unkeyed_hop_is_unresolved(self):
        # sidecar 承接源创建日（行首日期），C 才对得上
        qa_key, dev_key, shared_key = ("qa:2026-11-05:多跳示例正文", "dev:2026-11-06:多跳示例正文",
                                       "shared:2026-11-01:多跳示例正文")
        line = ("- 2026-11-01：多跳示例正文（2026-11-05 从 qa notes 提升）（2026-11-06 从 qa 迁移）"
                f"（{DAY} 从 dev 迁移）")
        events = [self.ev(qa_key, ts="2026-11-05T01:00:00+00:00", scope="qa"),
                  self.ev(dev_key, ts="2026-11-06T01:00:00+00:00", kind="memory_migrated", source="qa/MEMORY.md",
                          from_ref="qa/MEMORY.md", summary="多跳示例正文"),
                  self.ev(shared_key, scope="shared", kind="memory_migrated", source="dev/MEMORY.md",
                          from_ref="dev/MEMORY.md", summary="多跳示例正文", from_entry_key=dev_key)]
        result = self.ledger({"shared": [line]}, [shared_key], events)
        self.assert_ok(result)
        self.assertIn("A: 未解析 shared 搬迁前的提升标注(2026-11-05)", result.stdout)
        events[1]["from_entry_key"] = qa_key
        result = self.ledger({"shared": [line]}, [shared_key], events)
        self.assert_ok(result)
        self.assertNotIn("未解析", result.stdout)

    def test_line_resolution_stays_within_scope(self):
        dev_key, qa_key = f"dev:{DAY}:同文异域正文", f"qa:{DAY}:同文异域正文"
        line = f"- 2026-11-01：同文异域正文（{DAY} 从 notes 提升）"
        self.assert_red(self.ledger({"dev": [line], "qa": [line]}, [dev_key, qa_key],
                                    [self.ev(dev_key), self.ev(dev_key)]), "A: qa 提升条目")

    def test_line_resolution_respects_key_date(self):
        new_key, old_key = f"dev:{DAY}:同文异日正文", "dev:2026-10-05:同文异日正文"
        lines = {"dev": [f"- 2026-09-01：同文异日正文（{DAY} 从 dev notes 提升）",
                         "- 2026-09-02：同文异日正文（2026-10-05 从 dev notes 提升）"]}
        self.assert_red(self.ledger(lines, [new_key, old_key], [self.ev(new_key), self.ev(new_key)]),
                        "A: dev 提升条目(2026-10-05)")

    def test_correction_line_fragments_resolve(self):
        # 两种取法：去掉 `[纠正]` 的整行、连 `[纠正]` 一起的整行；解析到了才报「组内」，否则报「行解析不到」
        line = f"- [纠正] {DAY}：纠正行的片段示例正文（{DAY} 从 dev notes 提升）"
        for name, source in (("without marker", f"{DAY}：纠正行的片段示例正文"),
                             ("with marker", f"[纠正] {DAY}：纠正行的片段示例正文")):
            with self.subTest(name):
                key = f"dev:{DAY}:" + "".join(source.split())[:20]
                self.ledger({"dev": [line]}, [key], [])
                index = self.state / "memory-index.json"
                data = json.loads(index.read_text(encoding="utf-8"))
                data["entries"][key].update(protected=True, provenance="user-decree", source="[纠正]")
                write_text(index, json.dumps(data, ensure_ascii=False))
                self.assert_red(run_cli(LEDGER_CLI, self.project), "组内标注 1 个")

    def test_spill_without_main_event_file_is_a_valid_state(self):
        self.ledger({"dev": [self.SPILL_LINE]}, [self.SPILL_KEY], [],
                    spill={"events.4242.spill.jsonl": [self.ev(self.SPILL_KEY)]})
        (self.state / "events.jsonl").unlink()
        self.assert_ok(run_cli(LEDGER_CLI, self.project))

    def test_date_evidence_uses_best_overall_pairing(self):
        # 贪心配对会让第一行抢走第二行唯一能用的那条事件
        lines = {"dev": ["- 2026-07-01：旧条目甲（2026-07-19 从 dev notes 提升）",
                         "- 2026-07-02：旧条目乙（2026-07-17 从 dev notes 提升）"]}
        result = self.ledger(lines, [], [self.ev(ts="2026-07-18T01:00:00+00:00"),
                                         self.ev(ts="2026-07-20T01:00:00+00:00")])
        self.assert_ok(result)
        self.assertEqual(result.stdout.count("未解析"), 2, result.stdout)

    def test_date_window_is_one_day(self):
        line = "- 2026-07-01：旧条目正文（2026-07-19 从 dev notes 提升）"
        self.assert_red(self.ledger({"dev": [line]}, [], [self.ev(ts="2026-07-21T01:00:00+00:00")]),
                        "A: dev 提升条目(2026-07-19)")

    def test_key_fragment_matches_the_producer_rule(self):
        samples = ["  先删 空白\t再取前二十个字符的规则示例正文更多", "短", "2026-09-17：[亲测 TASK-1] 正文", ""]
        code = ("import json,sys;sys.path.insert(0,sys.argv[1]);import maintenance_workorder as mw;"
                "print(json.dumps([mw._normalize_key_fragment(s) for s in json.loads(sys.argv[2])]))")
        env = dict(os.environ, CLAUDE_PROJECT_DIR=str(self.project), PYTHONIOENCODING="utf-8")
        result = subprocess.run([sys.executable, "-B", "-c", code, str(CORE / "scripts"),
                                 json.dumps(samples, ensure_ascii=False)], capture_output=True, text=True,
                                encoding="utf-8", env=env, cwd=str(self.project), timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        want = json.loads(result.stdout.strip().splitlines()[-1])
        ledger = load_ledger_module()
        self.assertEqual([ledger.key_fragment(s) for s in samples], want)


class SchemaTests(unittest.TestCase):
    def test_module_promotion_has_non_sidecar_contract(self):
        schema = json.loads((CORE / ".workframe-meta" / "event-schema.json").read_text(encoding="utf-8"))
        fields = schema["events"]["memory_promoted"]["fields"]
        self.assertIn("module", fields["scope"])
        self.assertIn("module", fields["entry_key"])
        self.assertIn("omitted", fields["protected"])
        self.assertIn("omitted", fields["provenance"])

    def test_migration_and_injection_fields_are_registered(self):
        schema = json.loads((CORE / ".workframe-meta" / "event-schema.json").read_text(encoding="utf-8"))
        events = schema["events"]
        self.assertIn("module", events["user_correction"]["fields"])
        self.assertIn("target_path", events["user_correction"]["fields"])
        self.assertIn("memory_moved_out", events)
        self.assertIn("module_memory_injected", events)
        self.assertEqual(events["memory_moved_out"]["reliability"], "model_mediated")
        self.assertEqual(events["module_memory_injected"]["reliability"], "hook_deterministic")
        self.assertTrue({"scope", "entry_key", "archive_path", "target_path", "summary"}.issubset(
            events["memory_moved_out"]["fields"]))
        self.assertIn("from_entry_key", events["memory_migrated"]["fields"])
        self.assertTrue({"session_id", "agent_id", "cycle", "module", "tool", "chars", "outcome"}.issubset(
            events["module_memory_injected"]["fields"]))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    unittest.main(verbosity=2)
