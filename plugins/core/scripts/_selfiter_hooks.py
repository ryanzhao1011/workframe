#!/usr/bin/env python3
"""Small hook adapter; workers own expensive analysis and writes."""
import sys

from _selfiter_policy import background_off, in_job


def on_hook(project, door, event, session_id=''):
    if sys.version_info < (3, 11):
        return None
    if in_job() or background_off():
        return None
    try:
        from selfiter_controller import Controller
        controller = Controller(project)
        config = controller.configuration()
        if event in ('SessionStart', 'UserPromptSubmit') and controller.base.exists():
            controller.verify_due()
            message = controller.summary(session_id or 'unknown')
        else:
            message = None
        if event == 'SessionStart' or (event == 'SessionEnd' and door == 'cc') or (event == 'Stop' and door == 'codex'):
            if config['mode'] != 'off':
                from _selfiter_evidence import collect_evidence
                progress = __import__('selfiter_controller').read_json(controller.base / 'progress.json', {})
                evidence = collect_evidence(project, progress.get('consumed_ids', ()))
                if evidence['evidence']:
                    controller.queue(door)
        return message
    except (OSError, ValueError, TimeoutError, ImportError) as exc:
        print('[selfiter] deferred: ' + str(exc), file=sys.stderr)
        return None


def suppress_cadence(project):
    """Only presentation changes; the old trigger computations remain intact."""
    if sys.version_info < (3, 11) or background_off():
        return False
    try:
        from selfiter_controller import Controller
        return Controller(project).configuration()['mode'] != 'off'
    except (OSError, ValueError):
        return False
