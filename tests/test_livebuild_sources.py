"""Synthetic parser/generator fixtures only; never modify installed host files."""
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from producer import livebuild_sources as sources


PROJECT = Path(__file__).resolve().parents[1]
BASE = 'https://snapshot.debian.org/archive/debian/20261105T000000Z/'
SECURITY = BASE.replace('/debian/', '/debian-security/')
# Original synthetic fixture, not a copy of the upstream implementation.
GENERATOR = '''# Synthetic generator notice: retained by setup.
Create_apt_sources_list ()
{
    case "$1" in
        chroot) base="$LB_PARENT_MIRROR_CHROOT"; security="$LB_PARENT_MIRROR_CHROOT_SECURITY" ;;
        binary) base="$LB_PARENT_MIRROR_BINARY"; security="$LB_PARENT_MIRROR_BINARY_SECURITY" ;;
        *) exit 8 ;;
    esac
    local PARENT_LIST_FILE="chroot/etc/apt/sources.list"
    : > "$PARENT_LIST_FILE"
    for suite in bookworm bookworm-security bookworm-updates bookworm-backports; do
        mirror="$base"
        if [ "$suite" = bookworm-security ]; then mirror="$security"; fi
        for kind in deb deb-src; do
            echo "$kind $mirror $suite main contrib" >> "$PARENT_LIST_FILE"
        done
    done
\t# Disable deb-src entries?
    if [ "${2:-}" != source ] && [ "$LB_APT_SOURCE_ARCHIVES" != true ]; then
        sed -i 's/^deb-src/#deb-src/' "$PARENT_LIST_FILE"
    fi
}
'''


def shell_environment(arch, scheme='http', source_archives='false'):
    env = dict(os.environ, LB_ARCHITECTURES=arch, LB_MODE='debian',
               LB_DERIVATIVE='false', LB_SECURITY='true', LB_UPDATES='true',
               LB_BACKPORTS='true', LB_APT_SOURCE_ARCHIVES=source_archives,
               LB_PARENT_ARCHIVE_AREAS='main contrib', LB_ARCHIVE_AREAS='main')
    for mode in ('CHROOT', 'BINARY'):
        env['LB_PARENT_MIRROR_'+mode] = BASE.replace('https', scheme, 1)
        env['LB_PARENT_MIRROR_'+mode+'_SECURITY'] = SECURITY.replace('https', scheme, 1)
        env['LB_PARENT_DISTRIBUTION_'+mode] = 'bookworm'
        env['LB_MIRROR_'+mode] = 'http://127.0.0.1:18080'
        env['LB_MIRROR_'+mode+'_SECURITY'] = 'http://127.0.0.1:18080'
        env['LB_DISTRIBUTION_'+mode] = 'circinus'
    return env


# All forms in bootstrap_archives and chroot_archives (install/remove).
CALLS = ('chroot', 'chroot chroot', 'chroot binary', 'chroot source',
         'binary chroot', 'binary binary', 'binary source')


def exercise_generator(path, root, arch, scheme='http', source_archives='false'):
    """Execute patched shell code with a checking Apt stub, no network/packages."""
    (root/'chroot/etc/apt/sources.list.d').mkdir(parents=True, exist_ok=True)
    parent = 'sources.list'
    checker = root/'check.py'
    checker.write_text('''from pathlib import Path
import sys
p = Path(sys.argv[1])
lines = p.read_text().splitlines()
assert len(lines) == 8, lines
for line in lines:
    assert '[check-valid-until=no]' in line, line
    assert line.count('check-valid-until=') == 1, line
assert {line.split('] ')[1].split()[1] for line in lines} == {
    'bookworm', 'bookworm-security', 'bookworm-updates', 'bookworm-backports'}
if sys.argv[2] == 'source' or sys.argv[3] == 'true':
    assert sum(line.startswith('deb-src ') for line in lines) == 4
else:
    assert sum(line.startswith('#deb-src ') for line in lines) == 4
''')
    script = '. '+shlex.quote(str(path))+'\n'
    script += 'Apt () { python3 '+shlex.quote(str(checker))+' '+shlex.quote('chroot/etc/apt/'+parent)+' "$pass" "$LB_APT_SOURCE_ARCHIVES" || exit $?; }\n'
    for call in CALLS:
        script += 'pass='+shlex.quote(call.split()[1] if ' ' in call else '')+'\n'
        script += 'Create_apt_sources_list '+call+'\nApt chroot update\n'
    result = subprocess.run(['sh', '-c', script], cwd=root,
                            env=shell_environment(arch, scheme, source_archives),
                            capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise AssertionError(result.stderr + result.stdout)
    apt = root/'chroot/etc/apt'
    assert not (apt/'apt.conf').exists()
    assert not (apt/'apt.conf.d').exists()
    return len(CALLS)


class SourcesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='snapshot sources ')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.lock = self.root/'lock.json'
        self.lock.write_text(json.dumps({'debian_snapshot': BASE, 'debian_security_snapshot': SECURITY}))
        self.path = self.root/'sources.list'

    def rewrite(self, text):
        self.path.write_bytes(text.encode())
        sources.rewrite_sources(self.path, self.lock)
        return self.path.read_bytes().decode()

    def test_exact_roots_suites_schemes_options_and_idempotence(self):
        for uri in (BASE, SECURITY):
            for scheme in ('http', 'https'):
                for kind in ('deb', 'deb-src'):
                    for suite in ('bookworm', 'bookworm-security', 'bookworm-updates', 'bookworm-backports'):
                        with self.subTest(uri=uri, scheme=scheme, kind=kind, suite=suite):
                            line = f' \t{kind}\t[arch=amd64,arm64 signed-by=/keys/archive.gpg] {uri.replace("https", scheme)} {suite} main contrib # retained\r\n'
                            result = self.rewrite(line)
                            self.assertEqual(result, line.replace('archive.gpg]', 'archive.gpg check-valid-until=no]'))
                            self.assertEqual(self.rewrite(result), result)
        self.assertEqual(self.rewrite(f'deb {BASE} bookworm main'), f'deb [check-valid-until=no] {BASE} bookworm main')
        correct = f'deb [check-valid-until=no arch+=arm64] {BASE} bookworm main\n'
        self.assertEqual(self.rewrite(correct), correct)

    def test_unrelated_entries_and_comments_are_byte_preserved(self):
        uris = ('http://127.0.0.1:18080', 'file:/local', BASE.replace('20261105', '20261104'),
                BASE.replace('snapshot.debian.org', 'snapshot.debian.org.evil'),
                BASE+'extra', BASE.rstrip('/'), BASE+'?query=1',
                'https://deb.debian.org/debian', 'https://example.org/'+BASE)
        text = '# retained '+BASE+'\n\n'+f'#deb-src {BASE} bookworm main\n'
        text += ''.join(f'deb [broken options] {uri} bookworm main # {BASE}\n' for uri in uris)
        text += f'deb https://example.org/debian bookworm main {BASE}\n'
        text += f'deb [signed-by={BASE}] https://example.org/debian bookworm main\n'
        self.assertEqual(self.rewrite(text), text)

    def test_invalid_target_fails_without_partial_rewrite(self):
        bad = ('[check-valid-until=yes]', '[check-valid-until=no check-valid-until=no]',
               '[Check-Valid-Until=no]', '[check-valid-until+=yes]',
               '[check-valid-until-=no]', '[arch amd64]', '[]', '[arch=amd64',
               '[arch=amd64]]', '[check-valid-until=]', '[arch==amd64]')
        for option in bad:
            with self.subTest(option=option):
                text = f'deb {BASE} bookworm main\ndeb-src {option} {SECURITY} bookworm-security main\n'
                self.path.write_text(text)
                with self.assertRaises(ValueError): sources.rewrite_sources(self.path, self.lock)
                self.assertEqual(self.path.read_text(), text)
        for tail in ('', ' bookworm', ' #missing suite'):
            text = f'deb {BASE}{tail}\n'
            self.path.write_text(text)
            with self.assertRaises(ValueError): sources.rewrite_sources(self.path, self.lock)
            self.assertEqual(self.path.read_text(), text)

    def install(self, text=GENERATOR):
        path = self.root/'live/build/functions/sourcelist.sh'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def configure_fixture(self, path, expected=GENERATOR):
        with patch.object(sources.subprocess, 'check_output', return_value=str(path)+'\n') as query, \
                patch.object(sources, 'SOURCELIST_SHA256', hashlib.sha256(expected.encode()).hexdigest()):
            sources.configure_sources(self.lock)
        query.assert_called_once_with(['dpkg-query', '--listfiles', 'live-build'], text=True)

    def test_shell_hook_all_regenerations_both_architectures(self):
        path = self.install()
        self.configure_fixture(path)
        result = path.read_text()
        self.assertTrue(result.startswith('# Synthetic generator notice: retained by setup.\n'))
        self.assertEqual(result.count('Modified by michibiki-io:'), 1)
        self.assertEqual(result[result.index(sources.ANCHOR):],
                         GENERATOR[GENERATOR.index(sources.ANCHOR):])
        for arch in ('amd64', 'arm64'):
            for scheme in ('http', 'https'):
                for archives in ('true', 'false'):
                    with self.subTest(arch=arch, scheme=scheme, archives=archives):
                        self.assertEqual(exercise_generator(path, self.root, arch, scheme, archives), 7)
        with self.assertRaises(ValueError): self.configure_fixture(path)
        self.assertEqual(path.read_text(), result)

    def test_discovery_missing_ambiguous_relative_and_query_failure(self):
        path = self.install()
        for output in ('', '/unexpected/sourcelist.sh\n', str(path)+'\n'+str(path)+'\n',
                       'live/build/functions/sourcelist.sh\n'):
            with self.subTest(output=output), patch.object(sources.subprocess, 'check_output', return_value=output):
                with self.assertRaises(ValueError): sources.configure_sources(self.lock)
                self.assertEqual(path.read_text(), GENERATOR)
        with patch.object(sources.subprocess, 'check_output', side_effect=subprocess.CalledProcessError(1, 'dpkg-query')):
            with self.assertRaises(subprocess.CalledProcessError): sources.configure_sources(self.lock)
        path.unlink()
        with self.assertRaises(FileNotFoundError): self.configure_fixture(path)

    def test_anchor_and_implementation_guards_leave_input_unchanged(self):
        for text in (GENERATOR.replace(sources.ANCHOR, ''), GENERATOR+sources.ANCHOR,
                     GENERATOR.replace(sources.ANCHOR, '\t# Changed anchor\n'),
                     GENERATOR.replace('Create_apt_sources_list ()', 'Different_function ()')):
            path = self.install(text)
            with self.assertRaises(ValueError): self.configure_fixture(path, expected=text)
            self.assertEqual(path.read_text(), text)
        path = self.install(GENERATOR+'# unexpected change\n')
        with self.assertRaises(ValueError): self.configure_fixture(path)
        self.assertEqual(path.read_text(), GENERATOR+'# unexpected change\n')

    def test_hook_failure_prevents_apt_even_without_set_e(self):
        path = self.install()
        self.configure_fixture(path)
        (self.root/'chroot/etc/apt').mkdir(parents=True)
        for failure in ('malformed', 'missing-lock'):
            env = shell_environment('amd64')
            if failure == 'malformed':
                env['LB_PARENT_MIRROR_CHROOT_SECURITY'] = '[check-valid-until=yes] '+SECURITY
            else:
                self.lock.unlink()
            script = '. '+shlex.quote(str(path))+'\nCreate_apt_sources_list chroot\ntouch apt-was-run\n'
            result = subprocess.run(['sh', '-c', script], cwd=self.root, env=env, capture_output=True, timeout=15)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((self.root/'apt-was-run').exists())
            if failure == 'malformed':
                self.assertNotIn('[check-valid-until=no]', (self.root/'chroot/etc/apt/sources.list').read_text())

    def test_assemble_prepares_before_work_for_both_architectures(self):
        spec = importlib.util.spec_from_file_location('image_sources_test', PROJECT/'containers/native/image.py')
        image = importlib.util.module_from_spec(spec)
        with patch.object(sys, 'path', list(sys.path)):
            spec.loader.exec_module(image)
        for arch in ('amd64', 'arm64'):
            events = []
            def stop_generate(actual):
                self.assertEqual(actual, arch)
                events.append('generate')
                raise RuntimeError('stop before container filesystem work')
            with patch('producer.debootstrap.configure_retry', side_effect=lambda: events.append('retry')), \
                    patch.object(sources, 'configure_sources', side_effect=lambda: events.append('sources')), \
                    patch.object(image, 'generate', side_effect=stop_generate):
                with self.assertRaisesRegex(RuntimeError, 'stop before'):
                    image.assemble(None, None, arch, 'v1', None)
            self.assertEqual(events, ['retry', 'sources', 'generate'])
        # Existing tests execute the canonical builder; this assertion binds setup
        # to that same assemble function without invoking any host preparation.
        tree = ast.parse((PROJECT/'containers/native/image.py').read_text())
        assemble = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'assemble')
        self.assertTrue(any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                            and node.func.id == 'run_image_builder' for node in ast.walk(assemble)))
