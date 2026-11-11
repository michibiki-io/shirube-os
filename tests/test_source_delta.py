"""Reject source-cache additions with mismatched inputs before network access."""
import json
from pathlib import Path
import runpy
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch


class SourceDeltaTests(unittest.TestCase):
    def invoke(self, root, rows, archive_commit='a'*40):
        script=Path(__file__).resolve().parents[1]/'scripts/collect-source-delta.py'
        (root/'delta.json').write_text(json.dumps({'added':rows}))
        (root/'notices').mkdir();(root/'output').mkdir()
        with tarfile.open(root/'project.tar','w',format=tarfile.PAX_FORMAT,
                          pax_headers={'comment':archive_commit}) as archive:
            archive.addfile(tarfile.TarInfo('fixture'))
        argv=[str(script),'--delta',str(root/'delta.json'),'--notices',str(root/'notices'),
              '--project-source',str(root/'project.tar'),'--commit','a'*40,
              '--architecture','amd64','--version','v2026.03.0',
              '--output',str(root/'output'),'--work',str(root/'work')]
        with patch.object(sys,'argv',argv):
            runpy.run_path(str(script))['main']()

    def test_stock_cloud_init_cannot_be_mapped_to_native_fork_sources(self):
        with tempfile.TemporaryDirectory() as temporary,patch('subprocess.run') as command:
            with self.assertRaisesRegex(ValueError,'binary/source identity differs'):
                self.invoke(Path(temporary),{'cloud-init':['22.4-1','all','cloud-init','22.4-1']})
            command.assert_not_called()

    def test_wrong_architecture_never_downloads_or_compiles(self):
        with tempfile.TemporaryDirectory() as temporary,patch('subprocess.run') as command:
            with self.assertRaisesRegex(ValueError,'Invalid exact package addition'):
                self.invoke(Path(temporary),{'ifupdown':['0.8.41','arm64','ifupdown','0.8.41']})
            command.assert_not_called()

    def test_stale_project_archive_never_downloads_or_compiles(self):
        with tempfile.TemporaryDirectory() as temporary,patch('subprocess.run') as command:
            with self.assertRaisesRegex(ValueError,'Project source commit differs'):
                self.invoke(Path(temporary),{'ifupdown':['0.8.41','amd64','ifupdown','0.8.41']},'b'*40)
            command.assert_not_called()
