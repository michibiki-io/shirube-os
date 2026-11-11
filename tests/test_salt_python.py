"""Fixed Python source inputs and native compiler asset selection."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from producer.salt_python import prepare
from producer.salt_cargo import patch_sdk_environment


class SaltPythonTests(unittest.TestCase):
    def fixture(self, root):
        cache = root / 'cache'
        cache.mkdir()
        digest = lambda content: hashlib.sha256(content).hexdigest()
        source = cache / 'sample-1.0.tar.gz'
        source.write_bytes(b'fixed source archive fixture')
        record = {'name': 'pypi:sample', 'version': '1.0',
                  'url': 'https://files.pythonhosted.org/sample-1.0.tar.gz',
                  'sha256': digest(source.read_bytes())}
        lock = {'components': [record], 'ppbt_assets': {}}
        for arch, platform in [('amd64', 'x86_64'), ('arm64', 'aarch64')]:
            name = 'ppbt-0.1.8-py3-none-manylinux_2_17_' + platform + '.whl'
            data = ('compiler input fixture ' + arch).encode()
            (cache / name).write_bytes(data)
            lock['ppbt_assets'][arch] = {'name': name, 'url': 'https://files.pythonhosted.org/' + name,
                                         'sha256': digest(data), 'size': len(data)}
        path = root / 'lock.json'
        path.write_text(json.dumps(lock))
        return cache, source, path, lock

    def test_each_architecture_stages_sources_and_only_its_fixed_compiler(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache, _, path, _ = self.fixture(root)
            with patch('producer.salt_python.download') as download:
                for arch in ['amd64', 'arm64']:
                    out = prepare(path, arch, root / arch, cache)
                    records = json.loads((out / 'receipt.json').read_text())
                    self.assertEqual(len(records), 2)
                    compiler = next(r for r in records if r['name'] == 'compiler:ppbt')
                    self.assertEqual((out / compiler['file']).read_bytes(),
                                     ('compiler input fixture ' + arch).encode())
                download.assert_not_called()
            with self.assertRaises(ValueError):
                prepare(path, 'unsupported', root / 'bad', cache)

    def test_changed_existing_cache_and_wrong_platform_assets_never_fall_back(self):
        for variant in ['hash', 'link', 'platform']:
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                cache, source, path, lock = self.fixture(root)
                if variant == 'hash': source.write_bytes(b'changed')
                if variant == 'link':
                    source.unlink()
                    source.symlink_to('missing')
                if variant == 'platform':
                    lock['ppbt_assets']['amd64'] = lock['ppbt_assets']['arm64']
                    path.write_text(json.dumps(lock))
                with patch('producer.salt_python.download') as download:
                    with self.assertRaises(ValueError): prepare(path, 'amd64', root / 'out', cache)
                    download.assert_not_called()

    def test_sdk_bootstrap_and_installs_receive_local_index_policy_without_global_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / 'tools/pkg/build.py'
            path.parent.mkdir(parents=True)
            code = 'import os\n'
            for name in ['onedir_dependencies', 'salt_onedir']:
                code += ('def ' + name + '(ctx):\n    env = os.environ.copy()\n'
                         '    ctx.run("pip", "install", "relenv[toolchain]",)\n    return env\n')
            path.write_text(code)
            # Use actual upstream multiline call layout, preserving Python parsing.
            path.write_text(path.read_text().replace(
                '    ctx.run("pip", "install", "relenv[toolchain]",)',
                '    ctx.run(\n        "pip",\n        "install",\n        "relenv[toolchain]",\n    )'))
            patch_sdk_environment(root, '/task/cargo-home', '/task/python-inputs')
            namespace = {}
            exec(compile(path.read_text(), str(path), 'exec'), namespace)
            class Context:
                def run(self, *args, **kwargs): self.environment = kwargs['env']
            for name in ['onedir_dependencies', 'salt_onedir']:
                ctx = Context()
                env = namespace[name](ctx)
                self.assertIs(ctx.environment, env)
                self.assertEqual(env['PIP_NO_INDEX'], '1')
                self.assertEqual(env['PIP_FIND_LINKS'], '/task/python-inputs')
                self.assertEqual(env['CARGO_NET_OFFLINE'], 'true')
