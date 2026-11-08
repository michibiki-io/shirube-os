"""Source retention checks; fixtures do not establish component license clearance."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from producer.collect import files
from producer.ocaml_sources import retain,seed_download_cache


class OcamlSourceTests(unittest.TestCase):
    def test_public_source_cache_checks_content_and_excludes_git_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            base=Path(temporary);root,_,archive=self.fixture(base);destination=base/'private-cache'
            self.assertEqual(seed_download_cache(root/'download-cache',destination),1)
            target=destination/archive.relative_to(root/'download-cache')
            self.assertEqual(target.read_bytes(),archive.read_bytes())
            self.assertFalse((destination/'git').exists())
            original=archive.read_bytes();archive.write_bytes(b'changed source archive')
            with self.assertRaisesRegex(ValueError,'cache checksum mismatch'):
                seed_download_cache(root/'download-cache',destination)
            self.assertEqual(target.read_bytes(),original)
            archive.write_bytes(original);target.write_bytes(b'changed private cache')
            with self.assertRaisesRegex(ValueError,'Existing OCaml'):
                seed_download_cache(root/'download-cache',destination)

    def test_source_cache_rejects_links_and_wrong_content_addresses(self):
        with tempfile.TemporaryDirectory() as temporary:
            base=Path(temporary);cache=base/'cache';directory=cache/'sha512/ab';directory.mkdir(parents=True)
            source=base/'source';source.write_bytes(b'source fixture')
            target=directory/('ab'*64);target.symlink_to(source)
            with self.assertRaisesRegex(ValueError,'links'):
                seed_download_cache(cache,base/'output')
            target.unlink();target.write_bytes(b'changed bytes')
            with self.assertRaisesRegex(ValueError,'checksum mismatch'):
                seed_download_cache(cache,base/'output')
            self.assertEqual(seed_download_cache(base/'missing',base/'output'),0)

    def fixture(self,base):
        root=base/'opam';switch=root/'4.14.2/.opam-switch'
        for name in ['base-unix.base','vyos1x-config.0.3']:
            metadata=switch/'packages'/name;metadata.mkdir(parents=True)
            (metadata/'opam').write_text('opam-version: "2.0"\n')
        source=switch/'sources/vyos1x-config';source.mkdir(parents=True)
        (source/'LICENSE').write_text('fixture upstream license')
        (source/'config.ml').write_text('fixture source')
        content=b'original source fixture';digest=hashlib.sha256(content).hexdigest()
        archive=root/'download-cache/sha256'/digest[:2]/digest;archive.parent.mkdir(parents=True)
        archive.write_bytes(content)
        history=root/'download-cache/git/objects';history.mkdir(parents=True)
        (history/'fixture').write_bytes(b'not a source archive')
        return root,source,archive

    @patch('producer.ocaml_sources.subprocess.check_output',return_value='base-unix base\nvyos1x-config 0.3\n')
    def test_configuration_library_sources_notices_and_original_inputs_are_bound(self,_):
        with tempfile.TemporaryDirectory() as temporary:
            base=Path(temporary);root,source,archive=self.fixture(base)
            records,components=retain(root,base/'source.tar.gz',base/'work',files)
            self.assertEqual([c['name'] for c in components],['opam:vyos1x-config'])
            self.assertTrue(components[0]['sources']);self.assertTrue(components[0]['license_files'])
            self.assertTrue(any(r['member'].endswith(archive.name) for r in records['sources']))
            self.assertFalse(any('/git/' in r['member'] for group in records.values() for r in group))
            self.assertEqual(len(records['build_inputs']),3)

    @patch('producer.ocaml_sources.subprocess.check_output',return_value='base-unix base\nvyos1x-config 0.3\n')
    def test_changed_original_archive_or_missing_notice_stops_source_export(self,_):
        with tempfile.TemporaryDirectory() as temporary:
            base=Path(temporary);root,source,archive=self.fixture(base)
            original=archive.read_bytes();archive.write_bytes(b'changed original input')
            with self.assertRaisesRegex(ValueError,'Corrupt original'):
                retain(root,base/'changed.tar.gz',base/'changed',files)
            archive.write_bytes(original);(source/'LICENSE').unlink()
            with self.assertRaisesRegex(ValueError,'legal notice missing'):
                retain(root,base/'missing.tar.gz',base/'missing',files)
