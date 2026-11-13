import unittest
from producer.recipes import package_script


class RecipeTests(unittest.TestCase):
    def test_physical_stage_scripts_assemble_and_preserve_native_kernel_boundaries(self):
        from pathlib import Path
        import subprocess
        from producer.recipes import component_script
        native = Path(__file__).resolve().parents[1]/'containers/native'
        for stage in ('native','kernel'):
            script=component_script(native,stage)
            self.assertNotIn('# @COMMON@',script);self.assertNotIn('# @VALIDATE@',script)
            self.assertNotIn('# @EARLY@',script)
            result=subprocess.run(['bash','-n'],input=script,text=True,capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('found=0',script)
            if stage=='native':
                self.assertNotIn('package_kernel()',script)
                self.assertLess(script.index('DEB_ARCH='),script.index('exec python3 /src/native/http_api.py'))
                self.assertLess(script.index('exec python3 /src/native/http_api.py'),script.index('mkdir /output/source'))
                self.assertIn('security_native.py update-check',script)
            else:
                self.assertNotIn('security_native',script)
                self.assertIn('CONFIG_MODULE_SIG_FORCE=y',script)
                self.assertIn('KBUILD_BUILD_USER=michibiki-io',script)

    def test_legacy_full_build_uses_the_same_native_preparation_and_receipt_hooks(self):
        import ast
        from pathlib import Path
        text=(Path(__file__).resolve().parents[1]/'containers/native/build_release.py').read_text()
        tree=ast.parse(text)
        imported={alias.name for node in ast.walk(tree) if isinstance(node,ast.ImportFrom)
                  and node.module=='producer.compile_native' for alias in node.names}
        self.assertEqual(imported,{'prepare','receipt'})
        self.assertLess(text.index('prepare_native(jobs, sources, args.architecture)'),text.index('cached=restore('))
        self.assertIn('native_receipt(job, output, exported)',text)

    def test_build_launcher_routes_both_stage_spellings_to_compile_engine(self):
        import importlib.util
        import sys
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        spec = importlib.util.spec_from_file_location('native_build_launcher', 'containers/native/build.py')
        launcher = importlib.util.module_from_spec(spec); spec.loader.exec_module(launcher)
        for arguments, compiled in [(['amd64', '--stage', 'vpp'], True),
                                    (['arm64', '--stage=kernel'], True), (['amd64'], False)]:
            compile_main, release_main = Mock(), Mock()
            with patch.dict(sys.modules, {'build_compile': SimpleNamespace(main=compile_main),
                                          'build_release': SimpleNamespace(main=release_main)}), \
                 patch.object(sys, 'argv', ['build.py', *arguments]):
                launcher.main()
            self.assertEqual(compile_main.call_count, int(compiled))
            self.assertEqual(release_main.call_count, int(not compiled))

    def test_source_root_does_not_rewrite_output_receipt_filename(self):
        script='git rev-parse HEAD > /output/snapshot-git-commit.txt\ncp -a /snapshot/. /output/source/\n'
        rendered=package_script(script,'/build/job','/sources/core','/sources/builder','/deps')
        self.assertEqual(rendered,'git rev-parse HEAD > /build/job/snapshot-git-commit.txt\ncp -a /sources/core/. /build/job/source/\n')

    def test_replacements_are_not_processed_a_second_time(self):
        rendered=package_script('cd /output\nfind /snapshot -type f\ncfg=/stream-build/scripts\nls /local-debs/*.deb\n',
            '/private/snapshot/output','/private/output/snapshot','/private/stream-build','/private/local-debs')
        self.assertEqual(rendered,'cd /private/snapshot/output\nfind /private/output/snapshot -type f\ncfg=/private/stream-build/scripts\nls /private/local-debs/*.deb\n')

    def test_unsafe_shell_path_is_rejected(self):
        with self.assertRaises(ValueError):
            package_script('cd /output','/tmp/unsafe;command','/source','/builder','/deps')
