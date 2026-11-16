"""Execute workflow shells against historical/current source with bounded tools.

Only Docker/Git/native-host checks are replaced in the launcher fixture. Its
fresh compiler edge runs host gcc on one tiny C file, never a kernel or image.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock

from tests.test_workflow_cache_ci import workflow, expression_value
from tests import test_kernel_compile_cache as contracts

ROOT = Path(__file__).resolve().parents[1]
# Frozen from e1c89f3: writer-only host importer/launcher and the bounded
# implicit-export function. Keep runnable with a shallow CI checkout.
LEGACY = ROOT/'tests/fixtures/kernel_cache_legacy'
FILES = ('scripts/kernel_compile_cache.py', 'scripts/run-native-stage.py',
         'producer/kernel_compile_cache.py')

DRIVER = r'''
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

args = sys.argv[1:]
if args == ['-']:
    raise SystemExit(subprocess.run([sys.executable, '-'], stdin=sys.stdin).returncode)
path = args[0]
spec = importlib.util.spec_from_file_location('checked_source', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
sys.argv = args
log = Path(os.environ['CALLS'])
def record(value):
    with log.open('a') as stream:
        stream.write(json.dumps(value) + '\n')
if path.endswith('kernel_compile_cache.py'):
    record({'import': args, 'token': os.environ.get('GH_TOKEN')})
    def api(*args):
        record({'api': True})
        return object()
    def restore(*args):
        record({'restore': True})
        return 'MISS'
    with patch.object(module, 'API', side_effect=api), patch.object(module, 'import_cache', side_effect=restore), \
         patch('producer.kernel_seed.key', return_value='a'*64):
        module.main()
else:
    assert 'GH_TOKEN' not in os.environ
    def output(command, **kwargs):
        if command == ['git', 'rev-parse', 'HEAD']: return os.environ['SOURCE_COMMIT']
        if command == ['git', 'status', '--porcelain']: return ''
        assert command[:3] == ['docker', 'image', 'inspect'], command
        return os.environ['ARCHITECTURE']
    def execute(*command, **kwargs):
        if command[0] == 'git':
            assert command[:3] == ('git', 'archive', '--format=tar')
            kwargs['stdout'].write(b'bounded source fixture')
            return
        assert command[:2] == ('docker', 'run'), command
        record({'build': list(command)})
        destination = next(command[i+1].removesuffix(':/output') for i, arg in enumerate(command)
                           if arg == '-v' and command[i+1].endswith(':/output'))
        destination = Path(destination)
        # This compiler must actually run for unsupported sources, not just an
        # argv capture that could conceal an early exit in the workflow shell.
        subprocess.run(['gcc', '-x', 'c', '-c', '-o', str(destination/'fresh.o'), '-'],
                       input='int fresh(void) { return 7; }\n', text=True, check=True)
        (destination/'transfer.tar').write_bytes(b'bounded transfer')
        (destination/'transfer.json').write_text('{}')
        (destination/'resources.json').write_text('{}')
    with patch.object(module, 'native'), patch.object(module.subprocess, 'check_output', side_effect=output), \
         patch.object(module, 'run', side_effect=execute):
        module.main()
'''


class KernelCacheCapabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.job = workflow('staged-candidate.yml')['jobs']['kernel']
        cls.probe = next(s for s in cls.job['steps'] if s.get('id') == 'kernel-cache-capability')
        cls.importer = next(s for s in cls.job['steps'] if 'kernel_compile_cache.py import' in s.get('run',''))
        cls.execute = next(s for s in cls.job['steps'] if 'run-native-stage.py' in s.get('run',''))
        cls.old = {name: (LEGACY/('implicit_export.py' if name.startswith('producer/') else Path(name).name)).read_text()
                   for name in FILES}
        cls.current = {name: (ROOT/name).read_text() for name in FILES}

    def source(self, root, files):
        for name, text in files.items():
            target = root/name; target.parent.mkdir(parents=True, exist_ok=True); target.write_text(text)

    def probe_source(self, root, env):
        output = root/'probe-output'; output.unlink(missing_ok=True)
        subprocess.run(['bash','-eu','-c',self.probe['run']], cwd=root,
                       env=dict(env,GITHUB_OUTPUT=str(output)), check=True, capture_output=True)
        return dict(line.split('=',1) for line in output.read_text().splitlines())['supported']

    def test_historical_reader_failure_before_fix(self):
        # Reproduce the old unguarded workflow command with real old source.
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); self.source(root,self.old)
            event=root/'event';event.write_text('{}')
            env=dict(os.environ,PYTHONPATH=str(ROOT),CACHE_WRITE='false',CACHE_MODE='cold',
                     GITHUB_EVENT_NAME='pull_request',GITHUB_EVENT_PATH=str(event),
                     KERNEL_CACHE=str(root/'cache'),ARCHITECTURE='amd64',SOURCE_COMMIT='b'*40)
            result=subprocess.run(['bash','-eu','-c',self.importer['run']],
                cwd=root,env=env,text=True,capture_output=True)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('current_context',result.stderr)
            self.assertFalse((root/'cache').exists())
            self.assertIn("report['export'] = export(", self.old['producer/kernel_compile_cache.py'])

    def test_legacy_inner_export_is_implicit_only_when_cache_state_exists(self):
        # Execute the frozen old finish function: a mounted reader used to
        # export unconditionally, which is why gating import alone is unsafe.
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); state=root/'state'; archive=root/'export.tar'
            exports=[]
            def export(store, destination, scope):
                exports.append(destination); destination.write_bytes(b'legacy export')
                return {'entries':1}
            commands=Mock(); commands.check_output.return_value='cache_hit 0'
            namespace=dict(STATE=str(state),Path=Path,json=json,environment=lambda data:{},
                           subprocess=commands,export=export)
            exec(compile(self.old['producer/kernel_compile_cache.py'],'legacy-finish','exec'),namespace)
            namespace['finish']()
            self.assertEqual(exports,[])
            state.write_text(json.dumps(dict(store='bounded-store',mount=str(root),scope={},namespace='fixture',status='MISS')))
            namespace['finish']()
            self.assertEqual(exports,[archive])
            self.assertTrue(archive.is_file())

    def test_extracted_shells_old_current_all_readers_modes_and_arches(self):
        for source, files, supported in [('old',self.old,'false'),('current',self.current,'true')]:
            for caller_env,event,commit in contracts.ConsumerTests().contexts():
                # Both PR labels with local/fork heads, plus feature dispatch.
                if caller_env['GITHUB_EVENT_NAME'] == 'pull_request_target' or caller_env['GITHUB_REF'] == 'refs/heads/main':
                    continue
                for arch in ('amd64','arm64'):
                    for mode in ('cold','warm'):
                        with self.subTest(source=source,event=event,arch=arch,mode=mode), tempfile.TemporaryDirectory() as temp:
                            root=Path(temp); project=root/'project'; project.mkdir(); self.source(project,files)
                            (root/'driver.py').write_text(DRIVER)
                            shim=root/'bin/python3';shim.parent.mkdir()
                            shim.write_text('#!/bin/sh\nexec '+sys.executable+' "$FIXTURE_DRIVER" "$@"\n');shim.chmod(0o755)
                            (root/'event').write_text(json.dumps(event))
                            env=dict(os.environ,**caller_env,PYTHONPATH=str(ROOT),PATH=str(shim.parent)+':'+os.environ['PATH'],
                                FIXTURE_DRIVER=str(root/'driver.py'),CALLS=str(root/'calls'),CACHE_MODE=mode,
                                GITHUB_EVENT_PATH=str(root/'event'),SOURCE_COMMIT=commit,ARCHITECTURE=arch,
                                STAGE='kernel',VERSION='v2026.11.0',COMPILER_IMAGE='sha256:'+'a'*64,
                                STAGE_INPUTS=str(root/'inputs'),STAGE_OUTPUT=str(root/'output'),
                                KERNEL_CACHE=str(root/'cache'),KERNEL_CACHE_WRITE='false')
                            env.pop('GH_TOKEN',None)
                            capability=self.probe_source(project,env)
                            self.assertEqual(capability,supported)
                            context={'github.repository':caller_env['GITHUB_REPOSITORY'],
                                     'steps.kernel-cache-capability.outputs.supported':capability}
                            if expression_value(self.importer['if'],context):
                                subprocess.run(['bash','-eu','-c',self.importer['run']],cwd=project,
                                               env=dict(env,GH_TOKEN='bounded-test-token'),check=True,capture_output=True)
                            env['KERNEL_CACHE_ENABLED']=str(expression_value(self.execute['env']['KERNEL_CACHE_ENABLED'],context)).lower()
                            result=subprocess.run(['bash','-eu','-c',self.execute['run']],cwd=project,env=env,capture_output=True,text=True)
                            self.assertEqual(result.returncode,0,result.stderr)
                            calls=[json.loads(line) for line in (root/'calls').read_text().splitlines()]
                            self.assertTrue((root/'output/fresh.o').is_file())
                            build=[call['build'] for call in calls if 'build' in call]
                            self.assertEqual(len(build),1)
                            self.assertEqual(any('/kernel-compile-cache' in arg for arg in build[0]),supported=='true')
                            self.assertFalse(any('SHIRUBE_KERNEL_COMPILE_CACHE_WRITE=true' in arg for arg in build[0]))
                            self.assertEqual(sum('import' in call for call in calls),int(supported=='true'))
                            self.assertEqual(sum('api' in call for call in calls),int(supported=='true' and mode=='warm'))
                            self.assertEqual(sum('restore' in call for call in calls),int(supported=='true' and mode=='warm'))
                            self.assertFalse((root/'cache/export.tar').exists())
                            if supported=='false':
                                self.assertEqual(calls,[{'build':build[0]}])
                                self.assertFalse((root/'cache').exists())

    def test_malformed_capabilities_are_inert_and_disable_every_cache_edge(self):
        literal="KERNEL_CACHE_CONTRACT = 'reader-v1-explicit-export'\n"
        cases=['', 'raise RuntimeError("must not execute")\n', literal+'raise RuntimeError("must not execute")\n',
               'KERNEL_CACHE_CONTRACT = True\n','KERNEL_CACHE_CONTRACT = 1\n',
               'KERNEL_CACHE_CONTRACT = "future"\n','KERNEL_CACHE_CONTRACT = get_contract()\n',
               literal+literal,literal+'KERNEL_CACHE_CONTRACT: str = "future"\n',
               'other = '+literal,'KERNEL_CACHE_CONTRACT = (\n']
        for text in cases:
            with self.subTest(text=text), tempfile.TemporaryDirectory() as temp:
                root=Path(temp); self.source(root,{'scripts/kernel_compile_cache.py':text})
                supported=self.probe_source(root,os.environ)
                self.assertEqual(supported,'true' if text == literal+'raise RuntimeError("must not execute")\n' else 'false')
                for value in ('false','', 'malformed'):
                    context={'steps.kernel-cache-capability.outputs.supported':value,
                             'github.repository':'michibiki-io/shirube-os',
                             'needs.admission.outputs.kernel-write':'true','success()':True}
                    self.assertFalse(expression_value(self.execute['env']['KERNEL_CACHE_ENABLED'],context))
                    for step in self.job['steps']:
                        if 'kernel-cache-capability.outputs.supported' in step.get('if',''):
                            self.assertFalse(expression_value(step['if'],context))

    def test_absent_or_unreadable_source_contract_is_unsupported(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            self.assertEqual(self.probe_source(root,os.environ),'false')
            target=root/'scripts/kernel_compile_cache.py'; target.parent.mkdir()
            target.write_bytes(b'\xff')
            self.assertEqual(self.probe_source(root,os.environ),'false')
