import io
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from producer.sources import extract, fetch, download
import socket
import urllib.error


class SourceTests(unittest.TestCase):
    @patch('producer.sources.time.sleep')
    def test_transient_get_retry_uses_same_input_and_discards_partial_bytes(self,_):
        class Interrupted(io.BytesIO):
            url='https://example.test/fixed-input'
            def read(self,*args):
                if self.tell():raise ConnectionResetError('transient fixture')
                return super().read(3)
        with tempfile.TemporaryDirectory() as temporary:
            target=Path(temporary)/'source'
            good=io.BytesIO(b'complete input');good.url='https://example.test/fixed-input'
            with patch('producer.sources.urllib.request.urlopen',side_effect=[Interrupted(b'partial'),good]) as request:
                download(good.url,target)
            self.assertEqual(target.read_bytes(),b'complete input')
            self.assertFalse(target.with_name('source.partial').exists())
            self.assertEqual([call.args[0].full_url for call in request.call_args_list],[good.url,good.url])

    @patch('producer.sources.time.sleep')
    def test_permission_errors_are_not_retried_and_dns_retry_is_bounded(self,sleep):
        with tempfile.TemporaryDirectory() as temporary:
            target=Path(temporary)/'source'
            denied=urllib.error.HTTPError('https://example.test/input',403,'Forbidden',{},None)
            with patch('producer.sources.urllib.request.urlopen',side_effect=denied) as request:
                with self.assertRaises(urllib.error.HTTPError):download(denied.url,target)
            self.assertEqual(request.call_count,1);sleep.assert_not_called()
            transient=urllib.error.URLError(socket.gaierror(socket.EAI_AGAIN,'transient fixture'))
            with patch('producer.sources.urllib.request.urlopen',side_effect=transient) as request:
                with self.assertRaises(urllib.error.URLError):download('https://example.test/input',target)
            self.assertEqual(request.call_count,3);self.assertEqual(sleep.call_count,2)
            self.assertFalse(target.exists());self.assertFalse(target.with_name('source.partial').exists())

    def test_exact_nested_member_selection_omits_unrelated_source_bundle(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);archive=root/'outer.tar'
            with tarfile.open(archive,'w') as source:
                for name,data in [('release/src/src.tar.gz',b'unused'),('release/tarballs/input.tar.gz',b'needed')]:
                    member=tarfile.TarInfo(name);member.size=len(data);source.addfile(member,io.BytesIO(data))
            receipt=extract(archive,root/'selected',prefixes=['release/tarballs/input.tar.gz'])
            self.assertEqual((root/'selected/release/tarballs/input.tar.gz').read_bytes(),b'needed')
            self.assertFalse((root/'selected/release/src').exists())
            self.assertEqual(receipt['selected_roots'],['release/tarballs/input.tar.gz'])

    def test_late_unsafe_member_never_publishes_partial_destination(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);archive=root/'source.tar.gz'
            with tarfile.open(archive,'w:gz') as source:
                regular=tarfile.TarInfo('source/file');regular.size=4;source.addfile(regular,io.BytesIO(b'data'))
                unsafe=tarfile.TarInfo('source/escape');unsafe.type=tarfile.SYMTYPE;unsafe.linkname='../../outside'
                source.addfile(unsafe)
            with self.assertRaises(ValueError):extract(archive,root/'extracted')
            self.assertFalse((root/'extracted').exists())
            self.assertEqual(list(root.glob('.shirube-extract-*')),[])

    def test_forward_hard_link_survives_streaming_extraction(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);archive=root/'source.tar.gz'
            with tarfile.open(archive,'w:gz') as source:
                link=tarfile.TarInfo('source/link');link.type=tarfile.LNKTYPE;link.linkname='source/file';source.addfile(link)
                regular=tarfile.TarInfo('source/file');regular.size=4;source.addfile(regular,io.BytesIO(b'data'))
            extract(archive,root/'extracted')
            self.assertEqual((root/'extracted/source/link').read_bytes(),b'data')
            self.assertEqual((root/'extracted/source/link').stat().st_ino,(root/'extracted/source/file').stat().st_ino)

    def test_explicit_generated_directory_exclusion_keeps_sources_and_safety(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); archive = root/'source.tar'
            with tarfile.open(archive, 'w') as source:
                regular = tarfile.TarInfo('source/file'); regular.size = 4
                source.addfile(regular, io.BytesIO(b'data'))
                link = tarfile.TarInfo('source/build/link'); link.type = tarfile.SYMTYPE
                link.linkname = '/outside'; source.addfile(link)
            receipt = extract(archive, root/'extracted', exclude=('source/build',))
            self.assertEqual((root/'extracted/source/file').read_bytes(), b'data')
            self.assertFalse((root/'extracted/source/build').exists())
            self.assertEqual(receipt['excluded_generated_members'], ['source/build/link'])
            with self.assertRaises(ValueError):
                extract(archive, root/'unsafe', exclude=('source/other-build',))

    def test_rejects_traversal_links_devices_before_writing(self):
        for name, kind in [('../escape', tarfile.REGTYPE), ('/absolute', tarfile.REGTYPE),
                           ('source/link', tarfile.SYMTYPE), ('source/device', tarfile.CHRTYPE)]:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary); archive = root/'source.tar'
                with tarfile.open(archive, 'w') as source:
                    member = tarfile.TarInfo(name); member.type = kind
                    if kind == tarfile.SYMTYPE:
                        member.linkname = '/outside'
                    source.addfile(member, io.BytesIO())
                with self.assertRaises(ValueError):
                    extract(archive, root/'extracted')
                self.assertFalse((root/'extracted/source/link').exists())

    def test_hash_failure_does_not_verify_signature_or_extract(self):
        with tempfile.TemporaryDirectory() as temporary:
            def download(url, target):
                target.write_bytes(b'tampered')
            with patch('producer.sources.download', side_effect=download), patch('producer.sources.subprocess.run') as run:
                with self.assertRaises(ValueError):
                    fetch({'url': 'https://example.com/source', 'sha256': '0'*64}, Path(temporary)/'result')
                run.assert_not_called()
                self.assertFalse((Path(temporary)/'result/verification.json').exists())

    def test_safe_relative_source_link_preserved_on_python_311(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);archive=root/'source.tar'
            with tarfile.open(archive,'w') as source:
                regular=tarfile.TarInfo('source/file');regular.size=4;source.addfile(regular,io.BytesIO(b'data'))
                link=tarfile.TarInfo('source/link');link.type=tarfile.SYMTYPE;link.linkname='file';source.addfile(link)
            extract(archive,root/'extracted')
            self.assertTrue((root/'extracted/source/link').is_symlink())
            self.assertEqual((root/'extracted/source/link').read_bytes(),b'data')
