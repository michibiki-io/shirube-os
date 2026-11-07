import unittest
from producer.policy import eligible, next_version
from producer.architecture import runner, native, check_elf, check_pe, check_kernel


class PolicyTests(unittest.TestCase):
    def test_only_relevant_merged_pr_is_eligible(self):
        event = {'action': 'closed', 'pull_request': {'merged': True, 'base': {'ref': 'main'}, 'merge_commit_sha': 'a'*40}}
        self.assertTrue(eligible('pull_request', event, ['producer/disks.py']))
        self.assertTrue(eligible('pull_request_target', event, ['producer/disks.py']))
        self.assertFalse(eligible('push', event, ['producer/disks.py']))
        self.assertFalse(eligible('pull_request', event, ['docs/build.md']))
        event['pull_request']['merged'] = False
        self.assertFalse(eligible('pull_request', event, ['source-lock.json']))
        event['pull_request']['merged'] = True
        event['pull_request']['base']['ref'] = 'other'
        self.assertFalse(eligible('pull_request', event, ['source-lock.json']))

    def test_versions_use_commits_and_minimum_patch(self):
        self.assertEqual(next_version(None, ['feat!: initial']), 'v0.1.0')
        for message in ['fix: driver', 'chore: recipe', 'unstructured message']:
            self.assertEqual(next_version('v0.1.0', [message]), 'v0.1.1')
        self.assertEqual(next_version('v0.1.0', ['feat(netbird): integrate']), 'v0.2.0')
        self.assertEqual(next_version('v1.2.3', ['fix: change\n\nBREAKING CHANGE: config']), 'v2.0.0')
        self.assertEqual(next_version('v1.2.3', ['feat!: protocol']), 'v2.0.0')
        with self.assertRaises(ValueError):
            next_version('v01.2.3', [])

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
