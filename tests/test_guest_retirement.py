"""Exercise the real guest wrapper with an isolated fake fixture, never Docker."""
from itertools import product
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from test_iso_recovery import load_module


class GuestWrapperTests(unittest.TestCase):
    def test_broad_host_roots_fail_before_output_docker_or_fixture(self):
        wrapper = load_module('guest_wrapper_host_roots', 'scripts/run-release-container.py')
        for arch, directory in product(('amd64', 'arm64'),
                                      ('/', '/mnt', '/etc', '/var', '/usr', '/home', '/inputs', '/tmp')):
            with self.subTest(arch=arch, inputs=directory), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary); output = root/'output'
                argv = ['wrapper', '--architecture', arch, '--image', 'sha256:'+'a'*64,
                        '--output', str(output), '--project-source', str(root/'project.tar'),
                        '--private', str(root/'private'), '--version', 'v2026.03.0',
                        '--commit', 'b'*40, '--staged-inputs', directory, '--discard-input-archives']
                with patch.object(sys, 'argv', argv), patch.object(wrapper, 'Fixture') as fixture, \
                     patch.object(wrapper, 'release_accelerator') as accelerator, \
                     patch.object(wrapper, 'container_runtime') as runtime, \
                     patch.object(wrapper.subprocess, 'Popen') as popen:
                    with self.assertRaisesRegex(ValueError, 'not a host root'):
                        wrapper.main()
                    self.assertFalse(output.exists()); self.assertFalse((root/'private').exists())
                    fixture.assert_not_called(); popen.assert_not_called()
                    accelerator.assert_not_called(); runtime.assert_not_called()

    def invoke(self, root, arch, inputs=None, discard=False, **paths):
        wrapper = load_module('guest_wrapper', 'scripts/run-release-container.py')
        output = paths.get('output', root/'output'); output.mkdir(exist_ok=True)
        project = paths.get('project', root/'project.tar')
        if not project.exists(): project.write_bytes(b'exact source')
        argv = ['wrapper', '--architecture', arch, '--image', 'sha256:'+'a'*64,
                '--output', str(output), '--project-source', str(project),
                '--private', str(paths.get('private', root/'private')),
                '--cached-libraries', str(paths.get('cache', root/'cache')),
                '--version', 'v2026.03.0', '--commit', 'b'*40, '--qemu-accelerator', 'tcg']
        if inputs is not None: argv += ['--staged-inputs', str(inputs)]
        if discard: argv += ['--discard-input-archives']
        fixture = Mock(network='private-network', container='server', nonce='nonce', hostname='fixture',
                       work=root/'private', url='https://fixture', token='secret-stdin-token')
        fixture.docker.side_effect = lambda *args: (b'10.0.0.2' if args[0]=='inspect' and args[-1]=='server'
                                                   else b'0' if args[0]=='inspect' else b'build')
        fixture.__enter__ = Mock(return_value=fixture); fixture.__exit__ = Mock(return_value=False)
        process = Mock(returncode=0); process.poll.return_value=0
        with patch.object(sys, 'argv', argv), patch.object(wrapper, 'Fixture', return_value=fixture) as construct, \
             patch.object(wrapper, 'container_runtime'), patch.object(wrapper, 'release_accelerator'), \
             patch.object(wrapper.subprocess, 'Popen', return_value=process) as popen:
            if paths.get('unsafe'):
                with self.assertRaises(ValueError): wrapper.main()
                construct.assert_not_called(); popen.assert_not_called()
                return
            wrapper.main()
        command = next(c.args for c in fixture.docker.call_args_list if c.args[0]=='create')
        self.assertIn(str(project.resolve())+':/project-source.tar:ro', command)
        self.assertIn(str(fixture.work/'cert.pem')+':/fixture/cert.pem:ro', command)
        self.assertIn(str(paths.get('cache', root/'cache').resolve())+':/library-cache:ro', command)
        self.assertNotIn(fixture.token, str(command)); self.assertNotIn(fixture.token, str(popen.call_args))
        self.assertEqual(json.loads(process.communicate.call_args.args[0])['token'], fixture.token)
        fixture.__exit__.assert_called_once()
        if inputs is not None:
            self.assertIn(str(inputs.resolve())+':/inputs:'+('rw' if discard else 'ro'), command)
            self.assertIn('producer.staged', command); self.assertIn('guests', command)
        else:
            self.assertNotIn('producer.staged', command)
        self.assertEqual('--discard-input-archives' in command, discard)

    def test_default_staged_and_opt_in_mounts_and_secret_pipe_both_arches(self):
        for arch, mode in product(('amd64','arm64'), ('native','staged','retire')):
            with self.subTest(arch=arch, mode=mode), tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary); inputs=root/'inputs'; inputs.mkdir()
                self.invoke(root, arch, None if mode=='native' else inputs, mode=='retire')

    def test_unsafe_inputs_fail_before_fixture_construction_both_arches(self):
        for arch, case in product(('amd64','arm64'), ('missing','root','tmp','cwd','home','alias','parent-alias',
                'output-parent','output-child','output-equal','private-parent','private-child','private-equal',
                'project-parent','project-equal','project-child','cache-parent','cache-child','cache-equal',
                'build','source','dependencies')):
            with self.subTest(arch=arch, case=case), tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary); inputs=root/'inputs'; inputs.mkdir(); paths={}
                if case=='missing': inputs=None
                elif case in ('root','tmp','cwd','home'): inputs={'root':Path('/'),'tmp':Path('/tmp'),'cwd':Path.cwd(),'home':Path.home()}[case]
                elif case in ('alias','parent-alias'):
                    alias=root/'alias'; alias.symlink_to(inputs if case=='alias' else root, target_is_directory=True)
                    inputs=alias if case=='alias' else alias/'inputs'
                elif case in ('build','source','dependencies'):
                    inputs=Path({'build':'/build-private/input','source':'/src/input','dependencies':'/stage-dependencies/input'}[case])
                else:
                    kind, relation=case.split('-')
                    # Inputs are respectively an ancestor, descendant or equal to the protected path.
                    if relation=='parent': paths[kind]=inputs/('project.tar' if kind=='project' else 'protected')
                    elif relation=='child':
                        paths[kind]=root/('project.tar' if kind=='project' else 'protected')
                        inputs=paths[kind]/'inputs'
                    else: paths[kind]=inputs
                    if kind=='project' and relation=='parent': paths[kind].write_bytes(b'project')
                    if kind=='output' and relation=='parent': paths[kind].mkdir()
                self.invoke(root, arch, inputs, True, unsafe=True, **paths)
