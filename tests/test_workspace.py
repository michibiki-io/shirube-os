from pathlib import Path
import tempfile
import unittest
from producer.workspace import retire_compiler_scratch


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
