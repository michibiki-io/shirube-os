import copy
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

from producer.bundles import bundle
from producer.reconstruction import restore
from producer.release import verify_sources
from producer.source_batches import batch
from producer.sources import sha256


class DebianSourceBatchTests(unittest.TestCase):
    def inputs(self, root, count):
        assets = []; components = []
        for number in range(count):
            data = root/f'input-{number}'; data.mkdir()
            for name, content in [('source', 'preferred source '+str(number)),
                                  ('copyright', 'complete original synthetic permission '+str(number)),
                                  ('recipe', 'build input '+str(number))]:
                (data/name).write_text(content)
            archive = root/f'component-{number}.tar.gz'
            refs = bundle([(data/'source', 'source/preferred.txt', 'sources'),
                           (data/'copyright', 'copyright', 'license_files'),
                           (data/'recipe', 'recipe', 'build_inputs')], archive, 123)
            components.append({'name': 'package'+str(number), 'version': '1', 'architecture': 'amd64',
                               'source_identity': 'a'*64, 'license': 'UNREVIEWED',
                               'redistributable': False, 'bundled_components': [], **refs})
            assets.append(archive)
        return assets, components

    def test_parts_restore_all_original_bytes_and_keep_unresolved_license_gate(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            originals, components = self.inputs(root, 3)
            parts, replaced = batch(originals, components, root, 'amd64', 'v2026.03.0', max_components=2)
            self.assertEqual(len(parts), 1)
            self.assertEqual(len(replaced), 2)
            self.assertTrue(originals[2].is_file())
            self.assertFalse(originals[0].exists())
            self.assertFalse(originals[1].exists())
            paths = [originals[2]]+parts
            inventory = {'schema_version': 1, 'components': components,
                         'shipped_packages': [{k: c[k] for k in ['name', 'version', 'architecture']}
                                              for c in components]}
            legal = root/'licenses.json'; legal.write_text(json.dumps(inventory))
            assets = [{'name': path.name, 'role': role, 'architecture': 'amd64',
                       'size': path.stat().st_size, 'sha256': sha256(path)}
                      for path, role in [(p, 'source') for p in paths]+[(legal, 'licenses')]]
            (root/'manifest.json').write_text(json.dumps({'manifest_kind': 'diagnostic-source-set',
                'project': 'Shirube OS', 'provider': 'michibiki-io', 'source_commit': 'b'*40, 'assets': assets}))
            tree = root/'restored'
            result = restore(root, tree, 'amd64', 'b'*40)
            self.assertEqual(result['source_archives'], 2)
            self.assertEqual(result['restored_regular_members'], 9)
            for number, component in enumerate(components):
                ref = component['sources'][0]
                self.assertEqual((tree/ref['asset']/ref['member']).read_text(), 'preferred source '+str(number))
            with self.assertRaisesRegex(ValueError, 'unresolved component license'):
                verify_sources(root, inventory, assets)

    def test_changed_source_bytes_fail_after_write_without_losing_inputs_or_references(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            originals, components = self.inputs(root, 2)
            expected = copy.deepcopy(components)
            entries = []
            with tarfile.open(originals[0]) as archive:
                for member in archive:
                    content = archive.extractfile(member).read()
                    if member.name == 'source/preferred.txt':
                        content = b'changed source bytes'
                    entries.append((member.name, content))
            with tarfile.open(originals[0], 'w:gz') as archive:
                for name, content in entries:
                    member = tarfile.TarInfo(name); member.size = len(content)
                    archive.addfile(member, io.BytesIO(content))
            with self.assertRaisesRegex(ValueError, 'bytes differ'):
                batch(originals, components, root, 'amd64', 'v2026.03.0')
            self.assertEqual(components, expected)
            self.assertTrue(all(p.is_file() for p in originals))
            self.assertFalse(list(root.glob('*debian-sources*.tar')))
