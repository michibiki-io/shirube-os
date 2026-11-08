import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from producer.vpp_inputs import prepare,prepare_external


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

    def test_native_input_error_pages_and_corrupt_cache_fail_without_fallback(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);cache=root/'cache';cache.mkdir()
            name='rdma-core-58.0.tar.gz';data=b'fixed upstream archive'
            (cache/name).write_bytes(data)
            lock={'inputs':[{'filename':name,'sha256':hashlib.sha256(data).hexdigest(),
                             'url':'https://example.test/rdma-core-58.0.tar.gz'}]}
            with patch('producer.vpp_inputs.download',side_effect=AssertionError('Unexpected fallback')):
                prepare_external(lock,root/'good',cache)
                self.assertEqual((root/'good'/name).read_bytes(),data)
                (cache/name).write_text('<html>504 Gateway Time-out</html>')
                with self.assertRaisesRegex(ValueError,'hash mismatch'):
                    prepare_external(lock,root/'corrupt',cache)
            def bad_response(url,target):
                Path(target).write_text('<html>504 Gateway Time-out</html>')
            with patch('producer.vpp_inputs.download',side_effect=bad_response):
                with self.assertRaisesRegex(ValueError,'hash mismatch'):
                    prepare_external(lock,root/'error-page')
            lock['inputs'][0]['url']='http://example.test/rdma-core-58.0.tar.gz'
            with self.assertRaisesRegex(ValueError,'Invalid fixed'):
                prepare_external(lock,root/'insecure')
