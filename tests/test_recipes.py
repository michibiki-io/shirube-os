import unittest
from producer.recipes import package_script


class RecipeTests(unittest.TestCase):
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
