import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('install_gems',Path(__file__).resolve().parents[1]/'containers/native/install_gems.py')
installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)


class GemLockTests(unittest.TestCase):
    def test_mismatched_hash_cannot_install_any_gem(self):
        with tempfile.TemporaryDirectory() as directory:
            lock=Path(directory)/'lock.json'
            lock.write_text(json.dumps({'gems':[{'name':'fpm','version':'1.17.0',
                'url':'https://rubygems.org/downloads/fpm-1.17.0.gem','sha256':'0'*64}]}))
            response=io.BytesIO(b'tampered');response.url='https://rubygems.org/downloads/fpm-1.17.0.gem'
            with patch.object(installer.urllib.request,'urlopen',return_value=response), patch.object(installer.subprocess,'run') as install:
                with self.assertRaises(ValueError):installer.install(lock)
                install.assert_not_called()

    def test_unknown_download_origin_fails_before_fetch(self):
        with tempfile.TemporaryDirectory() as directory:
            lock=Path(directory)/'lock.json'
            lock.write_text(json.dumps({'gems':[{'name':'fpm','version':'1.17.0',
                'url':'https://untrusted.example/fpm.gem','sha256':'0'*64}]}))
            with patch.object(installer.urllib.request,'urlopen') as fetch:
                with self.assertRaises(ValueError):installer.install(lock)
                fetch.assert_not_called()
