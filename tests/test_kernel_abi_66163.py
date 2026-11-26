"""Issue #54's ABI gates and exact effective kernel patch replay."""
import hashlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
ABI = {'kernel_release': '6.6.163-vyos', 'package_version': '6.6.163-1'}
NATIVE = ROOT / 'containers/native'
SERIES = ROOT / 'patches/kernel/linux-6.6.163'


def load_native(filename):
    loader = importlib.machinery.SourceFileLoader(filename, str(NATIVE / filename))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(filename, loader))
    loader.exec_module(module)
    return module


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


class KernelABI(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.work = Path(workspace.name)
        self.image = load_native('image.py')

    def test_shell_checks_real_makefile_version_and_consistent_labels(self):
        script = (NATIVE / 'package_kernel.sh').read_text().split('# @COMMON@')[0]
        (self.work / 'Makefile').write_text('kernelversion:\n\t@echo 6.6.163\n')
        env = {**os.environ, 'SHIRUBE_KERNEL_VERSION':'6.6.163',
               'SHIRUBE_KERNEL_RELEASE':'6.6.163-vyos', 'SHIRUBE_KERNEL_PACKAGE_VERSION':'6.6.163-1'}
        def run(overrides):
            return subprocess.run(['bash', '-euc', script+'\ncheck_kernel_version'], cwd=self.work,
                                  env={**env, **overrides}, capture_output=True).returncode
        self.assertEqual(run({}), 0)
        self.assertNotEqual(run({'SHIRUBE_KERNEL_PACKAGE_VERSION':'6.6.128-1'}), 0)
        self.assertNotEqual(run({'SHIRUBE_KERNEL_RELEASE':'6.6.128-vyos'}), 0)
        (self.work / 'Makefile').write_text('kernelversion:\n\t@echo 6.6.128\n')
        self.assertNotEqual(run({}), 0)

    def test_image_defaults_and_grub_pair(self):
        defaults = self.work / 'defaults.toml'
        defaults.write_text('kernel_version = "6.6.128"\nkernel_flavor = "vyos"\n')
        self.image.configure_kernel_abi(defaults,'6.6.163')
        self.assertEqual(defaults.read_text(), 'kernel_version = "6.6.163"\nkernel_flavor = "vyos"\n')
        with self.assertRaises(ValueError):
            self.image.configure_kernel_abi(defaults,'6.6.163')
        grub = self.work / 'boot/grub/grub.cfg'
        grub.parent.mkdir(parents=True)
        grub.write_text('linux /live/vmlinuz-6.6.163-vyos console=ttyS0\ninitrd /live/initrd.img-6.6.163-vyos\n')
        self.image.verify_live_grub(self.work,ABI['kernel_release'])
        grub.write_text(grub.read_text().replace('initrd.img-6.6.163','initrd.img-6.6.128'))
        with self.assertRaisesRegex(ValueError,'wrong kernel ABI'):
            self.image.verify_live_grub(self.work,ABI['kernel_release'])

    @unittest.skipUnless(shutil.which('dpkg-deb'),'dpkg-deb required')
    def test_iso_kernel_package_metadata_not_just_filename(self):
        debs = self.work / 'debs'
        debs.mkdir()
        root = self.work / 'root'
        (root / 'DEBIAN').mkdir(parents=True)
        control = root / 'DEBIAN/control'
        artifact = debs / 'linux-image-6.6.163-vyos_6.6.163-1_amd64.deb'
        def deb(version):
            control.write_text('Package: linux-image-6.6.163-vyos\nVersion: '+version+
                               '\nArchitecture: amd64\nMaintainer: Fixture <fixture@example.invalid>\nDescription: Test\n')
            subprocess.run(['dpkg-deb','--root-owner-group','-b',str(root),str(artifact)],
                           check=True, capture_output=True)
        deb('6.6.163-1')
        self.assertEqual(self.image.live_kernel_image(debs,'amd64',ABI),artifact)
        deb('6.6.128-1')
        with self.assertRaisesRegex(ValueError,'Wrong kernel package ABI'):
            self.image.live_kernel_image(debs,'amd64',ABI)

    def test_module_vermagic_rejects_old_release_and_missing_modversions(self):
        sign=load_native('sign-modules.py.in')
        module=self.work / 'foo.ko'
        with patch.object(sign.subprocess,'check_output',return_value='6.6.163-vyos SMP modversions '):
            sign.verify_vermagic(module,ABI['kernel_release'])
        for bad in ('6.6.128-vyos SMP modversions', '6.6.163-vyos SMP', ''):
            with patch.object(sign.subprocess,'check_output',return_value=bad):
                with self.assertRaisesRegex(ValueError,'Module vermagic'):
                    sign.verify_vermagic(module,ABI['kernel_release'])

    @unittest.skipUnless(os.environ.get('SHIRUBE_TEST_KERNEL_TAR'),
                         'optional exact kernel.org source for selected-file replay')
    def test_effective_patch_series_on_exact_upstream_tar(self):
        source = self.work / 'source'
        source.mkdir()
        archive = Path(os.environ['SHIRUBE_TEST_KERNEL_TAR'])
        lock=json.loads((ROOT / 'source-lock.json').read_text())['kernel']
        self.assertEqual(sha(archive), lock['sha256'])
        files={line.split()[1] for line in (SERIES / 'effective-files.sha256').read_text().splitlines()}
        remaining=set(files)
        with tarfile.open(archive,mode='r|*') as tar:
            for member in tar:
                name=member.name.removeprefix(lock['source_root']+'/')
                if name not in remaining:
                    continue
                self.assertTrue(member.isfile())
                target=source / name
                target.parent.mkdir(exist_ok=True,parents=True)
                target.write_bytes(tar.extractfile(member).read())
                remaining.remove(name)
        self.assertFalse(remaining)
        stream = self.work / 'stream'
        stream.mkdir()
        with tarfile.open(ROOT / 'tests/fixtures/security/kernel-original.tar.gz') as tar:
            for member in tar.getmembers():
                if member.isfile() and member.name.startswith('stream-patches/'):
                    (stream / Path(member.name).name).write_bytes(tar.extractfile(member).read())
        script = (NATIVE / 'package_kernel.sh').read_text().split('# @COMMON@')[0]
        script = script.replace('/kernel-patches/linux-6.6.163',str(SERIES))
        def run(directory):
            return subprocess.run(['bash','-euo','pipefail','-c',script+'\napply_kernel_patches "$@"',
                                   'bash',str(stream),str(self.work/'state'),str(self.work/'local')],
                                  cwd=directory,capture_output=True,text=True)
        copy=self.work/'good'
        shutil.copytree(source,copy)
        result=run(copy)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('upstream-equivalent v4-0001-',(self.work/'state').read_text())
        perf=Path('tools/perf/util/jitdump.c')
        original=(source/perf).read_bytes()
        old_header=b'#include <string.h>\n'
        self.assertEqual(original.count(old_header),1)
        self.assertEqual((copy/perf).read_bytes(),
                         original.replace(old_header,b'#include <linux/string.h>\n'))
        self.assertIn('applied-shirube 0002-perf-jitdump-use-tools-string-header.patch',
                      (self.work/'state').read_text())
        # Unrelated contents are still checked when the one-line patch applies.
        changed_perf=self.work/'changed-perf'
        shutil.copytree(source,changed_perf)
        (changed_perf/perf).write_bytes(original+b'\n')
        failed=run(changed_perf)
        self.assertNotEqual(failed.returncode,0)
        self.assertIn('tools/perf/util/jitdump.c: FAILED',failed.stdout)
        # Reject a modified effective source file even if every patch still applies.
        unexpected=self.work/'unexpected'
        shutil.copytree(source,unexpected)
        target=source/'net/ipv4/ip_fragment.c'
        target.write_bytes(target.read_bytes()+b'\n')
        failed=run(source)
        self.assertNotEqual(failed.returncode,0)
        self.assertIn('net/ipv4/ip_fragment.c: FAILED', failed.stdout)
        (stream / 'unknown.patch').touch()
        self.assertNotEqual(run(unexpected).returncode,0)
