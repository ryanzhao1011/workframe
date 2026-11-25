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
                # The same selection the worker uses, failed re-checks included.
                if controller.scan()['trigger']:
                    controller.queue(door)
        return message
    except Exception as exc:  # a session hook reports the problem, it never becomes one
        print('[selfiter] deferred: ' + str(exc), file=sys.stderr)
        return None
