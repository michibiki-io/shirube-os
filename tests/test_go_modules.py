from pathlib import Path
import tempfile
import unittest
import warnings
import zipfile
from producer.go_modules import module_hash,build_info


class GoModuleSourceTests(unittest.TestCase):
    def test_actual_replacement_identity_overrides_import_path(self):
        info=build_info('/client: go1.26.10\n\tmod\texample.test/client\tv1.0.0\n'
            '\tdep\texample.test/original\tv1.0.0\n'
            '\t=>\texample.test/replacement\tv1.2.0\th1:'+'A'*43+'=\n'
            '\tbuild\tvcs.revision=public-commit\n')
        self.assertEqual(info['compiler'],'1.26.10')
        self.assertEqual(info['modules'][0]['module'],'example.test/replacement')
        self.assertEqual(info['modules'][0]['original_module'],'example.test/original')
        self.assertEqual(info['settings']['vcs.revision'],'public-commit')

    def test_missing_sum_or_local_dependency_cannot_claim_correspondence(self):
        for line in ['\tdep\texample.test/module\tv1.0.0\n',
                     '\tdep\texample.test/module\t(devel)\n',
                     '\t=>\texample.test/module\tv1.0.0\n']:
            with self.assertRaises(ValueError):build_info('/client: go1.26.10\n'+line)

    def test_source_identity_ignores_zip_encoding_and_detects_content_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);files={'example.test/mod@v1.0.0/go.mod':b'module example.test/mod\n',
                'example.test/mod@v1.0.0/file.go':b'package mod\n'}
            for name,method in [('stored',zipfile.ZIP_STORED),('deflated',zipfile.ZIP_DEFLATED)]:
                with zipfile.ZipFile(root/name,'w',compression=method) as archive:
                    for path,data in files.items():archive.writestr(path,data)
            expected=module_hash(root/'stored','example.test/mod','v1.0.0')
            self.assertEqual(expected,module_hash(root/'deflated','example.test/mod','v1.0.0'))
            with zipfile.ZipFile(root/'modified','w') as archive:
                for path,data in files.items():archive.writestr(path,data+b'// changed\n')
            self.assertNotEqual(expected,module_hash(root/'modified','example.test/mod','v1.0.0'))

    def test_duplicate_or_escaping_module_members_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for names in [['example.test/mod@v1.0.0/../escape'],['example.test/mod@v1.0.0/file']*2]:
                with warnings.catch_warnings():
                    warnings.simplefilter('ignore',UserWarning)
                    with zipfile.ZipFile(root/'source.zip','w') as archive:
                        for name in names:archive.writestr(name,b'data')
                with self.assertRaises(ValueError):module_hash(root/'source.zip','example.test/mod','v1.0.0')
