import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from producer.vpp_inputs import prepare


class VppBootstrapTests(unittest.TestCase):
    @patch('producer.vpp_inputs.download',side_effect=AssertionError('Unexpected network fallback'))
    def test_fixed_bootstrap_cache_rejects_changed_or_additional_inputs(self,_):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);cache=root/'cache';cache.mkdir()
            name='meson-0.57.2.tar.gz';source=cache/name;source.write_bytes(b'fixture source')
            lock={'inputs':[{'filename':name,'sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
                'url':'https://example.test/fixed-source'}]}
            target=root/'target';prepare(lock,target,cache)
            source=target/name;source.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'hash mismatch'):prepare(lock,target,cache)
            source.write_bytes(b'fixture source');(target/'unrecorded.whl').write_bytes(b'extra')
            with self.assertRaisesRegex(ValueError,'Unexpected Python'):prepare(lock,target,cache)
