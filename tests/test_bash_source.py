"""The Bash recipe must retain tar failures without mutating its input directory."""
from pathlib import Path
import hashlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from producer import bash_source


class BashSourceTests(unittest.TestCase):
    def test_archive_creation_does_not_change_directory_during_tar(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            rules=root/'vyatta-bash_4.1-3+vyos2+current2/vyatta-bash/debian/rules'
            rules.parent.mkdir(parents=True)
            original='#!/usr/bin/make -f\n\nbash-build:\n'+bash_source.ARCHIVE_ORIGINAL
            rules.write_text(original)
            with patch.object(bash_source,'ORIGINAL',hashlib.sha256(original.encode()).hexdigest()):
                proof=bash_source.prepare(root)
            self.assertEqual(proof['delivered_rules_sha256'],hashlib.sha256(rules.read_bytes()).hexdigest())
            work=root/'archive';work.mkdir();(work/'source.c').write_text('input\n')
            command=bash_source.ARCHIVE.replace('$(bash_src)','bash')
            subprocess.run(['bash','-ec',command],cwd=work,check=True,capture_output=True)
            self.assertEqual((work/'bash/source.c').read_bytes(),(work/'source.c').read_bytes())
            self.assertFalse((work/'bash-src.tar').exists())
            self.assertFalse((work/'bash/bash').exists())

    def test_parallel_make_waits_for_before_build(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            rules=root/'vyatta-bash_4.1-3+vyos2+current2/vyatta-bash/debian/rules'
            rules.parent.mkdir(parents=True)
            original=('SHELL=bash\n.PHONY: build before-build bash-build\n'
                      'build: before-build bash-build\n'
                      'before-build:\n\tsleep 0.1; touch ready\n'
                      '\nbash-build:\n\ttest -f ready\n'
                      'unused:\n'+bash_source.ARCHIVE_ORIGINAL)
            rules.write_text(original)
            with patch.object(bash_source,'ORIGINAL',hashlib.sha256(original.encode()).hexdigest()):
                bash_source.prepare(root)
            subprocess.run(['make','-j4','-f',str(rules),'build'],cwd=root,check=True,capture_output=True)

    def test_archive_producer_failure_is_not_hidden_by_successful_consumer(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);bin_dir=root/'bin';bin_dir.mkdir()
            source=root/'source';source.mkdir();(source/'file').write_text('input')
            archive=root/'valid.tar'
            subprocess.run(['tar','cf',str(archive),'-C',str(source),'file'],check=True)
            fake=bin_dir/'tar'
            fake.write_text('#!/bin/bash\nif [[ $1 == c ]]; then cat "'+str(archive)+'"; exit 7; fi\nexec /usr/bin/tar "$@"\n')
            fake.chmod(0o755)
            work=root/'work';work.mkdir()
            import os
            result=subprocess.run(['bash','-ec',bash_source.ARCHIVE.replace('$(bash_src)','bash')],
                cwd=work,env={**os.environ,'PATH':str(bin_dir)+':'+os.environ['PATH']},capture_output=True)
            self.assertEqual(result.returncode,7)
            self.assertEqual((work/'bash/file').read_text(),'input')

    def test_unrecognized_and_symlink_sources_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            self.assertIsNone(bash_source.prepare(root))
            rules=root/'vyatta-bash_4.1-3+vyos2+current2/vyatta-bash/debian/rules'
            rules.parent.mkdir(parents=True);rules.write_text('different')
            with self.assertRaises(ValueError):bash_source.prepare(root)
            rules.unlink();rules.symlink_to(root/'missing')
            with self.assertRaises(ValueError):bash_source.prepare(root)
