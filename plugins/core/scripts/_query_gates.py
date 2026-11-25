"""Adapt the pure doctor/close-check results without their CLI write paths."""

from contextlib import contextmanager
import os
from pathlib import Path

import module_close_check as close
import workframe_doctor as doctor
from _state_io import state_dir_of
from codex_roles import LEDGER_REL


@contextmanager
def read_only_git():
    """All nested Git calls inherit no optional writes and no fsmonitor hook."""
    count = int(os.environ.get("GIT_CONFIG_COUNT", "0"))
    if count < 0:
        raise ValueError("Invalid GIT_CONFIG_COUNT")
    additions = {"GIT_OPTIONAL_LOCKS": "0", "GIT_CONFIG_COUNT": str(count + 1),
                 "GIT_CONFIG_KEY_" + str(count): "core.fsmonitor",
                 "GIT_CONFIG_VALUE_" + str(count): "false"}
    before = {key: os.environ.get(key) for key in additions}
    os.environ.update(additions)
    try:
        yield
    finally:
        for key, value in before.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


# Close-check rows whose findings carry the specific file they are about (`path`).
PER_FILE_ROWS = frozenset({"stale-clean", "nav-index"})


def normalize(gate, rows, related):
    """One finding per output row; absent module inputs are never a pass."""
    checks = []
    summary = dict.fromkeys(("ok", "info", "skipped", "warn", "error"), 0)
    for row in rows:
        files = sorted(set(related.get(row["id"], [])))
        doc = next((path for path in files if Path(path).suffix == ".md"), None)
        for finding in row["findings"]:
            level = "skipped" if row["id"] == "modules" else finding["level"]
            summary[level] = summary.get(level, 0) + 1
            # Rows whose findings each name their own file never fall back to an unrelated module document.
            own = finding.get("path")
            fallback = None if row["id"] in PER_FILE_ROWS else doc
            checks.append({"id": row["id"], "name": row["name"], "level": level,
                           "message": finding["msg"], "detail": row.get("criteria", ""),
                           "doc_path": own if own and Path(own).suffix == ".md" else (None if own else fallback),
                           "files": [own] if own else files})
    return {"gate": gate, "summary": summary, "checks": checks}


def related_files(project, cfg, rows):
    """Project-relative navigation inputs, including the configured ledger."""
    state = state_dir_of(project).relative_to(project).as_posix()
    close_cfg = cfg.get("close_check")
    ledger = close_cfg.get("ledger") if isinstance(close_cfg, dict) else None
    module_docs = sorted(path.relative_to(project).as_posix()
                         for path in (project / "projects" / "modules").glob("*/*/submodule.yaml"))
    module_docs += sorted(path.relative_to(project).as_posix()
                          for path in (project / "projects" / "modules").glob("*/*/current-state/*.md"))
    table = {
        "modules": ["projects/modules/"], "ledger": [ledger, ".workframe-config.json"],
        "stale-clean": [state + "/stale-modules.yaml", state + "/code-paths-index.json"] + module_docs,
        "code-paths": module_docs, "ref-consistency": module_docs,
        "module-refreshed": module_docs, "repo-status": [".git/"],
        "code-map-coverage": module_docs, "yaml-parse": module_docs + [ledger],
        "discipline-trace": module_docs + [ledger, state + "/events.jsonl"],
        "nav-index": ["projects/modules/"],
        "agents_md": ["AGENTS.md"], "claude_md": ["CLAUDE.md", "AGENTS.md"],
        "config": [".workframe-config.json"], "codex_trust": [".codex/config.toml"],
        "codex_roles": [".codex/config.toml", ".codex/roles/", LEDGER_REL.as_posix(),
                        state + "/plugin-root.codex.txt"],
        "setup_state": [state + "/setup-state.json"], "gitignore": [".gitignore"],
        "subscription": [".claude/settings.json", ".claude/settings.local.json"],
        "yaml_parse": ["projects/board.yaml", "projects/issues/", "projects/proposals/"],
    }
    for row in rows:
        if row["id"] not in table:
            # A directory is a truthful input scope when a check spans many files.
            table[row["id"]] = [state + "/", ".workframe-config.json"]
    for key, paths in table.items():
        table[key] = [path for path in paths if isinstance(path, str) and not Path(path).is_absolute()
                      and ".." not in Path(path).parts]
    return table


def run(project, gate, cfg, *, guard_git, codex_check):
    """Run the existing verdict functions, with Git and Codex side effects gated."""
    with read_only_git():
        guard_git(project)
        if gate == "doctor":
            # Doctor also inspects the executing plugin's worktree integrity.
            guard_git(Path(__file__).resolve().parents[1])
            rows = doctor.run_all(project, codex_check=codex_check)
        else:
            if (project / "projects" / "modules").is_dir():
                csm = close._load_csm(project)
                subs = close._collect_submodules(project, csm)
                patterns = sorted({path for sub in subs for path in sub["code_paths"]})
                for nested in csm.discover_nested_repos(patterns):
                    guard_git(project / nested)
            rows = close.run_all(project)
    return normalize(gate, rows, related_files(project, cfg, rows))
