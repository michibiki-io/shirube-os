import unittest
from producer.policy import eligible, next_version,require_stream_version
from producer.architecture import runner, native, check_elf, check_pe, check_kernel


class PolicyTests(unittest.TestCase):
    def test_only_relevant_merged_pr_is_eligible(self):
        event = {'action': 'closed', 'pull_request': {'merged': True, 'base': {'ref': 'main'}, 'merge_commit_sha': 'a'*40}}
        self.assertTrue(eligible('pull_request', event, ['producer/disks.py']))
        self.assertTrue(eligible('pull_request_target', event, ['producer/disks.py']))
        self.assertTrue(eligible('pull_request_target', event, ['license-audit.json']))
        self.assertTrue(eligible('pull_request_target', event, ['NOTICE']))
        self.assertTrue(eligible('pull_request_target', event, ['scripts/build-release.sh']))
        self.assertFalse(eligible('push', event, ['producer/disks.py']))
        # This legacy helper admits event shape; release_plan owns semantic decisions.
        self.assertTrue(eligible('pull_request', event, ['docs/build.md']))
        self.assertTrue(eligible('pull_request', event, ['new-root.conf']))
        self.assertFalse(eligible('pull_request', event, []))
        event['pull_request']['merged'] = False
        self.assertFalse(eligible('pull_request', event, ['source-lock.json']))
        event['pull_request']['merged'] = True
        event['pull_request']['base']['ref'] = 'other'
        self.assertFalse(eligible('pull_request', event, ['source-lock.json']))

    def test_stream_version_selects_baseline_and_shirube_revision(self):
        self.assertEqual(next_version(None,'2026.03'),'v2026.03.0')
        self.assertEqual(next_version('v2026.03.0','2026.03'),'v2026.03.1')
        self.assertEqual(next_version('v2026.03.9','2026.03'),'v2026.03.10')
        self.assertEqual(next_version('v2026.03.99','2026.04'),'v2026.04.0')
        self.assertEqual(next_version('v2026.12.4','2027.01'),'v2027.01.0')

    def test_stream_downgrade_or_malformed_release_is_rejected(self):
        for previous,stream in [('v2026.04.0','2026.03'),('v2026.3.0','2026.03'),
            ('v2026.03.01','2026.03'),('v2026.03.0','2026.13'),(None,'2026.3')]:
            with self.assertRaises(ValueError):next_version(previous,stream)
        with self.assertRaises(ValueError):require_stream_version('v2026.04.0','2026.03')

    def test_runner_variables_independent_without_fallback(self):
        self.assertEqual(runner('amd64'), 'ubuntu-24.04')
        self.assertEqual(runner('arm64'), 'ubuntu-24.04-arm')
        self.assertEqual(runner('arm64', 'offline-arm64'), 'offline-arm64')
        self.assertEqual(runner('amd64', '["self-hosted", "linux", "x64"]'), ['self-hosted', 'linux', 'x64'])
        for value in ['[]', '[1]', '[""]', '{}', 'x,y']:
            with self.assertRaises((ValueError, TypeError)):
                runner('arm64', value)
        with self.assertRaises(ValueError):
            native('arm64', 'x86_64')

    def test_binary_architecture_is_not_only_filename(self):
        elf = bytearray(64); elf[:6] = b'\x7fELF\x02\x01'; elf[18:20] = (183).to_bytes(2, 'little')
        check_elf(elf, 'arm64')
        with self.assertRaises(ValueError):
            check_elf(elf, 'amd64')
        pe = bytearray(256); pe[:2] = b'MZ'; pe[60:64] = (128).to_bytes(4, 'little')
        pe[128:132] = b'PE\0\0'; pe[132:134] = (0x8664).to_bytes(2, 'little')
        check_pe(pe, 'amd64')
        with self.assertRaises(ValueError):
            check_pe(pe, 'arm64')
        arm = bytearray(64); arm[56:60] = b'ARMd'; check_kernel(arm, 'arm64')
        with self.assertRaises(ValueError):
            check_kernel(arm, 'amd64')
