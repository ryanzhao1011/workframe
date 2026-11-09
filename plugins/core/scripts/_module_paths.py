"""Shared hook path extraction; importing this helper does not alter stdio.

The stale index remains authoritative. Its public query accepts an explicit
project, so a reader need not change process-global paths to reuse the index.
"""
import importlib.util
from pathlib import Path
import re
import sys

EDIT_TOOLS = ('Edit', 'Write', 'MultiEdit', 'NotebookEdit')
PATCH_TOOL = 'apply_patch'
_PATCH_HEAD_RE = re.compile(r'^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+?)[ \t]*\r?$', re.M)


def edited_paths(payload):
    """Return original file paths, including both sides of patch moves."""
    tool = payload.get('tool_name', '')
    raw = payload.get('tool_input', {}) or {}
    if tool in EDIT_TOOLS:
        path = (raw.get('file_path') or raw.get('notebook_path')) if isinstance(raw, dict) else None
        return [path] if isinstance(path, str) and path else []
    if tool != PATCH_TOOL:
        return []
    patch = raw.get('command') if isinstance(raw, dict) else raw
    if isinstance(patch, list):
        patch = '\n'.join(x for x in patch if isinstance(x, str))
    paths = []
    for match in _PATCH_HEAD_RE.finditer(patch if isinstance(patch, str) else ''):
        path = match.group(1).strip()
        if path and path not in paths:
            paths.append(path)
    return paths


def lookup_submodules(project_dir, file_path):
    """Reuse the stale index's query and rebuild contract for an explicit root.

The entry script is loaded lazily and caches by module name; its stdio setup
is restricted to its command-line entry point. parse_code_paths remains in
that script because the migration tool extracts that function independently.
"""
    name = '_workframe_stale_index'
    module = sys.modules.get(name)
    if module is None:
        path = Path(__file__).with_name('check-stale-modules.py')
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(name, None)
            raise
    return module.lookup_submodules(file_path, project_dir=project_dir)
