"""Module/document directory metadata; a damaged document stays navigable."""

import importlib
from pathlib import Path

from _coverage_probe import frontmatter_block
from _doc_state import ABSENT, MARK_KEY, effective_state
from _harness import is_dir_link
from _query_board import normalized_date, parse_detail, yaml_warning
from _query_paths import project_relative
from _state_io import state_dir_of
from _yaml_probe import safe_load
from project_scaffold import _outside_fence


def document_metadata(text):
    text = text.lstrip(chr(0xFEFF)).replace("\r\n", "\n").replace("\r", "\n")
    block = frontmatter_block(text)
    if block is None:
        if text.split("\n", 1)[0].strip() == "---":
            raise ValueError("Unterminated frontmatter")
        return {}
    metadata = safe_load(block)
    if not isinstance(metadata, dict):
        raise ValueError("Frontmatter must be a mapping")
    return metadata


def document_text(text, rel, kind, warnings):
    raw = text.encode("utf-8")       # 判「未编写」按原文的行，不按下面归一过换行的副本
    text = text.lstrip(chr(0xFEFF)).replace("\r\n", "\n").replace("\r", "\n")
    lines = text.splitlines()
    if frontmatter_block(text) is not None:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
        lines = lines[end + 1:]
    heading = next((line[2:].strip() for _, line in _outside_fence(lines)
                    if line.startswith("# ")), Path(rel).stem)
    metadata, problem = {}, None
    try:
        metadata = document_metadata(text)
    except Exception as exc:
        metadata, problem = {}, f"{rel}: {type(exc).__name__}: {exc}"
    # Only a stamped skeleton whose body is unchanged counts as unwritten; a literal
    # `true` (unstamped) or a damaged frontmatter is shown as written, so real text is
    # never hidden behind the flag.
    unwritten = problem is None and effective_state(
        raw, metadata.get(MARK_KEY, ABSENT)) == "unwritten"
    return {"kind": kind, "title": str(metadata.get("title") or heading), "path": rel,
            "type": str(metadata["type"]) if metadata.get("type") is not None else None,
            "status": str(metadata["status"]) if metadata.get("status") is not None else None,
            "updated": normalized_date(metadata.get("updated"), warnings, rel + ".updated"),
            "unwritten": unwritten, "meta_error": problem}


def _document(path, project, kind, warnings):
    return _document_source(path, project, kind, warnings)[0]


def _document_source(path, project, kind, warnings):
    rel = path.relative_to(project).as_posix()
    try:
        text = path.read_text(encoding="utf-8-sig")
        return document_text(text, rel, kind, warnings), text
    except (OSError, UnicodeError) as exc:
        doc = document_text("", rel, kind, warnings)
        doc["meta_error"] = f"{rel}: {type(exc).__name__}: {exc}"
        doc["unwritten"] = False
        return doc, None


def _mapping_file(path, project, error):
    rel = path.relative_to(project).as_posix()
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise error("internal", str(exc), {"file": rel}) from exc
    try:
        data = safe_load(raw.decode("utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError("Expected a mapping")
        return data
    except Exception as exc:
        raise error("parse_error", f"Cannot parse {rel}: {exc}", parse_detail(raw, rel)) from exc


def _directories(path):
    if not path.exists():
        return []
    return sorted(child for child in path.iterdir() if child.is_dir() and not is_dir_link(child))


def _markdown(path):
    """Walk project-owned directories only; directory links are not recursed."""
    if not path.exists():
        return []
    files, pending = [], [path]
    while pending:
        for child in sorted(pending.pop().iterdir()):
            if child.is_dir():
                if not is_dir_link(child):
                    pending.append(child)
            elif child.suffix.lower() == ".md":
                files.append(child)
    return sorted(files)


def stale_details(info):
    return importlib.import_module("check-stale-modules").structured_stale(info)


def dependencies(project, cfg):
    close_cfg = cfg.get("close_check") or {}
    ledger = close_cfg.get("ledger", "projects/changelog.md") if isinstance(close_cfg, dict) else "projects/changelog.md"
    paths = [".workframe-config.json", "projects/modules/", "projects/specs/",
             (state_dir_of(project) / "stale-modules.yaml").relative_to(project).as_posix(),
             "projects/changelog.md"]
    if isinstance(ledger, str) and ledger not in paths:
        paths.append(ledger)
    return paths


def _requirements(sub, project, warnings, error):
    out = []
    for req in _directories(sub / "requirements"):
        try:
            meta = _mapping_file(req / "meta.yaml", project, error)
        except error as exc:
            meta = {}
            warnings.append(str(exc))
        title = meta.get("title")
        if not title and (req / "overview.md").is_file():
            title = _document(req / "overview.md", project, "overview", warnings)["title"]
        children = []
        for child in _directories(req):
            prd = child / "prd.md"
            if not prd.is_file():
                continue
            doc, text = _document_source(prd, project, "other", warnings)
            description = None
            if doc["meta_error"] is None:
                fields = document_metadata(text)
                description = fields.get("description")
            else:
                warnings.append(doc["meta_error"])
            children.append({"slug": child.name, "prd_path": doc["path"], "status": doc["status"],
                             "description": str(description) if description is not None else None,
                             "unwritten": doc["unwritten"]})
        out.append({"slug": req.name, "title": str(title or req.name),
                    "status": str(meta["status"]) if meta.get("status") is not None else None, "subs": children})
    return out


def _submodule(sub, project, warnings, marks, error):
    rel = sub.relative_to(project).as_posix()
    result = {"name": sub.name, "path": rel, "status": None,
              "overview_path": rel + "/overview.md" if (sub / "overview.md").is_file() else None,
              "stale": None, "docs": [], "requirements": [], "error": None}
    try:
        metadata = _mapping_file(sub / "submodule.yaml", project, error)
        result["name"] = str(metadata.get("name") or sub.name)
        result["status"] = str(metadata["status"]) if metadata.get("status") is not None else None
    except error as exc:
        result["error"] = {"code": exc.code, "message": str(exc), "file": rel + "/submodule.yaml"}
    key = sub.parent.name + "/" + sub.name
    if key in marks:
        result["stale"] = stale_details(marks[key])
    for path in _markdown(sub):
        parts = path.relative_to(sub).parts
        if parts[0] == "requirements":
            continue
        kind = "other"
        if path.name == "overview.md":
            kind = "overview"
        elif parts[0] == "current-state" and path.stem in ("architecture", "api-surface", "data-model", "code-map"):
            kind = path.stem
        elif parts[0] in ("decisions", "research", "shared"):
            kind = {"decisions": "decision", "research": "research", "shared": "shared"}[parts[0]]
        result["docs"].append(_document(path, project, kind, warnings))
    result["requirements"] = _requirements(sub, project, warnings, error)
    return result


def run(project, cfg, warnings, depends_on, *, error):
    try:
        yaml_warning(warnings)
    except ImportError as exc:
        raise error("internal", "Reading module metadata requires PyYAML") from exc
    stale_file = state_dir_of(project) / "stale-modules.yaml"
    # Validate syntax for the query's error envelope, then use the framework's
    # recorded-state reader. No stale comparison, lock, or write entry is run.
    _mapping_file(stale_file, project, error)
    marks = importlib.import_module("check-stale-modules").load_stale(stale_file)["submodules"]
    basics = []
    for basic in _directories(project / "projects/modules"):
        if basic.name == "shared":
            continue
        try:
            meta = _mapping_file(basic / "module.yaml", project, error)
        except error as exc:
            meta = {}
            warnings.append(str(exc))
        subs = [child for child in _directories(basic) if child.name != "shared" and any(
            (child / name).exists() for name in ("submodule.yaml", "overview.md", "current-state", "requirements"))]
        basics.append({"name": str(meta.get("name") or basic.name),
                       "status": str(meta["status"]) if meta.get("status") is not None else None,
                       "overview_path": (basic / "overview.md").relative_to(project).as_posix()
                       if (basic / "overview.md").is_file() else None,
                       "shared": [_document(path, project, "shared", warnings) for path in _markdown(basic / "shared")],
                       "subs": [_submodule(sub, project, warnings, marks, error) for sub in subs]})
    ledgers = []
    for rel in depends_on:
        if rel.endswith(".md"):
            path = (project / rel).resolve()
            try:
                relative = project_relative(path, project)
            except ValueError as exc:
                raise error("parse_error", "Configured ledger must be inside the project",
                            {"file": ".workframe-config.json", "line": 1}) from exc
            if path.is_file():
                ledgers.append(_document(project / relative, project, "ledger", warnings))
    return {"basics": basics, "ledgers": ledgers,
            "cross_module": [_document(path, project, "plan", warnings)
                             for path in _markdown(project / "projects/specs")]}
