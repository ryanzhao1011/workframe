"""技能统计身份归一——`skill_used` / `skill_invoked` 两类事件按「哪个技能」聚合时的**唯一判定式**。

为什么需要：同一个 core 技能在事件流里有三种写法——`skill_used` 的裸名（`task-management`）、
`skill_used` 的带前缀名（`core:task-management`）、`skill_invoked` 的裸名加 `plugin: core` 字段。
按原始字符串聚合，同一个技能会被拆成两三行。

规则（`identity`）：
  1. 名字已带 `<插件>:` 前缀 → 原样；
  2. 事件带 `plugin` 字段 → `<plugin>:<名>`；
  3. 裸名、本插件 `skills/<名>/SKILL.md` 存在、且项目 skills 目录**没有**同名技能 → `<本插件>:<名>`；
  4. 其余（项目技能、外部插件技能、与项目技能同名）→ 保持裸名。

**只在聚合时归一，不改写事件流**——原始值留在事件里，规则改了重算即可。

**已知局限**：用户级 skills 目录（或别的插件）里与 core 同名的技能自报裸名时，规则 3 会把它
归给 core。判这一格要知道「这一次实际加载的是哪一份」，事件里没有这个信息；有意不另处理。

使用方：`recompute_skill_metrics`（skill-metrics.yaml）、`_query_stats`（`workframe-query stats`）、
`log-skill-invoked`（写入端近时去重：同分钟同身份只记一条）。doctor 的事件趋势只数总次数、不按技能分，
不经本模块。新增按技能聚合的读者一律调本模块。

被同目录 import：模块 import 无副作用（不碰 stdout、不读环境）。
"""

from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_CORE_NAMES = {}


def plugin_name():
    """本插件名（前缀）；读插件清单的那份实现在 `_role_memory.plugin_name`，读不到用 `core`。"""
    try:
        import _role_memory
        return _role_memory.plugin_name()
    except Exception:
        return "core"


def core_skill_names():
    """本插件自带的技能名集合（有 `SKILL.md` 的子目录）；读不到返回空集（规则 3 随之不生效）。"""
    if "v" not in _CORE_NAMES:
        try:
            names = {p.parent.name for p in (_PLUGIN_ROOT / "skills").glob("*/SKILL.md")}
        except OSError:
            names = set()
        _CORE_NAMES["v"] = frozenset(names)
    return _CORE_NAMES["v"]


def project_skill_names(project_dir):
    """项目 skills 目录里的技能名集合（目录解析同 `_harness.project_skills_dir`）；读不到返回空集。"""
    if project_dir is None:
        return frozenset()
    try:
        import _harness
        root = Path(project_dir) / _harness.project_skills_dir(project_dir)
        return frozenset(p.parent.name for p in root.glob("*/SKILL.md"))
    except Exception:
        return frozenset()


def identity(skill, plugin=None, *, core_names=None, project_names=frozenset(), own_plugin=None):
    """事件里的 `skill`（＋可选 `plugin` 字段）→ 规范身份字符串；`skill` 不是非空字符串时返回 None。

    `core_names` / `project_names` / `own_plugin` 由调用方一次取好传入（一次聚合要判上千行，
    不该每行扫目录）；`core_names` 缺省时现取 `core_skill_names()`。
    """
    if not isinstance(skill, str) or not skill.strip():
        return None
    name = skill.strip()
    if ":" in name:
        return name
    if isinstance(plugin, str) and plugin.strip():
        return f"{plugin.strip()}:{name}"
    core = core_skill_names() if core_names is None else core_names
    if name in core and name not in project_names:
        return f"{own_plugin or plugin_name()}:{name}"
    return name


def resolver(project_dir):
    """绑好本次聚合所需集合的 `identity`：`resolve(skill, plugin) -> str | None`。"""
    core, project, own = core_skill_names(), project_skill_names(project_dir), plugin_name()

    def resolve(skill, plugin=None):
        return identity(skill, plugin, core_names=core, project_names=project, own_plugin=own)

    return resolve
