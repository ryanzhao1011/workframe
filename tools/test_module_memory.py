"""Behavior tests for two-level, on-demand module reminders."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

FRAMEWORK = Path(__file__).resolve().parents[1]
SCRIPTS = FRAMEWORK / 'plugins/core/scripts'
sys.path.insert(0, str(SCRIPTS))


def load_reminders():
    path = SCRIPTS / '_module_memory.py'
    if not path.is_file():
        raise AssertionError('Module reminders are not implemented: _module_memory.py is missing')
    spec = importlib.util.spec_from_file_location('module_memory_under_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ModuleMemoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mm = load_reminders()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='wf-module-memory-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.put('.workframe-config.json', '{}\n')
        self.put('projects/modules/alpha/module.yaml', 'name: alpha\n')
        self.put('projects/modules/beta/module.yaml', 'name: beta\n')
        for basic, sub in [('alpha', 'one'), ('alpha', 'two'), ('beta', 'three')]:
            self.put(f'projects/modules/{basic}/{sub}/submodule.yaml',
                     f'parent_module: {basic}\nname: {sub}\ncode_paths: []\n')
            self.put(f'projects/modules/{basic}/{sub}/overview.md', '# Ordinary overview\n')
        self.put('projects/modules/alpha/module-memory.md', '- Parent reminder ALPHA.\n')
        self.put('projects/modules/alpha/one/module-memory.md', '- Child reminder ONE.\n')
        self.put('projects/modules/alpha/two/module-memory.md', '- Child reminder TWO.\n')
        self.put('projects/modules/beta/module-memory.md', '- Parent reminder BETA.\n')
        self.put('projects/modules/beta/three/module-memory.md', '- Child reminder THREE.\n')

    def put(self, relative, text):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode('utf-8'))
        return path

    def payload(self, tool='Read', path='projects/modules/alpha/one/overview.md', agent=None):
        data = {'session_id':'session-A', 'cwd':str(self.root), 'tool_name':tool,
                'tool_input':{'file_path':path}}
        if tool in ('Bash', 'PowerShell'):
            data['tool_input'] = {'command':f'Get-Content -LiteralPath "{path}"'}
        if agent:
            data['agent_id'] = agent
        return data

    def events(self):
        path = self.root / '.workframe/state/events.jsonl'
        return [json.loads(x) for x in path.read_text(encoding='utf-8').splitlines()] if path.exists() else []

    def test_parent_before_child_and_source_base(self):
        out = self.mm.deliver(self.root, self.payload())
        self.assertLess(out.index('ALPHA'), out.index('ONE'))
        self.assertIn('projects/modules/alpha/one/module-memory.md', out)
        self.assertIn('projects/modules/alpha/one', out)
        self.assertNotIn('TWO', out)

    def test_same_thread_dedup_and_sibling_parent_once(self):
        self.mm.deliver(self.root, self.payload())
        receipts = lambda: [e for e in self.events() if e.get('type') == 'module_memory_injected']
        first = receipts()
        self.assertTrue(first and all(e.get('outcome') == 'injected' for e in first), first)
        self.assertFalse(self.mm.deliver(self.root, self.payload()))
        self.assertEqual(receipts(), first, '已投过、去重跳过不写回执')
        out = self.mm.deliver(self.root, self.payload(path='projects/modules/alpha/two/overview.md'))
        self.assertIn('TWO', out)
        self.assertNotIn('ALPHA', out)

    def test_parent_child_and_sibling_threads_are_independent(self):
        self.mm.deliver(self.root, self.payload())
        self.assertIn('ONE', self.mm.deliver(self.root, self.payload(agent='child-A')))
        self.assertIn('ONE', self.mm.deliver(self.root, self.payload(agent='child-B')))
        self.assertFalse(self.mm.deliver(self.root, self.payload(agent='child-A')))

    def test_compact_rotates_only_main_and_does_not_eagerly_deliver(self):
        self.mm.deliver(self.root, self.payload())
        self.mm.deliver(self.root, self.payload(agent='child-A'))
        self.assertIsNone(self.mm.rotate_after_compact(self.root, self.payload()))
        self.assertIn('ONE', self.mm.deliver(self.root, self.payload()))
        self.assertFalse(self.mm.deliver(self.root, self.payload(agent='child-A')))
        self.mm.rotate_after_compact(self.root, self.payload(agent='child-A'))
        self.assertFalse(self.mm.deliver(self.root, self.payload(agent='child-A')))

    def test_basic_shared_only_loads_parent(self):
        self.put('projects/modules/alpha/shared/fact.md', '# fact\n')
        out = self.mm.deliver(self.root, self.payload(path='projects/modules/alpha/shared/fact.md'))
        self.assertIn('ALPHA', out)
        self.assertNotIn('ONE', out)

    def test_absent_memory_not_marked_so_later_file_is_seen(self):
        self.put('projects/modules/alpha/absent/submodule.yaml', 'name: absent\n')
        payload = self.payload(path='projects/modules/alpha/absent/new.md')
        self.mm.deliver(self.root, payload)
        self.put('projects/modules/alpha/absent/module-memory.md', '- Created later LATER.\n')
        self.assertIn('LATER', self.mm.deliver(self.root, payload))

    def test_module_without_memory_file_writes_no_event_or_state(self):
        self.put('projects/modules/gamma/module.yaml', 'name: gamma\n')
        self.put('projects/modules/gamma/four/submodule.yaml', 'name: four\n')
        for _ in range(3):
            self.assertIsNone(self.mm.deliver(self.root, self.payload(path='projects/modules/gamma/four/x.md')))
        self.assertEqual([e for e in self.events() if e['type'] == 'module_memory_injected'], [])
        self.assertFalse((self.root / '.workframe/state' / self.mm.STATE_FILE).exists())

    def test_state_keeps_most_recently_touched_sessions(self):
        def touch(session):
            payload = self.payload()
            payload['session_id'] = session
            return self.mm.deliver(self.root, payload)
        with patch.object(self.mm, 'MAX_SESSIONS', 2):
            touch('s1'); touch('s2')
            self.assertFalse(touch('s1'))  # re-touch moves s1 to most recent
            touch('s3')                    # evicts s2, the least recently touched
            state = json.loads((self.root / '.workframe/state' / self.mm.STATE_FILE).read_text(encoding='utf-8'))
            self.assertEqual(list(state['sessions']), ['s1', 's3'])
            self.assertFalse(touch('s1'))
            self.assertIn('ONE', touch('s2'))

    def test_over_budget_pointer_not_marked_and_retries(self):
        payload = self.payload()
        self.put('projects/modules/alpha/one/module-memory.md', '- Child reminder ONE. ' + 'x' * 250 + '\n')
        out = self.mm.deliver(self.root, payload, budget=380)
        self.assertLessEqual(self.mm.u16len(out), 380)
        self.assertIn('未投全文', out)
        self.assertIn('ONE', self.mm.deliver(self.root, payload))

    def test_unreadable_utf8_not_marked(self):
        path = self.root / 'projects/modules/alpha/one/module-memory.md'
        path.write_bytes(b'\xff\xfe\xff')
        self.mm.deliver(self.root, self.payload())
        path.write_bytes(b'- Recovered RECOVERED.\n')
        self.assertIn('RECOVERED', self.mm.deliver(self.root, self.payload()))

    def test_lock_timeout_does_not_mark(self):
        with patch.object(self.mm, 'FileLock', side_effect=TimeoutError('forced lock timeout')):
            self.mm.deliver(self.root, self.payload())
        self.assertIn('ONE', self.mm.deliver(self.root, self.payload()))
        self.assertTrue(any(e.get('outcome') == 'lock_timeout' for e in self.events()))

    def test_empty_budget_never_spills_or_marks(self):
        self.assertFalse(self.mm.deliver(self.root, self.payload(), budget=0))
        self.assertIn('ONE', self.mm.deliver(self.root, self.payload()))

    def test_invalid_module_and_outside_project_are_not_hits(self):
        self.put('projects/modules/pretend/sub/module-memory.md', '- SHOULD_NOT_DELIVER\n')
        self.assertFalse(self.mm.deliver(self.root, self.payload(path='projects/modules/pretend/sub/f.md')))
        self.put('node_modules/alpha/shared/module.yaml', 'name: fake\n')
        self.assertFalse(self.mm.deliver(self.root, self.payload(path='node_modules/alpha/shared/f.md')))
        outside = self.root.parent / 'projects/modules/alpha/one/overview.md'
        self.assertFalse(self.mm.deliver(self.root, self.payload(path=str(outside))))

    def test_shell_multiple_paths_relative_cwd_and_quotes(self):
        payload = self.payload('Bash')
        payload['tool_input']['command'] += ' ; Get-Content "projects/modules/beta/three/overview.md"'
        out = self.mm.deliver(self.root, payload)
        self.assertIn('ONE', out)
        self.assertIn('THREE', out)
        payload['session_id'] = 'session-B'
        payload['cwd'] = str(self.root / 'projects/modules/alpha/one')
        payload['tool_input']['command'] = 'Get-Content overview.md'
        self.assertIn('ONE', self.mm.deliver(self.root, payload))

    def test_shell_explicit_workdir_and_location_prefix(self):
        payload = self.payload('Bash')
        payload['tool_input'] = {'command':'Get-Content overview.md',
                                 'workdir':str(self.root / 'projects/modules/alpha/two')}
        self.assertIn('TWO', self.mm.deliver(self.root, payload))
        payload['session_id'] = 'session-B'
        payload['tool_input'] = {'command':'cd projects/modules/beta/three && cat overview.md'}
        self.assertIn('THREE', self.mm.deliver(self.root, payload))
        payload['session_id'] = 'session-C'
        payload['tool_input'] = {'command':"Set-Location -LiteralPath 'projects/modules/alpha/one'; Get-Content overview.md"}
        self.assertIn('ONE', self.mm.deliver(self.root, payload))

    def test_shell_does_not_reverse_lookup_code_but_read_and_patch_do(self):
        self.put('src/app.py', '# code\n')
        # 映射写在声明里，不手填缓存：读者按声明的 stat 摘要判过期，手填的缓存没有 source_stamp、会被重建掉
        for basic, sub in [('alpha', 'one'), ('beta', 'three')]:
            self.put(f'projects/modules/{basic}/{sub}/submodule.yaml',
                     f'parent_module: {basic}\nname: {sub}\ncode_paths:\n  - src/**\n')
        self.assertFalse(self.mm.deliver(self.root, self.payload('Bash', path='src/app.py')))
        out = self.mm.deliver(self.root, self.payload(path='src/app.py'))
        self.assertIn('ONE', out)
        self.assertIn('THREE', out)
        payload = self.payload()
        payload['session_id'] = 'session-B'
        payload['tool_name'] = 'apply_patch'
        payload['tool_input'] = {'command':'*** Begin Patch\n*** Update File: src/app.py\n@@\n-old\n+new\n*** End Patch'}
        self.assertIn('ONE', self.mm.deliver(self.root, payload))

    def test_patch_move_resolves_both_sides(self):
        payload = self.payload()
        payload['tool_name'] = 'apply_patch'
        payload['tool_input'] = {'command':'*** Begin Patch\n*** Update File: projects/modules/alpha/one/old.md\n*** Move to: projects/modules/beta/three/new.md\n@@\n-a\n+b\n*** End Patch'}
        out = self.mm.deliver(self.root, payload)
        self.assertIn('ONE', out)
        self.assertIn('THREE', out)

    def test_emoji_output_budget_uses_utf16(self):
        self.put('projects/modules/alpha/one/module-memory.md', '- ' + '\U0001f642' * 160 + '\n')
        self.mm.deliver(self.root, self.payload(path='projects/modules/alpha/module.yaml'))
        out = self.mm.deliver(self.root, self.payload(), budget=400)
        self.assertLessEqual(self.mm.u16len(out), 400)
        self.assertIn('未投全文', out or '')
        self.assertNotIn('\U0001f642' * 160, out)

    def test_events_record_thread_cycle_tool_and_character_units(self):
        self.mm.deliver(self.root, self.payload(agent='child-A'))
        rows = [e for e in self.events() if e.get('outcome') == 'injected']
        self.assertEqual({e['module'] for e in rows}, {'alpha','alpha/one'})
        for event in rows:
            self.assertEqual(event['session_id'], 'session-A')
            self.assertEqual(event['agent_id'], 'child-A')
            self.assertEqual(event['cycle'], 0)
            self.assertEqual(event['tool'], 'Read')
            self.assertGreater(event['chars'], 0)

    def test_read_error_does_not_block_other_modules(self):
        payload = self.payload('Bash')
        payload['tool_input']['command'] += ' ; cat projects/modules/beta/three/overview.md'
        (self.root / 'projects/modules/alpha/one/module-memory.md').write_bytes(b'\xff')
        self.assertIn('THREE', self.mm.deliver(self.root, payload))

    def test_concurrent_siblings_preserve_both_records_and_parent_once(self):
        code = (
            "import json,sys,time; from pathlib import Path; "
            "sys.path.insert(0,sys.argv[1]); import _module_memory as m; "
            "original=m._load; "
            "exec('def delayed(p):\\n state=original(p)\\n time.sleep(0.25)\\n return state'); "
            "m._load=delayed; "
            "p=Path(sys.argv[2]); n=sys.argv[3]; "
            "(p/('ready-'+n)).write_bytes(b'ready'); "
            "exec('while not (p/\"go\").exists(): time.sleep(0.01)'); "
            "out=m.deliver(p,{'session_id':'parallel','cwd':str(p),'tool_name':'Read',"
            "'tool_input':{'file_path':'projects/modules/alpha/'+n+'/overview.md'}}); "
            "print(json.dumps(out))")
        processes = [subprocess.Popen([sys.executable,'-c',code,str(SCRIPTS),str(self.root),sub],
                                      stdout=subprocess.PIPE,stderr=subprocess.PIPE)
                     for sub in ('one','two')]
        try:
            deadline = time.monotonic()+20
            while not all((self.root/('ready-'+s)).exists() for s in ('one','two')):
                self.assertLess(time.monotonic(),deadline,'Children did not reach the barrier')
                time.sleep(0.01)
            (self.root/'go').write_bytes(b'go')
            outputs = []
            for process in processes:
                stdout, stderr = process.communicate(timeout=20)
                self.assertEqual(process.returncode,0,stderr.decode('utf-8',errors='replace'))
                outputs.append(json.loads(stdout))
            joined='\n'.join(o or '' for o in outputs)
            self.assertEqual(joined.count('Parent reminder ALPHA.'),1)
            self.assertEqual(joined.count('Child reminder ONE.'),1)
            self.assertEqual(joined.count('Child reminder TWO.'),1)
            payload=self.payload()
            payload['session_id']='parallel'
            self.assertFalse(self.mm.deliver(self.root,payload))
            payload['tool_input']['file_path']='projects/modules/alpha/two/overview.md'
            self.assertFalse(self.mm.deliver(self.root,payload))
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.wait()

    def test_legacy_stale_import_keeps_host_streams_and_api(self):
        stdout,stderr=sys.stdout,sys.stderr
        self.mm.lookup_submodules(self.root,'src/missing.py')
        self.assertIs(sys.stdout,stdout)
        self.assertIs(sys.stderr,stderr)

    def test_hook_json_and_real_compact_payload_for_both_harnesses(self):
        script=SCRIPTS/'module-memory-inject.py'
        for harness in ('cc','codex'):
            payload=self.payload()
            payload['session_id']='hook-'+harness
            payload['hook_event_name']='PostToolUse'
            def invoke(data):
                result=subprocess.run([sys.executable,str(script),'--harness',harness],
                                      cwd=self.root,input=json.dumps(data).encode('utf-8'),
                                      capture_output=True,timeout=15)
                self.assertEqual(result.returncode,0,result.stderr.decode('utf-8',errors='replace'))
                return result.stdout.decode('utf-8')
            first=json.loads(invoke(payload))
            self.assertIn('ONE',first['hookSpecificOutput']['additionalContext'])
            self.assertEqual(invoke(payload),'')
            compact={k:payload[k] for k in ('session_id','cwd')}
            compact.update(hook_event_name='PostCompact',trigger='manual')
            self.assertEqual(invoke(compact),'')
            self.assertIn('ONE',json.loads(invoke(payload))['hookSpecificOutput']['additionalContext'])

    def test_shipping_manifest_keeps_both_module_entry_points(self):
        data=json.loads((SCRIPTS.parent/'hooks/hooks.json').read_text(encoding='utf-8'))['hooks']
        for event in ('PostToolUse','PostCompact'):
            self.assertTrue(any('module-memory-inject.py' in h.get('command','')
                                for group in data.get(event,[]) for h in group.get('hooks',[])))

    @unittest.skipUnless(os.name=='nt','Git Bash drive paths are Windows-specific')
    def test_windows_absolute_and_git_bash_paths(self):
        path=self.root/'projects/modules/alpha/one/overview.md'
        self.assertIn('ONE',self.mm.deliver(self.root,self.payload(path=str(path))))
        payload=self.payload(path='/'+path.drive[0].lower()+path.as_posix()[2:])
        payload['session_id']='git-bash'
        self.assertIn('ONE',self.mm.deliver(self.root,payload))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    unittest.main(verbosity=2)
