from pathlib import Path
import tempfile
import unittest
from producer.bundles import bundle


class BundleTests(unittest.TestCase):
    def test_exact_reconstruction_inputs_and_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs = []
            for name, category in [('source.c','sources'), ('COPYING','license_files'), ('build.sh','build_inputs')]:
                path = root/name; path.write_text('synthetic '+name)
                inputs.append((path, name, category))
            result = bundle(inputs, root/'component.tar.gz', 0)
            self.assertEqual(set(result), {'sources','license_files','build_inputs'})
            self.assertEqual(result['sources'][0]['member'], 'source.c')
            bundle(inputs, root/'second.tar.gz', 0)
            self.assertEqual((root/'component.tar.gz').read_bytes(), (root/'second.tar.gz').read_bytes())

    def test_missing_recipe_or_traversal_blocks_collection(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); path = root/'file'; path.write_text('synthetic')
            for inputs in [[(path,'source','sources')], [(path,'../escape','sources')]]:
                with self.assertRaises(ValueError):
                    bundle(inputs, root/'invalid.tar.gz', 0)
                self.assertFalse((root/'invalid.tar.gz').exists())
