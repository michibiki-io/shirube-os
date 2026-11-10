"""The Bash recipe must retain tar failures without mutating its input directory."""
from pathlib import Path
import hashlib
import os
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
            original='#!/usr/bin/make -f\n'+bash_source.COMMAND+'\t -f bash-src.tar .\n'
            rules.write_text(original)
            with patch.object(bash_source,'ORIGINAL',hashlib.sha256(original.encode()).hexdigest()):
                proof=bash_source.prepare(root)
            self.assertEqual(proof['delivered_rules_sha256'],hashlib.sha256(rules.read_bytes()).hexdigest())
            work=root/'archive';work.mkdir();(work/'source.c').write_text('input\n')
            os.utime(work,(1,1))
            command=(bash_source.NOTICE+bash_source.COMMAND+'\t -f bash-src.tar .\n')
            subprocess.run(['bash','-ec',command],cwd=work,check=True,capture_output=True)
            names=subprocess.check_output(['tar','tf','bash-src.tar'],cwd=work,text=True)
            self.assertIn('./source.c',names)
            self.assertNotIn('./bash-src.tar',names)

    def test_unrecognized_and_symlink_sources_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            self.assertIsNone(bash_source.prepare(root))
            rules=root/'vyatta-bash_4.1-3+vyos2+current2/vyatta-bash/debian/rules'
            rules.parent.mkdir(parents=True);rules.write_text('different')
            with self.assertRaises(ValueError):bash_source.prepare(root)
            rules.unlink();rules.symlink_to(root/'missing')
            with self.assertRaises(ValueError):bash_source.prepare(root)
