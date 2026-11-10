from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from producer.workspace import retire_compiler_scratch, retire_native_scratch


class CompilerWorkspaceTests(unittest.TestCase):
    def test_preserves_exported_kernel_factory_root_and_external_cache(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)/'work';root.mkdir()
            outside=Path(temporary)/'cache';outside.mkdir()
            (outside/'source').write_text('public source cache')
            for name in ['kernel','sources','package-output','release-rootfs']:
                (root/name).mkdir();(root/name/'retain').write_text(name)
            (root/'sources/cache').symlink_to(outside,target_is_directory=True)
            retire_compiler_scratch(root,['kernel','sources','input'])
            self.assertFalse((root/'kernel').exists())
            self.assertEqual((outside/'source').read_text(),'public source cache')
            self.assertTrue((root/'package-output/retain').is_file())
            self.assertTrue((root/'release-rootfs/retain').is_file())

    def test_invalid_selection_aborts_before_removing_anything(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);(root/'kernel').mkdir()
            (root/'alias').symlink_to(root/'kernel',target_is_directory=True)
            for invalid in ['..','../outside','package-output','release-rootfs','alias']:
                with self.assertRaises(ValueError):retire_compiler_scratch(root,['kernel',invalid])
                self.assertTrue((root/'kernel').is_dir())

    def test_native_retirement_includes_extra_assembly_and_private_caches(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            disposable = ['build-private/kernel', 'build-private/sources', 'extra/vpp',
                          'extra/kea', 'iso-work/vyos-build', 'root/.cache', 'root/go',
                          'guest-package/netbird-source-build']
            retained = ['build-private/package-output', 'build-private/release-rootfs',
                        'output', 'verified', 'library-cache', 'public-inputs', 'root/.local']
            for name in disposable + retained:
                path = root/name;path.mkdir(parents=True);(path/'payload').write_text(name)
            evidence = retire_native_scratch(root/'build-private', ['kernel'], filesystem_root=root)
            self.assertTrue(all(not (root/name).exists() for name in disposable))
            self.assertTrue(all((root/name/'payload').read_text() == name for name in retained))
            self.assertEqual(evidence['workspaces']['extra-components'], ['vpp', 'kea'])

    def test_nested_same_filesystem_mount_aborts_all_workspaces_before_deletion(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ['build-private/kernel', 'root/.cache/public']:
                (root/name).mkdir(parents=True)
            with patch('producer.workspace._mount_points', return_value=[root/'root/.cache/public']):
                with self.assertRaisesRegex(ValueError, 'mounted resource'):
                    retire_native_scratch(root/'build-private', ['kernel'], filesystem_root=root)
            self.assertTrue((root/'build-private/kernel').is_dir())
            self.assertTrue((root/'root/.cache/public').is_dir())
