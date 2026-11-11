from pathlib import Path
import tempfile
import unittest
from producer.notices import NOTICE, mark_changed, snapshot


class ModificationNoticeTests(unittest.TestCase):
    def test_changed_upstream_headers_preserved_and_unchanged_file_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            script=root/'display.py'; template=root/'boot.j2'; unchanged=root/'config.toml'
            original='#!/usr/bin/python3\n# Copyright upstream; GPL-2.0-or-later\nprint("Upstream")\n'
            script.write_text(original);template.write_text('Upstream\n');unchanged.write_text('name="Upstream"\n')
            before=snapshot(root)
            script.write_text(original.replace('print("Upstream")','print("Shirube OS")'))
            template.write_text('Shirube OS\n')
            self.assertEqual(set(mark_changed(before)),{script,template})
            self.assertTrue(script.read_text().startswith('#!/usr/bin/python3\n# '+NOTICE+'\n'))
            self.assertIn('# Copyright upstream; GPL-2.0-or-later',script.read_text())
            self.assertTrue(template.read_text().startswith('{# '+NOTICE+' #}\n'))
            self.assertNotIn(NOTICE,unchanged.read_text())
            mark_changed(before)
            self.assertEqual(script.read_text().count(NOTICE),1)
