#!/usr/bin/env python3
"""开场提醒收敛与技能身份归一的行为单测：hook / 脚本以子进程真跑，判定模块在进程内直调，喂临时项目。

覆盖：
  - heartbeat-check：只剩启动横幅
  - _overdue（由 session-start-prep 在判完休眠 / 唤醒之后调用）：只把列出来的记为已提醒（超过上限的下次接着列）、
    读真看板（notes 块里的示例任务不算）、自定义看板路径、看板没能检查时每次都报且不动记录（含文件不存在、
    坏 YAML、无 PyYAML 时认不出的写法）、恢复后不重播
  - session-start-prep：正常档位也不打印会话摘要与 onboard 提示；归档 / 唤醒会话不提逾期、不消耗提醒；档位只认配置
  - user-prompt-inject：维护项按「工单 ID ＋ 内容版本」只提醒一次；只认领真正显示过的；内容变了只播那一项；
    「本会话首问」按会话逐个记（两个会话交替提问不互相挤掉）；dormant 不播也不认领
  - _skill_identity / recompute_skill_metrics / log-skill-invoked / stats：裸名与 core: 名归一、项目同名保持裸名、
    自报与调起分列不相加、写入端近时去重按规范身份（裸名与 core: 名算同一个）
  - 维护提醒的种类：Stop 只产出记忆积压与收口待跑（旧的四种条件全满足也不产出）、两种各自驳回互不压制；
    存量的四种 open 卡在 SessionStart 转 retired、到期清理，不进首问提醒与维护工单
  - session-end-flush：后面的阶段被杀时，结束事件与本会话标记的清理已经落地；CC 门先排队；事件锁被占时立刻落 spill
  - 看板 summary、skill 统计、回滚索引换不上去时原文件不动、不留临时文件；重算期间看板被别人改过就不写
  - workframe_event：没带 --harness 时只按独占的会话号环境变量认门，并存或都没有写 unknown
  - workframe_doctor config：运行态残留的旧档位与配置不一致才报，只看磁盘上真实存在的键
  - workframe_doctor promise_producer：两门都登记为没有 producer 的停写事件不对账，其余没有 producer 的照报 error

期望值一律手写在用例里，不取自被测模块。
"""
import json
import os
import subprocess
import sys
import re
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "plugins" / "core" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import _state_io  # noqa: E402

PAST = (date.today() - timedelta(days=30)).isoformat()
PAST2 = (date.today() - timedelta(days=20)).isoformat()
FUTURE = (date.today() + timedelta(days=30)).isoformat()


def run(script, project, payload=None, args=("--harness", "cc"), env_extra=None):
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(project), PYTHONUTF8="1")
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    env.update(env_extra or {})
    r = subprocess.run([sys.executable, str(SCRIPTS / script), *args],
                       input=json.dumps(payload or {}).encode("utf-8"), capture_output=True,
                       cwd=str(project), env=env, timeout=60)
    return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")


def run_without_yaml(project, code):
    """在挡掉 PyYAML 的子进程里跑一段调 `_overdue` 的代码，返回 stdout。"""
    prog = (f"import sys; sys.modules['yaml'] = None; sys.path.insert(0, {str(SCRIPTS)!r}); "
            f"import _overdue; from pathlib import Path; p = Path({str(project)!r}); " + code)
    r = subprocess.run([sys.executable, "-c", prog], capture_output=True, cwd=str(project),
                       env=dict(os.environ, CLAUDE_PROJECT_DIR=str(project), PYTHONUTF8="1"), timeout=60)
    if r.returncode != 0:
        raise AssertionError(r.stderr.decode("utf-8", "replace"))
    return r.stdout.decode("utf-8", "replace")


def reminder_text(out):
    out = out.strip()
    if not out:
        return ""
    return json.loads(out)["hookSpecificOutput"]["additionalContext"]


class Project:
    def __init__(self, td, config=None):
        self.root = Path(td)
        self.state = _state_io.state_dir_of(self.root)
        self.state.mkdir(parents=True, exist_ok=True)
        self.config(config)

    def config(self, extra=None):
        cfg = {"project_name": "probe", "dormant_profile": "normal"}
        cfg.update(extra or {})
        self.write(".workframe-config.json", json.dumps(cfg))

    def write(self, rel, text):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))
        return p

    def board(self, tasks, rel="projects/board.yaml", extra=""):
        lines = ["summary:", "  total: 0", "tasks:"]
        for t in tasks:
            lines.append(f"  - id: {t['id']}")
            for k, v in t.items():
                if k != "id":
                    lines.append(f"    {k}: {v}")
        self.write(rel, "\n".join(lines) + "\n" + extra)

    def activity(self, **fields):
        p = self.state / "activity-state.json"
        data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"session_counter": 1}
        data.update(fields)
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8", newline="")
        return data

    def read_activity(self):
        return json.loads((self.state / "activity-state.json").read_text(encoding="utf-8"))

    def notified(self):
        p = self.state / "heartbeat-state.json"
        return json.loads(p.read_text(encoding="utf-8")).get("overdue_notified") if p.exists() else None


def task(i, deadline=PAST, status="pending"):
    return {"id": i, "status": status, "deadline": f'"{deadline}"'}


NOTES_EXAMPLE = ("  - id: REAL-NOTE\n    status: pending\n    notes: |\n"
                 f"      - id: EXAMPLE\n        deadline: {PAST}\n")


class BannerTests(unittest.TestCase):
    def test_heartbeat_prints_banner_only(self):
        with tempfile.TemporaryDirectory() as td:
            p = Project(td)
            p.board([task("A")])
            rc, out, err = run("heartbeat-check.py", p.root)
            self.assertEqual(rc, 0, err)
            self.assertIn("[probe]", out)
            self.assertIn("会话启动", out)
            for gone in ("HEARTBEAT_OK", "周计划", "周复盘", "月度", "逾期"):
                self.assertNotIn(gone, out)


class OverdueTests(unittest.TestCase):
    def setUp(self):
        import _overdue
        self.od = _overdue
        # 外层临时目录归本用例独占：项目建在它下面，「项目外」的样例文件也放在这一层，不碰共享临时目录
        self.td = tempfile.TemporaryDirectory()
        root = Path(self.td.name) / "proj"
        root.mkdir()
        self.p = Project(str(root))

    def tearDown(self):
        self.td.cleanup()

    def line(self):
        return self.od.reminder_line(self.p.root) or ""

    def test_lists_once_and_ignores_notes_examples_and_done(self):
        self.p.board([task("A"), task("DONE", status="completed"), task("LATER", deadline=FUTURE)],
                     extra=NOTES_EXAMPLE)
        first = self.line()
        self.assertIn("逾期任务 1 个", first)
        self.assertIn(f"A（截止 {PAST}）", first)
        for absent in ("EXAMPLE", "DONE", "LATER"):
            self.assertNotIn(absent, first)
        self.assertEqual(self.p.notified(), [f"A@{PAST}"])
        self.assertEqual(self.line(), "", "全部列过：静默")

    def test_more_than_cap_claims_only_listed_and_lists_rest_next_time(self):
        self.p.board([task(f"T{i}") for i in range(1, 8)])
        first = self.line()
        self.assertIn("逾期任务 7 个", first)
        listed = [f"T{i}" for i in range(1, 8) if f"T{i}（" in first]
        self.assertEqual(len(listed), 5)
        self.assertIn("其中 2 个下次开场再列", first)
        self.assertEqual(len(self.p.notified()), 5, "没列出来的不记为已提醒")
        second = self.line()
        rest = [f"T{i}" for i in range(1, 8) if f"T{i}（" in second]
        self.assertEqual(sorted(rest), sorted(set(f"T{i}" for i in range(1, 8)) - set(listed)))
        self.assertIn("之前提醒过", second)
        self.assertEqual(self.line(), "")
        self.assertEqual(len(self.p.notified()), 7)

    def test_new_item_listed_alone_and_resolved_items_leave(self):
        self.p.board([task("A")])
        self.line()
        self.p.board([task("A"), task("B", deadline=PAST2)])
        grown = self.line()
        self.assertIn("逾期任务 2 个", grown)
        self.assertIn("B（截止", grown)
        self.assertNotIn("A（截止", grown)
        self.assertIn("另有 1 个（之前提醒过）", grown)
        self.p.board([task("A", deadline=FUTURE)])
        self.assertEqual(self.line(), "")
        self.assertEqual(self.p.notified(), [], "不再逾期的出列")
        self.p.board([task("A")])
        self.assertIn("A（截止", self.line(), "出列后再逾期会重新提醒")

    def test_custom_board_path_from_config(self):
        self.p.config({"close_check": {"board": "custom/b.yaml"}})
        self.p.board([task("C1")], rel="custom/b.yaml")
        self.assertIn("C1（截止", self.line())

    def test_unavailable_board_reports_every_time_and_keeps_record(self):
        self.p.board([task("A")])
        self.line()
        before = (self.p.state / "heartbeat-state.json").read_bytes()
        self.p.config({"close_check": {"board": "missing/board.yaml"}})
        for _ in range(2):
            self.assertIn("看板读不出来", self.line())
        self.assertEqual((self.p.state / "heartbeat-state.json").read_bytes(), before)
        self.p.config()
        self.assertEqual(self.line(), "", "恢复后不重播已列过的")
        self.p.write("projects/board.yaml", "tasks: [\n  - id: broken\n")
        try:
            import yaml  # noqa: F401
        except ImportError:
            return
        for _ in range(2):
            self.assertIn("看板读不出来", self.line())
        self.assertEqual((self.p.state / "heartbeat-state.json").read_bytes(), before)

    def test_without_pyyaml_block_style_reads_and_flow_style_is_unavailable(self):
        self.p.board([task("A"), {"id": "DONE", "status": "completed  # 已验收", "deadline": f'"{PAST}"'}],
                     extra=NOTES_EXAMPLE)
        out = run_without_yaml(self.p.root, "print(_overdue.reminder_line(p))")
        self.assertIn("逾期任务 1 个", out)
        self.assertNotIn("EXAMPLE", out)
        (self.p.state / "heartbeat-state.json").unlink()
        self.p.write("projects/board.yaml", f"tasks: [{{id: A, status: pending, deadline: {PAST}}}]\n")
        out = run_without_yaml(self.p.root, "print(_overdue.reminder_line(p))")
        self.assertIn("看板读不出来", out, "认不出的非空看板不能当成空看板")
        self.assertFalse((self.p.state / "heartbeat-state.json").exists())


    def test_without_pyyaml_unreadable_shapes_keep_record_and_empty_list_clears(self):
        self.p.board([task("A")])
        self.line()
        before = (self.p.state / "heartbeat-state.json").read_bytes()
        shapes = {
            "缺 tasks": "summary:\n  total: 0\n",
            "字段顺序不同的条目": f"tasks:\n  - id: B\n    status: pending\n  - status: pending\n    id: A\n    deadline: {PAST}\n",
            "重复 ID": f"tasks:\n  - id: A\n    deadline: {PAST}\n  - id: A\n    deadline: {PAST}\n",
            "带引号的 '[]'（是字符串）": "tasks: '[]'\n",
            "只剩一行 tasks:（空值 / 截断）": "summary:\n  total: 0\ntasks:\n",
        }
        for label, text in shapes.items():
            self.p.write("projects/board.yaml", text)
            for _ in range(2):
                self.assertIn("看板读不出来", run_without_yaml(self.p.root, "print(_overdue.reminder_line(p))"), label)
            self.assertEqual((self.p.state / "heartbeat-state.json").read_bytes(), before, label)
        self.p.config({"close_check": {"board": "../outside.yaml"}})
        (self.p.root.parent / "outside.yaml").write_text(f"tasks:\n  - id: X\n    deadline: {PAST}\n",
                                                         encoding="utf-8", newline="")
        try:
            self.assertIn("看板读不出来", run_without_yaml(self.p.root, "print(_overdue.reminder_line(p))"), "项目外路径")
        finally:
            (self.p.root.parent / "outside.yaml").unlink()
        self.assertEqual((self.p.state / "heartbeat-state.json").read_bytes(), before)
        self.p.config()
        self.p.write("projects/board.yaml", "summary:\n  total: 0\ntasks: []\n")
        self.assertEqual(run_without_yaml(self.p.root, "print(_overdue.reminder_line(p))").strip(), "None",
                         "新装模板的 tasks: [] 是读到了空看板")
        self.assertEqual(self.p.notified(), [], "空看板让 A 出列")

    def test_stale_observation_does_not_claim(self):
        self.p.board([task("A"), task("B")])
        self.assertIn("B（截止", self.line())
        before = self.p.notified()
        # 甲读到的是一份临时的空看板（摘要随这份字节）；随后看板恢复成含 A、B 的那份
        self.p.write("projects/board.yaml", "tasks: []\n")
        tasks, seen = self.od.load_tasks(self.p.root)
        self.p.board([task("A"), task("B")])
        shown, _, _ = self.od.claim(self.p.root, self.od.overdue(tasks), seen)
        self.assertEqual(shown, [], "据以认领的那份字节已不是当前看板：整次不认领")
        self.assertEqual(self.p.notified(), before, "旧观测不得把刚记下的项出列")
        self.assertEqual(self.line(), "", "下一个会话不重播")
        shown, _, _ = self.od.claim(self.p.root, [], None)
        self.assertEqual((shown, self.p.notified()), ([], before), "拿不到摘要时不提交")

    def test_reminder_line_claims_against_the_bytes_it_parsed(self):
        """reminder_line 的摘要必须来自解析的那一次读取：第一次读到临时空板、之后的读取都是含 A、B 的看板时，
        不得用空板的结论把已记下的 A、B 出列。"""
        from unittest import mock
        self.p.board([task("A"), task("B")])
        self.line()
        before = self.p.notified()
        real = self.od.read_board_bytes
        calls = {"n": 0}

        def first_read_is_empty(project_dir):
            calls["n"] += 1
            path, raw = real(project_dir)
            return (path, b"tasks: []\n") if calls["n"] == 1 else (path, raw)
        with mock.patch.object(self.od, "read_board_bytes", first_read_is_empty):
            self.assertIsNone(self.od.reminder_line(self.p.root))
        self.assertGreaterEqual(calls["n"], 2, "锁内必须再读一次当前看板")
        self.assertEqual(self.p.notified(), before)


class SessionStartTests(unittest.TestCase):
    def start(self, p, sid="s1"):
        rc, out, err = run("session-start-prep.py", p.root, {"session_id": sid, "source": "startup"})
        self.assertEqual(rc, 0, err)
        return out

    def test_normal_profile_has_no_digest_no_onboard_and_lists_overdue(self):
        with tempfile.TemporaryDirectory() as td:
            p = Project(td)
            p.write(".workframe/state/session-digest-latest.md", "# Session Digest (latest)\n\n- x\n")
            p.board([task("A")])
            out = self.start(p)
            self.assertNotIn("[last-session-digest]", out)
            self.assertNotIn("Session Digest", out)
            self.assertNotIn("/core:onboard", out)
            self.assertIn("A（截止", out)
            self.assertNotIn("A（截止", self.start(p, "s2"), "第二个会话不重播")

    def test_archive_profile_from_config_skips_overdue(self):
        with tempfile.TemporaryDirectory() as td:
            p = Project(td, {"dormant_profile": "archive"})
            p.activity(dormant_profile="normal")
            p.board([task("A")])
            out = self.start(p)
            self.assertIn("[archive]", out, "档位取自配置（运行态里的旧值 normal 不再读）")
            self.assertNotIn("逾期", out)
            self.assertIsNone(p.notified(), "归档态不消耗提醒")

    def test_persistent_dormant_session_skips_overdue(self):
        with tempfile.TemporaryDirectory() as td:
            p = Project(td)
            p.activity(dormant=True, last_session_at="2020-01-01T00:00:00+00:00")
            p.board([task("A")])
            out = self.start(p)
            self.assertIn("[dormant]", out)
            self.assertNotIn("逾期", out)
            self.assertIsNone(p.notified(), "持续休眠不消耗提醒")

    def test_wake_up_session_skips_overdue_then_next_session_lists(self):
        with tempfile.TemporaryDirectory() as td:
            p = Project(td)
            p.activity(last_session_at="2020-01-01T00:00:00+00:00")
            p.board([task("A")])
            out = self.start(p)
            self.assertIn("[wake-up]", out)
            self.assertNotIn("逾期", out)
            self.assertIsNone(p.notified(), "唤醒会话不消耗提醒")
            self.assertIn("A（截止", self.start(p, "s2"), "下一个会话恢复正常、照常提醒")


def pm(i, details="d", kind="memory_backlog", severity="info"):
    return {"id": f"PM-{i}", "kind": kind, "severity": severity, "status": "open", "details": details,
            "dedup_key": f"k{i}", "created_at": "2026-11-01T00:00:00+00:00"}


class MaintenanceReminderTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.p = Project(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def prompt(self, sid):
        payload = {"prompt": "hi", "hook_event_name": "UserPromptSubmit"}
        if sid:
            payload["session_id"] = sid
        rc, out, err = run("user-prompt-inject.py", self.p.root, payload)
        self.assertEqual(rc, 0, err)
        return reminder_text(out)

    def test_same_version_once_change_reshows_only_that_item(self):
        self.p.activity(pending_maintenance=[pm(1), pm(2, severity="warn")])
        first = self.prompt("A")
        self.assertIn("PM-1", first)
        self.assertIn("PM-2", first)
        self.assertEqual(self.prompt("A"), "", "同一会话不重播")
        self.assertEqual(self.prompt("B"), "", "新会话、同版本不重播")
        self.p.activity(pending_maintenance=[pm(1, details="changed"), pm(2, severity="warn")])
        changed = self.prompt("C")
        self.assertIn("PM-1", changed)
        self.assertNotIn("PM-2 ", changed)
        self.assertIn("另有 1 条未变", changed)

    def test_interleaved_sessions_keep_their_own_first_prompt(self):
        self.p.activity(pending_maintenance=[])
        self.assertEqual(self.prompt("A"), "")
        self.assertEqual(self.prompt("B"), "")
        self.p.activity(pending_maintenance=[pm(1)])
        self.assertEqual(self.prompt("A"), "", "A 的第二问不是首问，B 的首问不能把 A 挤掉")
        self.assertIn("PM-1", self.prompt("C"))

    def test_upgrade_folds_legacy_single_marker_into_list(self):
        self.p.activity(last_prompt_injected_session_id="sid:A", pending_maintenance=[])
        self.assertEqual(self.prompt("B"), "")
        self.p.activity(pending_maintenance=[pm(1)])
        self.assertEqual(self.prompt("A"), "", "升级前记过首问的 A，B 先来也不能让 A 的下一问被当成首问")
        self.assertIn("PM-1", self.prompt("C"))

    def test_only_shown_items_are_claimed(self):
        self.p.activity(pending_maintenance=[pm(i) for i in range(6)])
        first = self.prompt("A")
        self.assertEqual(sum(f"PM-{i} " in first for i in range(6)), 5)
        self.assertIn("另 1 条新的下次再提醒", first)
        second = self.prompt("B")
        self.assertEqual(sum(f"PM-{i} " in second for i in range(6)), 1, "被条数上限挡住的那条下次再播")
        self.assertEqual(self.prompt("C"), "")

    def test_length_cap_defers_instead_of_claiming(self):
        self.p.activity(pending_maintenance=[pm(1, details="长" * 1200), pm(2, details="长" * 1200)])
        first = self.prompt("A")
        self.assertIn("PM-1", first)
        self.assertNotIn("PM-2", first)
        self.assertIn("PM-2", self.prompt("B"))

    def test_closed_items_leave_the_record(self):
        self.p.activity(pending_maintenance=[pm(1), pm(2)])
        self.prompt("A")
        self.p.activity(pending_maintenance=[pm(1), dict(pm(2), status="closed")])
        self.assertEqual(self.prompt("B"), "")
        self.assertEqual(len(self.p.read_activity()["maintenance_notified"]), 1)

    def test_dormant_is_silent_and_does_not_claim(self):
        self.p.activity(dormant=True, pending_maintenance=[pm(1)])
        self.assertEqual(self.prompt("A"), "")
        self.p.activity(dormant=False)
        self.assertIn("PM-1", self.prompt("A"), "dormant 时没有认领，恢复后同一会话号照样能播")

    def test_without_session_id_falls_back_to_counter(self):
        self.p.activity(session_counter=7, pending_maintenance=[pm(1)])
        self.assertIn("PM-1", self.prompt(None))
        self.assertEqual(self.p.read_activity()["last_prompt_injected_session_id"], "7")


RETIRED = ("problem_threshold", "activity_threshold", "completed_delta", "cadence_timeout")


def days_ago(n):
    return (datetime.now(timezone.utc) - timedelta(days=n)).isoformat(timespec="seconds")


def card(i, kind, status="open", closed_at=None):
    return dict(pm(i, kind=kind), dedup_key=kind, status=status, closed_at=closed_at)


class ReminderKindTests(unittest.TestCase):
    """维护提醒只剩两种；旧的四种不再产出，存量 open 卡转 retired。"""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.p = Project(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def hook(self, script, payload=None, args=("--harness", "cc")):
        rc, out, err = run(script, self.p.root, payload, args=args, env_extra={"WORKFRAME_SELFITER_OFF": "1"})
        self.assertEqual(rc, 0, err)
        self.assertNotIn("Traceback", err)
        return out

    def open_kinds(self):
        items = [i for i in self.p.read_activity()["pending_maintenance"] if i["status"] == "open"]
        for i in items:
            self.assertEqual(i["kind"], i["dedup_key"])
        return sorted(i["kind"] for i in items)

    def stop(self):
        self.hook("check-iteration-trigger.py", {"session_id": "s"})
        return self.open_kinds()

    def backlog(self, lines=100):
        self.p.write(".workframe/agent-memory/dev/notes.md", "- 2026-11-01: x\n" + "  y\n" * lines)

    def stale(self, ts):
        self.p.write(".workframe/state/stale-modules.yaml",
                     f"submodules:\n  b/s:\n    first_marked_at: {ts}\n    last_marked_at: {ts}\n")

    def all_old_conditions(self):
        """从未迭代、问题分 ≥5、活动分 ≥10、完成任务 ≥10：旧的四种全部够条件。"""
        rows = [{"ts": days_ago(1), "type": "user_correction", "summary": f"c{i}"} for i in range(4)]
        rows += [{"ts": days_ago(1), "type": "skill_used", "skill": "x", "session_id": f"s{s}"}
                 for s in range(6) for _ in range(20)]
        self.p.write(".workframe/state/events.jsonl", "".join(json.dumps(r) + "\n" for r in rows))
        today = date.today().isoformat()
        self.p.board([{"id": f"T{i}", "status": "completed", "completed_at": f'"{today}"'} for i in range(15)])

    def test_stop_never_produces_retired_kinds(self):
        self.p.activity(pending_maintenance=[])
        self.all_old_conditions()
        self.assertEqual(self.stop(), [])
        self.backlog()
        self.stale(days_ago(0))
        self.assertEqual(self.stop(), ["close_check_due", "memory_backlog"])

    def test_backlog_counts_characters_and_starts_from_the_oldest_entry(self):
        """从没提升过的项目按最早一条的日期算；行数不多而字数够了也提醒。"""
        def notes(text):
            self.p.activity(pending_maintenance=[])
            self.p.write(".workframe/agent-memory/dev/notes.md", text)
            return self.stop()
        day = lambda n: (date.today() - timedelta(days=n)).isoformat()
        body = "  y\n" * 6
        self.assertEqual(notes(f"- {day(30)}: x\n" + body), [])
        self.assertEqual(notes(f"- {day(31)}: x\n" + body), ["memory_backlog"])
        self.assertEqual(notes(f"- {day(31)}: x\n  → 不变：已由 z 覆盖\n" + body), [], "已处置的不算")
        self.assertEqual(notes("### 没有日期\n" + body), [])
        head = f"- {day(1)}: "
        self.assertEqual(notes(head + "字" * (2999 - len(head))), [])
        self.assertEqual(notes(head + "字" * (3000 - len(head))), ["memory_backlog"])
        self.p.write(".workframe/state/events.jsonl", json.dumps({"ts": days_ago(1), "type": "memory_promoted"}) + "\n")
        self.assertEqual(notes(f"- {day(90)}: x\n" + body), [], "提升过的项目仍从上次提升算起")

    def test_stop_always_hands_over_to_background_queue(self):
        """没有任何提醒可出时，Stop 仍要把会话交给后台排队（Codex 门只在这里排）。"""
        self.p.activity(pending_maintenance=[])
        script = SCRIPTS / "check-iteration-trigger.py"
        prog = ("import runpy, sys, types; m = types.ModuleType('_selfiter_hooks'); "
                "m.on_hook = lambda *a, **k: sys.stderr.write('HANDED ' + a[1] + ' ' + a[2] + chr(10)); "
                f"sys.modules['_selfiter_hooks'] = m; sys.path.insert(0, {str(SCRIPTS)!r}); "
                "sys.argv = ['check-iteration-trigger.py', '--harness', 'codex']; "
                f"runpy.run_path({str(script)!r}, run_name='__main__')")
        env = dict(os.environ, CLAUDE_PROJECT_DIR=str(self.p.root), PYTHONUTF8="1")
        payload = json.dumps({"session_id": "s", "cwd": str(self.p.root)}).encode("utf-8")
        r = subprocess.run([sys.executable, "-c", prog], input=payload, capture_output=True,
                           cwd=str(self.p.root), env=env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stderr.decode("utf-8").count("HANDED codex Stop"), 1, r.stderr)

    def test_each_kept_kind_is_dismissed_on_its_own(self):
        self.backlog()
        self.stale(days_ago(0))
        closed = days_ago(1)
        self.p.activity(pending_maintenance=[card(1, "memory_backlog", "closed", closed)])
        self.assertEqual(self.stop(), ["close_check_due"], "驳回积压不压收口")
        self.p.activity(pending_maintenance=[card(1, "close_check_due", "closed", closed)])
        self.stale(days_ago(3))
        self.assertEqual(self.stop(), ["memory_backlog"], "驳回收口：旧标记静音，积压照报")
        self.p.activity(pending_maintenance=[card(1, "close_check_due", "closed", closed)])
        self.stale(days_ago(0))
        self.assertEqual(self.stop(), ["close_check_due", "memory_backlog"], "驳回之后的新标记照报")

    def test_session_start_retires_old_open_cards_only(self):
        kept = [card(5, "memory_backlog"), card(6, "close_check_due"),
                card(7, "problem_threshold", "closed", days_ago(1))]
        self.p.activity(pending_maintenance=[card(i, k) for i, k in enumerate(RETIRED)] + kept)
        self.hook("session-start-prep.py", {"session_id": "s1", "source": "startup"})
        items = self.p.read_activity()["pending_maintenance"]
        self.assertEqual({i["kind"]: i["status"] for i in items[:4]}, {k: "retired" for k in RETIRED})
        self.assertTrue(all(i["closed_at"] for i in items[:4]))
        self.assertEqual(items[4:], kept, "保留的两种与驳回过的卡一字不动")
        self.hook("session-start-prep.py", {"session_id": "s2", "source": "startup"})
        self.assertEqual(self.p.read_activity()["pending_maintenance"], items, "再开一次会话结果不变")

    def test_old_completed_count_alone_produces_nothing(self):
        self.p.activity(pending_maintenance=[])
        today = date.today().isoformat()
        self.p.write("projects/proposals/applied/PROP-20260101-001.yaml", f"applied_at: {today}\n")
        self.p.board([{"id": f"T{i}", "status": "completed", "completed_at": f'"{today}"'} for i in range(15)])
        self.assertEqual(self.stop(), [])

    def test_retired_cards_expire_like_closed_ones(self):
        over, under = 7 + 1 / 24, 7 - 1 / 24     # 保留期 7 天，两侧各差一小时
        self.p.activity(pending_maintenance=[
            card(1, "problem_threshold", "retired", days_ago(over)), card(2, "cadence_timeout", "closed", days_ago(over)),
            card(3, "problem_threshold", "retired", days_ago(under)), card(4, "memory_backlog", "closed", days_ago(under))])
        self.hook("session-start-prep.py", {"session_id": "s1", "source": "startup"})
        self.assertEqual([i["id"] for i in self.p.read_activity()["pending_maintenance"]], ["PM-3", "PM-4"])

    def test_naive_closed_at_does_not_block_retirement(self):
        self.p.activity(pending_maintenance=[card(1, "memory_backlog", "closed", "2020-01-01T09:00:00"),
                                             card(2, "problem_threshold")])
        self.hook("session-start-prep.py", {"session_id": "s1", "source": "startup"})
        self.assertEqual([(i["id"], i["status"]) for i in self.p.read_activity()["pending_maintenance"]],
                         [("PM-2", "retired")])

    def test_retired_kinds_cannot_be_turned_into_a_dismissal(self):
        """旧版那扇门把 closed 的问题卡读成驳回、压住证据：两条关单路径都不许写出来。"""
        cards = [card(1, "problem_threshold"), card(2, "close_check_due"), card(3, "memory_backlog")]
        self.p.activity(pending_maintenance=cards)
        rc, out, err = run("maintenance_workorder.py", self.p.root, args=("--close-pm", "PM-1", "PM-2"))
        self.assertEqual(rc, 0, err)
        self.assertIn("未命中（不存在 / 已是 closed / 已退役）：PM-1", out)
        self.p.write("logs/maintenance-commit.json", json.dumps({"close_pm": ["PM-1", "PM-3"]}))
        rc, out, err = run("maintenance_workorder.py", self.p.root, args=("--commit",))
        self.assertEqual(rc, 0, out + err)
        self.assertEqual([i["status"] for i in self.p.read_activity()["pending_maintenance"]], ["open", "closed", "closed"])
        rows = [json.loads(line) for line in (self.p.state / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([r["pm_id"] for r in rows if r.get("type") == "pending_maintenance_dismissed"], ["PM-2", "PM-3"])

    def test_retired_kinds_never_reach_prompt_or_workorder(self):
        # 仍是 open（旧版那扇门写的、本版还没开过场）也不显示
        self.p.activity(pending_maintenance=[card(i, k) for i, k in enumerate(RETIRED)]
                        + [card(8, "problem_threshold", "retired", days_ago(1)), card(9, "memory_backlog")])
        text = reminder_text(self.hook("user-prompt-inject.py", {
            "prompt": "hi", "session_id": "A", "hook_event_name": "UserPromptSubmit"}))
        self.assertIn("PM-9", text)
        self.hook("maintenance_workorder.py", {"trigger": "maintenance"}, args=())
        order = (self.p.state / "maintenance-workorder.md").read_text(encoding="utf-8")
        self.assertIn("PM-9", order)
        for out in (text, order):
            for kind in RETIRED:
                self.assertNotIn(kind, out)


class SessionEndOrderTests(unittest.TestCase):
    """两门的 SessionEnd 都有时限，到点进程被杀。这里让指定的一步直接结束进程来代表「到点」。"""

    def killed_at(self, p, door, patch, payload):
        """在 `patch` 点上的那一步把进程结束掉，返回写下的事件 `(type, harness)`。"""
        prog = (f"import sys, os, runpy; sys.path.insert(0, {str(SCRIPTS)!r}); {patch}; "
                f"sys.argv = ['session-end-flush.py', '--harness', {door!r}]; "
                f"runpy.run_path({str(SCRIPTS / 'session-end-flush.py')!r}, run_name='__main__')")
        env = dict(os.environ, PYTHONUTF8="1", CLAUDE_PROJECT_DIR=str(p.root))
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        r = subprocess.run([sys.executable, "-c", prog], input=json.dumps(payload).encode("utf-8"),
                           capture_output=True, cwd=str(p.root), env=env, timeout=60)
        self.stderr = r.stderr.decode("utf-8", "replace")
        self.assertEqual(r.returncode, 9, self.stderr)   # 确实死在那一步
        events = p.state / "events.jsonl"
        rows = [json.loads(x) for x in events.read_text(encoding="utf-8").splitlines()] if events.exists() else []
        return [(x["type"], x["harness"]) for x in rows]

    def test_cheap_and_important_steps_land_before_a_kill(self):
        with tempfile.TemporaryDirectory() as td:
            p = Project(td)
            mine = p.state / "current-session-codex-s1.json"
            other = p.state / "current-session-codex-s2.json"
            for m in (mine, other):
                m.write_text("{}", encoding="utf-8", newline="")
            # 回滚索引是结束事件与标记之后的第一步；它要写文件，在那里结束进程
            got = self.killed_at(p, "codex", "import _state_io; _state_io.atomic_write = lambda *a: os._exit(9)",
                                 {"session_id": "s1", "cwd": str(p.root), "reason": "other"})
            self.assertEqual(got, [("session_ended", "codex")])
            self.assertFalse(mine.exists())
            self.assertTrue(other.exists())

    def test_claude_code_queues_before_anything_else(self):
        with tempfile.TemporaryDirectory() as td:
            p = Project(td)
            got = self.killed_at(p, "cc", "import _selfiter_hooks; _selfiter_hooks.on_hook = lambda *a: os._exit(9)",
                                 {"session_id": "s1", "reason": "clear"})
            self.assertEqual(got, [])

    def test_marker_is_taken_from_the_payload_only(self):
        with tempfile.TemporaryDirectory() as td:
            p = Project(td)
            outer = p.state / "current-session-codex-outer.json"
            outer.write_text("{}", encoding="utf-8", newline="")
            rc, _, err = run("session-end-flush.py", p.root, {"cwd": str(p.root), "reason": "other"},
                             args=("--harness", "codex"), env_extra={"CLAUDE_CODE_SESSION_ID": "outer"})
            self.assertEqual(rc, 0, err)
            self.assertTrue(outer.exists())

    def test_busy_event_lock_spills_at_once(self):
        with tempfile.TemporaryDirectory() as td:
            p = Project(td)
            mine = p.state / "current-session-codex-s1.json"
            mine.write_text("{}", encoding="utf-8", newline="")
            # 断言的是锁收到的等待上限，不是墙钟：慢机器上进程启动就要好几秒
            patch = ("import _state_io; _state_io.atomic_write = lambda *a: os._exit(9); "
                     "_state_io.FileLock.__init__ = lambda self, path, timeout=5, _o=_state_io.FileLock.__init__: "
                     "(sys.__stderr__.write('lock-wait=%s;' % timeout), sys.__stderr__.flush(), _o(self, path, timeout))[2]")
            with _state_io.FileLock(p.state / "events.jsonl.lock"):
                got = self.killed_at(p, "codex", patch, {"session_id": "s1", "cwd": str(p.root), "reason": "other"})
            self.assertEqual(got, [])
            spills = list(p.state.glob("events.*.spill.jsonl"))
            rows = [json.loads(x) for f in spills for x in f.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([x["type"] for x in rows], ["session_ended"])
            self.assertFalse(mine.exists())
            waits = [float(x) for x in re.findall(r"lock-wait=([0-9.]+);", self.stderr)]
            self.assertEqual(len(waits), 1, self.stderr)
            self.assertLessEqual(waits[0], 0.5)

    def test_claude_code_side_leaves_codex_markers_alone_and_refreshes_the_board(self):
        with tempfile.TemporaryDirectory() as td:
            p = Project(td)
            p.board([{"id": "T-1", "status": "pending"}, {"id": "T-2", "status": "completed"}])
            marker = p.state / "current-session-codex-s1.json"
            marker.write_text("{}", encoding="utf-8", newline="")
            rc, _, err = run("session-end-flush.py", p.root, {"session_id": "s1", "reason": "clear"},
                             env_extra={"WORKFRAME_SELFITER_OFF": "1"})
            self.assertEqual(rc, 0, err)
            rows = [json.loads(x) for x in (p.state / "events.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual([(x["type"], x.get("status")) for x in rows],
                             [("session_ended", None), ("summary_recomputed", "ok"), ("skill_metrics_recomputed", "ok")])
            self.assertIn("  total: 2", (p.root / "projects" / "board.yaml").read_text(encoding="utf-8"))
            self.assertTrue((p.state / "skill-metrics.yaml").is_file())
            self.assertTrue(marker.exists())

    def test_codex_manifest_asks_for_the_longest_session_end(self):
        data = json.loads((SCRIPTS.parent / "hooks" / "hooks.codex.json").read_text(encoding="utf-8"))
        hooks = [(event, h) for event, groups in data["hooks"].items() for g in groups for h in g["hooks"]]
        self.assertEqual([h.get("timeout") for event, h in hooks if event == "SessionEnd"], [3])
        self.assertEqual({h.get("timeout") for event, h in hooks if event != "SessionEnd"}, {None})

    def test_failed_swap_keeps_the_board_and_leaves_no_temp_file(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "board.yaml"
            target.write_text("old", encoding="utf-8", newline="")
            with mock.patch.object(_state_io.os, "replace", side_effect=PermissionError("busy")):
                with self.assertRaises(PermissionError):
                    _state_io.atomic_write(target, "new")
            self.assertEqual(target.read_text(encoding="utf-8"), "old")
            self.assertEqual([x.name for x in Path(td).iterdir()], ["board.yaml"])

    def test_board_is_replaced_not_rewritten_in_place(self):
        with tempfile.TemporaryDirectory() as td:
            p = Project(td)
            p.board([{"id": "T-1", "status": "pending"}])
            board = p.root / "projects" / "board.yaml"
            before = board.read_bytes()
            prog = (f"import sys, os; sys.path.insert(0, {str(SCRIPTS)!r})\n"
                    "calls = []\n"
                    "def boom(a, b): calls.append(1); raise PermissionError(13, 'busy', a, None, b)\n"
                    "os.replace = boom\n"
                    "import recompute_board_summary as r\n"
                    "got = r.recompute_board_summary()\n"
                    "print(got['status'], got['reason'], len(calls))\n")
            r = subprocess.run([sys.executable, "-c", prog], capture_output=True, cwd=str(p.root),
                               env=dict(os.environ, CLAUDE_PROJECT_DIR=str(p.root), PYTHONUTF8="1"), timeout=60)
            # 只换一次：占着看板的一方可能正在写，等它写完再换就把它写的盖掉了
            self.assertEqual(r.stdout.decode().strip(), "skipped board_busy 1", r.stderr.decode("utf-8", "replace"))
            self.assertEqual(board.read_bytes(), before)
            self.assertEqual([x.name for x in board.parent.iterdir()], ["board.yaml"])

    def test_unwritable_temp_file_is_not_reported_as_a_busy_board(self):
        with tempfile.TemporaryDirectory() as td:
            p = Project(td)
            p.board([{"id": "T-1", "status": "pending"}])
            prog = (f"import sys; from pathlib import Path; sys.path.insert(0, {str(SCRIPTS)!r})\n"
                    "import recompute_board_summary as r\n"
                    "def boom(*a, **k): raise PermissionError(13, 'read-only', 'board.yaml.tmp')\n"
                    "Path.write_text = boom\n"
                    "try: r.recompute_board_summary()\n"
                    "except PermissionError as e: print(e.strerror)\n")
            r = subprocess.run([sys.executable, "-c", prog], capture_output=True, cwd=str(p.root),
                               env=dict(os.environ, CLAUDE_PROJECT_DIR=str(p.root), PYTHONUTF8="1"), timeout=60)
            self.assertEqual(r.stdout.decode().strip(), "read-only", r.stderr.decode("utf-8", "replace"))

    def test_metrics_and_rollback_index_are_replaced_not_rewritten_in_place(self):
        with tempfile.TemporaryDirectory() as td:
            p = Project(td)
            metrics, rollback = p.state / "skill-metrics.yaml", p.state / "rollback-index.json"
            metrics.write_text("old: metrics\n", encoding="utf-8", newline="")
            rollback.write_text('{"entries": [{"proposal_id": "P", "applied_at": "2026-01-01"}]}', encoding="utf-8",
                                newline="")
            before = metrics.read_bytes(), rollback.read_bytes()
            prog = (f"import sys, os, runpy; sys.path.insert(0, {str(SCRIPTS)!r})\n"
                    "def boom(*a): raise PermissionError('busy at swap')\n"
                    "os.replace = boom\n"
                    "sys.argv = ['session-end-flush.py', '--harness', 'codex']\n"
                    f"runpy.run_path({str(SCRIPTS / 'session-end-flush.py')!r}, run_name='__main__')\n")
            r = subprocess.run([sys.executable, "-c", prog], capture_output=True, cwd=str(p.root),
                               input=json.dumps({"session_id": "s1", "cwd": str(p.root)}).encode("utf-8"),
                               env=dict(os.environ, CLAUDE_PROJECT_DIR=str(p.root), PYTHONUTF8="1"), timeout=60)
            err = r.stderr.decode("utf-8", "replace")
            self.assertEqual(r.returncode, 0, err)
            self.assertIn("rollback-index GC failed (non-fatal): PermissionError: busy at swap", err)
            rows = [json.loads(x) for x in (p.state / "events.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual([x["reason"] for x in rows if x["type"] == "skill_metrics_recomputed"],
                             ["exception during recompute: PermissionError: busy at swap"])
            self.assertEqual((metrics.read_bytes(), rollback.read_bytes()), before)
            self.assertEqual([x.name for x in p.state.iterdir() if ".tmp." in x.name], [])

    def test_short_busy_swap_is_retried(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "board.yaml"
            real, calls = os.replace, []

            def busy_once(a, b):
                calls.append(1)
                if len(calls) == 1:
                    raise PermissionError("busy")
                return real(a, b)
            with mock.patch.object(_state_io.os, "replace", side_effect=busy_once):
                _state_io.atomic_write(target, "new")
            self.assertEqual(target.read_text(encoding="utf-8"), "new")
            self.assertEqual(len(calls), 2)

    def test_board_changed_by_someone_else_during_recompute_is_left_alone(self):
        with tempfile.TemporaryDirectory() as td:
            p = Project(td)
            p.board([{"id": "T-1", "status": "pending"}])
            board = p.root / "projects" / "board.yaml"
            prog = (f"import sys; sys.path.insert(0, {str(SCRIPTS)!r})\n"
                    "import recompute_board_summary as r\n"
                    "real = r.rewrite_summary_block\n"
                    "def slow(*a):\n"
                    "    with open(r.BOARD_FILE, 'ab') as f: f.write(b'  - id: NEW\\n    status: pending\\n')\n"
                    "    return real(*a)\n"
                    "r.rewrite_summary_block = slow\n"
                    "print(r.recompute_board_summary()['reason'])\n")
            r = subprocess.run([sys.executable, "-c", prog], capture_output=True, cwd=str(p.root),
                               env=dict(os.environ, CLAUDE_PROJECT_DIR=str(p.root), PYTHONUTF8="1"), timeout=60)
            self.assertEqual(r.stdout.decode().strip(), "board_changed_meanwhile", r.stderr.decode("utf-8", "replace"))
            self.assertIn("id: NEW", board.read_text(encoding="utf-8"))


class CommandDoorTests(unittest.TestCase):
    MARKS = ("CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID", "CODEX_SESSION_ID")

    def harness_of(self, project, env_marks, args=()):
        env = {k: v for k, v in os.environ.items() if k not in self.MARKS}
        env.update(CLAUDE_PROJECT_DIR=str(project), PYTHONUTF8="1", **env_marks)
        r = subprocess.run([sys.executable, str(SCRIPTS / "workframe_event.py"), "skill-used", "--skill", "x",
                            "--role", "main", "--dry-run", *args], capture_output=True, cwd=str(project),
                           env=env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))
        row = json.loads(r.stdout.decode("utf-8"))
        return row["harness"], row.get("session_id")

    def test_single_marker_names_the_door_and_anything_else_is_unknown(self):
        cc, thread, sess = ({"CLAUDE_CODE_SESSION_ID": "c1"}, {"CODEX_THREAD_ID": "t1"}, {"CODEX_SESSION_ID": "x1"})
        with tempfile.TemporaryDirectory() as td:
            root = Project(td).root
            self.assertEqual(self.harness_of(root, cc), ("cc", "c1"))
            self.assertEqual(self.harness_of(root, thread), ("codex", None))
            self.assertEqual(self.harness_of(root, sess), ("codex", None))
            self.assertEqual(self.harness_of(root, {**cc, **thread}), ("unknown", None))
            self.assertEqual(self.harness_of(root, {}), ("unknown", None))

    def test_explicit_argument_wins_and_a_bad_one_stays_unknown(self):
        with tempfile.TemporaryDirectory() as td:
            root = Project(td).root
            env = {"CLAUDE_CODE_SESSION_ID": "c1"}
            self.assertEqual(self.harness_of(root, env, ("--harness", "codex")), ("codex", None))
            self.assertEqual(self.harness_of(root, env, ("--harness", "bogus")), ("unknown", "c1"))

    def test_codex_door_writes_to_the_project_it_stands_in(self):
        with tempfile.TemporaryDirectory() as td:
            a, b = Project(Path(td) / "a"), Project(Path(td) / "b")
            (b.state / "current-session-codex-bsid.json").write_text("{}", encoding="utf-8", newline="")
            env = {k: v for k, v in os.environ.items() if k not in self.MARKS}
            for args in ((), ("--harness", "codex")):
                r = subprocess.run([sys.executable, str(SCRIPTS / "workframe_event.py"), "skill-used", "--skill", "x",
                                    "--role", "main", *args], capture_output=True, cwd=str(a.root), timeout=60,
                                   env=dict(env, CLAUDE_PROJECT_DIR=str(b.root), CODEX_THREAD_ID="t1", PYTHONUTF8="1"))
                self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))
            rows = [json.loads(x) for x in (a.state / "events.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual([(x["harness"], x.get("session_id")) for x in rows], [("codex", None)] * 2)
            self.assertFalse((b.state / "events.jsonl").exists())
            # 站在没有根标记的目录里：也不落到外层项目
            bare = Path(td) / "bare"
            bare.mkdir()
            prog = (f"import sys; sys.path.insert(0, {str(SCRIPTS)!r})\n"
                    "import workframe_event as w\n"
                    "w.find_root = lambda *a: None\n"
                    "sys.argv = ['workframe-event']\n"
                    "w.main(['skill-used', '--skill', 'x', '--role', 'main'])\n")
            r = subprocess.run([sys.executable, "-c", prog], capture_output=True, cwd=str(bare), timeout=60,
                               env=dict(env, CLAUDE_PROJECT_DIR=str(b.root), CODEX_THREAD_ID="t1", PYTHONUTF8="1"))
            self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))
            self.assertFalse((b.state / "events.jsonl").exists())
            self.assertTrue((_state_io.state_dir_of(bare) / "events.jsonl").is_file())

    def test_marker_file_needs_the_explicit_argument(self):
        with tempfile.TemporaryDirectory() as td:
            p = Project(td)
            (p.state / "current-session-codex-dead.json").write_text("{}", encoding="utf-8", newline="")
            env = {"CODEX_THREAD_ID": "t1"}
            self.assertEqual(self.harness_of(p.root, env), ("codex", None))
            self.assertEqual(self.harness_of(p.root, env, ("--harness", "codex")), ("codex", "dead"))

    def test_hooks_still_do_not_guess(self):
        code = (f"import sys; sys.path.insert(0, {str(SCRIPTS)!r}); sys.argv = ['x']; "
                "import _harness; print(_harness.harness())")
        env = {k: v for k, v in os.environ.items() if k not in self.MARKS}
        r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                           env=dict(env, CLAUDE_CODE_SESSION_ID="c1"), timeout=60)
        self.assertEqual(r.stdout.decode().strip(), "unknown")


class SkillIdentityTests(unittest.TestCase):
    def test_identity_rules(self):
        import _skill_identity as si
        core = frozenset({"task-management", "code-review"})
        res = lambda s, pl=None, proj=frozenset(): si.identity(s, pl, core_names=core, project_names=proj,
                                                               own_plugin="core")
        self.assertEqual(res("task-management"), "core:task-management")
        self.assertEqual(res("core:task-management"), "core:task-management")
        self.assertEqual(res("task-management", "core"), "core:task-management")
        self.assertEqual(res("local-tool"), "local-tool")
        self.assertEqual(res("x", "other"), "other:x")
        self.assertEqual(res("code-review", proj=frozenset({"code-review"})), "code-review")
        self.assertIsNone(res(""))
        self.assertIsNone(res(None))

    def test_metrics_merge_and_separate_columns(self):
        with tempfile.TemporaryDirectory() as td:
            p = Project(td)
            p.write(".agents/skills/local-tool/SKILL.md", "---\nname: local-tool\n---\n")
            from datetime import datetime, timezone
            now = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(timespec="seconds")
            rows = [{"ts": now, "type": "skill_used", "skill": "task-management", "success": True, "role": "main"},
                    {"ts": now, "type": "skill_used", "skill": "core:task-management", "success": False, "role": "dev"},
                    {"ts": now, "type": "skill_invoked", "skill": "task-management", "plugin": "core"},
                    {"ts": now, "type": "skill_invoked", "skill": "local-tool"}]
            p.write(".workframe/state/events.jsonl", "".join(json.dumps(r) + "\n" for r in rows))
            rc, out, err = run("recompute_skill_metrics.py", p.root, args=("--no-event", "--window-days", "36500"))
            self.assertEqual(rc, 0, err)
            y = (p.state / "skill-metrics.yaml").read_text(encoding="utf-8")
            self.assertRegex(y, r"core:task-management:\s*\n\s*invocations: 2\s*\n\s*successes: 1\s*\n"
                                r"\s*last_used: \"" + now[:10] + r"\"\s*\n\s*invoked: 1")
            self.assertNotRegex(y, r"(?m)^  task-management:")
            self.assertRegex(y, r"local-tool:\s*\n\s*invocations: 0\s*\n\s*successes: 0\s*\n"
                                r"\s*last_used: null\s*\n\s*invoked: 1")
            # stats 用同一套归一：自报与调起分两张表，归一后的名字一致
            q = subprocess.run([sys.executable, str(SCRIPTS / "workframe_query.py"), "stats", "--project", str(p.root),
                                "--weeks", "[]", "--days", "90"], capture_output=True, timeout=60,
                               env=dict(os.environ, PYTHONUTF8="1"))
            events = json.loads(q.stdout.decode("utf-8"))["data"]["events"]
            self.assertEqual(events.get("state"), "ok", events)
            self.assertEqual(events["skill_usage"], [{"skill": "core:task-management", "count": 2}])
            self.assertEqual(events["skill_invoked"], [{"skill": "core:task-management", "count": 1},
                                                       {"skill": "local-tool", "count": 1}])

    def test_writer_dedup_uses_normalized_identity(self):
        for _attempt in range(3):   # 去重窗口是墙钟分钟：四次调用跨了分钟边界就重来一次
            with tempfile.TemporaryDirectory() as td:
                p = Project(td)
                for tool_skill in ("audit", "core:audit", "other:audit", "audit"):
                    rc, _, err = run("log-skill-invoked.py", p.root,
                                     {"hook_event_name": "PostToolUse", "session_id": "s1",
                                      "tool_input": {"skill": tool_skill}})
                    self.assertEqual(rc, 0, err)
                rows = [json.loads(l) for l in (p.state / "events.jsonl").read_text(encoding="utf-8").splitlines()
                        if '"skill_invoked"' in l]
                if len({r["ts"][:16] for r in rows}) == 1:
                    break
        self.assertEqual(sorted(str(r.get("plugin")) for r in rows), ["None", "other"],
                         "裸名 audit 与 core:audit 同分钟是同一个技能只记一条；other:audit 另记")


class DoctorProfileConflictTests(unittest.TestCase):
    def check(self, state_fields):
        import workframe_doctor as doc
        with tempfile.TemporaryDirectory() as td:
            p = Project(td, {"project_type": "product-work", "role_profile": "solo-pm"})
            (p.state / "activity-state.json").write_text(json.dumps(dict({"session_counter": 1}, **state_fields)),
                                                         encoding="utf-8", newline="")
            return doc.check_config({"paths": doc.Paths(td)})

    def test_conflict_reported_only_when_key_really_present(self):
        out = self.check({"dormant_profile": "low-frequency"})
        warns = [m for lv, m in out if lv == "warn" and "活动档位两处不一致" in m]
        self.assertEqual(len(warns), 1, out)
        self.assertIn("'normal'", warns[0])
        self.assertIn("'low-frequency'", warns[0])
        self.assertFalse([m for lv, m in self.check({}) if "活动档位" in m], "运行态没有该键时不报")
        self.assertFalse([m for lv, m in self.check({"dormant_profile": "normal"}) if "活动档位" in m])


class DoctorProducerTests(unittest.TestCase):
    def check(self, events=()):
        import workframe_doctor as doc
        return doc.check_promise_producer({"events": list(events)})

    def levels(self, spec):
        import workframe_doctor as doc
        with tempfile.TemporaryDirectory() as td:
            schema = Path(td) / "event-schema.json"
            schema.write_text(json.dumps({"events": {"zz_no_such_producer": spec}}), encoding="utf-8", newline="")
            with mock.patch.object(doc, "EVENT_SCHEMA_FILE", schema):
                return [lv for lv, _ in self.check()]

    def test_shipped_registry_reports_nothing_broken_without_history(self):
        out = self.check()
        self.assertEqual([m for lv, m in out if lv == "error"], [], "没有历史事件的项目不该报 error")
        self.assertFalse([m for _, m in out if "self_signoff" in m], "停写的事件不该被说成承诺还在")

    def test_stopped_event_with_history_is_neither_active_nor_unregistered(self):
        out = self.check([{"type": "modules_index_rebuilt"}])
        self.assertIn("normal 0 类", out[0][1])
        self.assertFalse([m for _, m in out if "modules_index_rebuilt" in m], out)

    def test_event_without_producer_is_still_an_error_unless_both_doors_say_none(self):
        def spec(cc, codex):
            return {"reliability": "hook_deterministic", "availability": {"cc": cc, "codex": codex}}
        for cc, codex in (("full", "full"), ("full", "none"), ("none", "full"), ("unknown", "unknown"),
                          ("partial", "partial")):
            self.assertIn("error", self.levels(spec(cc, codex)), (cc, codex))
        self.assertIn("error", self.levels({"reliability": "hook_deterministic"}), "没有声明按活跃算")
        self.assertIn("error", self.levels({"reliability": "hook_deterministic", "availability": {}}))
        self.assertNotIn("error", self.levels(spec("none", "none")))
        self.assertNotIn("error", self.levels(dict(spec("full", "full"), reliability="removed_v0_2_1")))


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    unittest.main()
