"""Runtime packaging retains standard upstream legal notice filenames."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('runtime_fpm', Path(__file__).parents[1] / 'containers/native/fpm_package.py')
fpm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fpm)


class RuntimeNoticeTests(unittest.TestCase):
    def test_credits_with_upstream_license_are_retained_without_using_generated_or_linked_notices(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            credits = root / 'CREDITS'
            credits.write_text('Upstream license grant, disclaimer and contributor credits.\n')
            generated = root / 'package/usr/share/doc/sample'
            generated.mkdir(parents=True)
            (generated / 'copyright').write_text('Generated package notice.')
            (root / 'COPYING').symlink_to('CREDITS')
            selected = fpm.legal(root, exclude={'package'})
            self.assertEqual(selected, [credits])
