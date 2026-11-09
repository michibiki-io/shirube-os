"""Download retries must stay bounded and preserve non-network failures."""
from pathlib import Path
import subprocess
import tempfile
import unittest

from producer.debootstrap import RETRY_FUNCTION, configure_retry


class DebootstrapTests(unittest.TestCase):
    def test_only_expected_debootstrap_call_sites_are_modified(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'functions'
            original = 'wget $@ 2>&1\nwget ${NVSWITCH:+"$NVSWITCH"} "$@"\n'
            path.write_text(original)
            configure_retry(path)
            patched = path.read_text()
            self.assertTrue(patched.startswith(RETRY_FUNCTION))
            self.assertIn('shirube_wget "$@" 2>&1', patched)
            with self.assertRaises(ValueError): configure_retry(path)
            self.assertEqual(path.read_text(), patched)
            path.write_text(original.replace('wget $@', 'changed $@'))
            with self.assertRaises(ValueError): configure_retry(path)
