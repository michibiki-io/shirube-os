"""Synthetic shared native helpers; no package commands or native builds."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from producer import native_common


class NativeCommonTests(unittest.TestCase):
    def test_generate_substitutes_architecture_multiarch_and_copies_lock(self):
        for architecture, multiarch in (('amd64', 'x86_64-linux-gnu'),
                                        ('arm64', 'aarch64-linux-gnu')):
            with self.subTest(architecture=architecture), tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary)
                templates = base / 'native'; templates.mkdir()
                (templates / 'extras.py.in').write_text('@ARCH@ @MULTIARCH@ @ARCH@\n')
                (templates / 'sign-modules.py.in').write_text('arch=@ARCH@\n')
                (templates / 'ignored.txt').write_text('not a template')
                lock = templates / 'external-sources.lock.json'
                lock.write_bytes(b'{"synthetic": true}\n'); lock.chmod(0o640)
                output = base / 'opt/stream'

                def mapped(value):
                    return {'/opt/stream': output, '/src/native': templates}.get(str(value), Path(value))

                copy2 = native_common.shutil.copy2

                def copy_lock(source, destination):
                    return copy2(lock if source == '/src/native/external-sources.lock.json' else source, destination)

                with patch.object(native_common, 'Path', side_effect=mapped), \
                     patch('producer.component_graph.declaration', return_value={'templates': ['extras.py.in', 'sign-modules.py.in']}), \
                     patch.object(native_common.subprocess, 'check_output',
                                  return_value=multiarch+'\n') as command, \
                     patch.object(native_common.shutil, 'copy2', side_effect=copy_lock) as copy:
                    self.assertEqual(native_common.generate(architecture, stage='kernel'), output)
                command.assert_called_once_with(['dpkg-architecture', '-qDEB_HOST_MULTIARCH'], text=True)
                copy.assert_called_once_with('/src/native/external-sources.lock.json',
                                             output / 'external-sources.lock.json')
                self.assertEqual((output / 'extras.py').read_text(),
                                 f'{architecture} {multiarch} {architecture}\n')
                self.assertEqual((output / 'sign-modules.py').read_text(), f'arch={architecture}\n')
                self.assertEqual({p.name for p in output.iterdir()},
                                 {'extras.py', 'sign-modules.py', 'external-sources.lock.json'})
                self.assertEqual((output / lock.name).read_bytes(), lock.read_bytes())
                self.assertEqual((output / lock.name).stat().st_mode & 0o777, 0o640)

    def test_run_checks_command_and_forwards_options_and_result(self):
        with patch.object(native_common.subprocess, 'run') as command:
            result = native_common.run('python3', 'extras.py', 'base', cwd='/synthetic', text=True)
        command.assert_called_once_with(('python3', 'extras.py', 'base'),
                                        check=True, cwd='/synthetic', text=True)
        self.assertIs(result, command.return_value)

    def test_run_preserves_command_failure(self):
        failure = subprocess.CalledProcessError(1, ('synthetic',))
        with patch.object(native_common.subprocess, 'run', side_effect=failure), \
             self.assertRaises(subprocess.CalledProcessError) as raised:
            native_common.run('synthetic')
        self.assertIs(raised.exception, failure)
