#!/usr/bin/env python3
"""PostToolUse delivers module reminders; PostCompact resets main-thread dedup."""
import json
from pathlib import Path
import sys

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
import _harness  # noqa: E402


def main():
    if _harness.hook_outside_project():
        return 0
    try:
        payload = _harness.hook_payload()
        if payload is None:
            payload = json.loads(sys.stdin.buffer.read().decode('utf-8') or '{}')
        if _harness.hook_outside_project(payload):
            return 0
        import _module_memory
        if payload.get('hook_event_name') == 'PostCompact':
            _module_memory.rotate_after_compact(_harness.project_dir(payload), payload)
            return 0
        text = _module_memory.deliver(_harness.project_dir(payload), payload)
        if text:
            print(json.dumps({'hookSpecificOutput':{'hookEventName':'PostToolUse',
                                                   'additionalContext':text}}, ensure_ascii=True))
    except Exception as error:
        print('[warn] module memory hook: ' + type(error).__name__, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    # exit-audited: main() returns only zero; reminder failures must not block the tool.
    sys.exit(main())
