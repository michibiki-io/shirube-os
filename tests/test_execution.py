"""Explicit virtualization/runtime policy; fixtures are not boot evidence."""
import json
import os
import subprocess
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from producer.architecture import release_accelerator,container_runtime
from producer.guestfs_kernel import appliance_kernel


class ExecutionTests(unittest.TestCase):
    def test_no_automatic_accelerator_or_runtime_fallback(self):
        self.assertEqual(release_accelerator('amd64','tcg'),'tcg')
        self.assertEqual(release_accelerator('arm64','kvm'),'kvm')
        self.assertEqual(release_accelerator('arm64','tcg'),'tcg')
        for arch,mode in [('arm64','auto'),('amd64','auto'),('unknown','kvm')]:
            with self.assertRaises(ValueError):release_accelerator(arch,mode)
        with patch('subprocess.check_output',return_value=json.dumps({'runc':{}})) as query:
            self.assertEqual(container_runtime('runc',registered=True),'runc')
            with self.assertRaises(ValueError):container_runtime('missing',registered=True)
            self.assertTrue(all(c.args[0][1]=='info' for c in query.call_args_list))
        with self.assertRaises(ValueError):container_runtime('--runtime=other')
        error=subprocess.CalledProcessError(1,['docker','info'],stderr=b'private-context-fixture')
        with patch('subprocess.check_output',side_effect=error):
            with self.assertRaisesRegex(ValueError,'Could not inspect') as failure:
                container_runtime('runc',registered=True)
            self.assertNotIn('private-context-fixture',str(failure.exception))

    def test_preflight_tcg_does_not_require_kvm_and_conflicting_choice_fails(self):
        from producer.cli import main
        argv=['producer.cli','preflight','amd64','--qemu-accelerator','tcg','--memory-gib','3']
        with patch('sys.argv',argv),patch('producer.cli.preflight') as probe:
            main();self.assertFalse(probe.call_args.kwargs['require_kvm'])
        with patch('sys.argv',[value if value!='tcg' else 'kvm' for value in argv]),patch('producer.cli.preflight') as probe:
            main();self.assertTrue(probe.call_args.kwargs['require_kvm'])
        with patch('sys.argv',argv+['--require-kvm']),patch('producer.cli.preflight') as probe:
            with self.assertRaises(ValueError):main()
            probe.assert_not_called()

    def test_appliance_backend_is_explicit_and_environment_is_restored_on_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);packages=root/'packages';packages.mkdir()
            (packages/'linux-image-test_amd64.deb').write_bytes(b'fixture package')
            header=bytearray(0x238);header[0x202:0x206]=b'HdrS';header[0x236]=1
            def unpack(command,**kwargs):
                target=Path(command[-1]);(target/'boot').mkdir(parents=True)
                (target/'boot/vmlinuz-test').write_bytes(header)
                (target/'lib/modules/test').mkdir(parents=True)
            original_is_dir=Path.is_dir
            def directory(path):return path==Path('/lib/modules') or original_is_dir(path)
            previous=dict(os.environ)
            with patch.object(Path,'is_dir',directory),patch('producer.guestfs_kernel.subprocess.check_output',return_value='amd64\n'),patch('producer.guestfs_kernel.subprocess.run',side_effect=unpack):
                with self.assertRaisesRegex(RuntimeError,'fixture failure'):
                    with appliance_kernel(packages,'amd64',root/'appliance',accelerator='tcg') as receipt:
                        self.assertEqual(os.environ['LIBGUESTFS_BACKEND_SETTINGS'],'force_tcg')
                        self.assertEqual(receipt['accelerator'],'tcg')
                        raise RuntimeError('fixture failure')
            self.assertTrue(dict(os.environ)==previous,'Appliance context changed the process environment')
