"""The actionlint compatibility allowance must retain cache service validation."""
import copy
from pathlib import Path
import unittest
import yaml

from scripts import check_workflow_cache_mode as checker


class WorkflowCacheModeTests(unittest.TestCase):
    def setUp(self):
        self.workflows = {p.name: yaml.load(p.read_text(), Loader=checker.UniqueLoader)
                          for p in Path('.github/workflows').glob('*.yml')}

    def test_repository_contract_and_effective_caller_modes(self):
        checker.validate(self.workflows)
        staged = self.workflows['staged-candidate.yml']
        for caller, names in [('release.yml', ('build',)),
                              ('validate-staged.yml', ('validate', 'validate-main'))]:
            workflow = self.workflows[caller]
            for name in names:
                mode = checker.effective_modes(workflow)[name]
                modes = checker.effective_modes(staged, inherited=mode, cap=mode)
                for job, effective in modes.items():
                    self.assertEqual(effective, mode if job in checker.CACHE_JOBS else 'read')

    def test_job_override_and_explicit_read_cap_reject_overrequests_even_if_skipped(self):
        workflow = {'cache-mode': 'read', 'jobs': {'build': {'cache-mode': 'write'}}}
        self.assertEqual(checker.effective_modes(workflow), {'build': 'write'})
        for callee in [workflow, {'cache-mode': 'write', 'jobs': {}},
                       {'jobs': {'build': {'cache-mode': 'write', 'if': 'false'}}}]:
            with self.assertRaisesRegex(ValueError, 'caller read cap'):
                checker.effective_modes(callee, inherited='read', cap='read')
        self.assertEqual(checker.effective_modes({'jobs': {'build': {}}}, inherited='read', cap='read'),
                         {'build': 'read'})
        self.assertEqual(checker.effective_modes({'cache-mode': 'read', 'jobs': {'build': {}}},
                                                inherited='write', cap='write'), {'build': 'read'})

    def test_only_literal_modes_and_unique_keys_are_allowed(self):
        for mode in ('${{ github.ref }}', 'cold', 'warm', '', None, True, ['write']):
            for workflow in ({'cache-mode': mode}, {'jobs': {'build': {'cache-mode': mode}}}):
                with self.subTest(mode=mode), self.assertRaises(ValueError):
                    checker.effective_modes(workflow)
        with self.assertRaises(ValueError):
            yaml.load('cache-mode: read\ncache-mode: write\n', Loader=checker.UniqueLoader)

    def test_policy_regressions_fail_independent_validation(self):
        for filename, job, mode in [('staged-candidate.yml', None, 'read'),
                                    ('staged-candidate.yml', 'compiler', 'write'),
                                    ('staged-candidate.yml', 'kernel', 'write'),
                                    ('release.yml', 'stage', 'write'),
                                    ('validate-staged.yml', 'validate', 'write'),
                                    ('validate-staged.yml', 'validate-main', 'read')]:
            workflows = copy.deepcopy(self.workflows)
            target = workflows[filename] if job is None else workflows[filename]['jobs'][job]
            target['cache-mode'] = mode
            with self.subTest(filename=filename, job=job), self.assertRaises(ValueError):
                checker.validate(workflows)
