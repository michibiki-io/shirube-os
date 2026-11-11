"""Offline contracts for release cache restores and PR-only CI events."""
from pathlib import Path
import ast
import unittest

import yaml


WORKFLOWS = Path(__file__).resolve().parents[1] / '.github/workflows'
COMPILER_SCOPE = 'type=gha,scope=shirube-compiler-${{ matrix.architecture }}'
CACHE_ACTION = 'actions/cache/restore@0057852bfaa89a56745cba8c7296529d2fc39830'


def workflow(name):
    # BaseLoader keeps GitHub's `on` key and treats scalar booleans as strings.
    return yaml.load((WORKFLOWS / name).read_text(), Loader=yaml.BaseLoader)


def mappings(value):
    """Include root/job/step inputs, even conditional and failure-path steps."""
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from mappings(child)
    elif isinstance(value, list):
        for child in value:
            yield from mappings(child)


def expression_value(expression, context):
    """Evaluate the string/boolean expression subset used by the entry point."""
    expression = expression.removeprefix('${{').removesuffix('}}').strip()
    for name, value in context.items():
        expression = expression.replace(name, repr(value))
    tree = ast.parse(expression.replace('&&', ' and ').replace('||', ' or '), mode='eval')

    def value(node):
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], (ast.Eq, ast.NotEq)):
            equal = value(node.left) == value(node.comparators[0])
            return equal if isinstance(node.ops[0], ast.Eq) else not equal
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            args = [value(arg) for arg in node.args]
            if node.func.id == 'hashFiles':
                return 'fixture-input-hash'
            if node.func.id == 'format':
                return args[0].format(*args[1:])
        if isinstance(node, ast.BoolOp):
            result = value(node.values[0])
            for child in node.values[1:]:
                if (isinstance(node.op, ast.And) and result) or (isinstance(node.op, ast.Or) and not result):
                    result = value(child)
            return result
        raise AssertionError(f'Unsupported workflow expression: {ast.dump(node)}')

    return value(tree.body)


class WorkflowCacheCITests(unittest.TestCase):
    def setUp(self):
        self.release=workflow('release.yml')
        self.staged=workflow('staged-candidate.yml')
        self.validation=workflow('validate-staged.yml')

    def test_release_and_nonpublishing_validation_share_the_same_path(self):
        release=self.release['jobs']['build'];validation=self.validation['jobs']['validate']
        self.assertEqual(release['uses'],'./.github/workflows/staged-candidate.yml')
        self.assertEqual(release['uses'],validation['uses'])
        self.assertEqual(release['permissions'],{'contents':'read'})
        self.assertEqual(validation['permissions'],{'contents':'read'})
        self.assertEqual(set(self.validation['on']),{'workflow_dispatch','pull_request'})
        self.assertEqual(self.validation['on']['pull_request']['types'],['labeled'])
        self.assertEqual(self.validation['permissions'],{'contents':'read'})
        for node in mappings(self.validation):
            self.assertNotIn('GH_TOKEN',node)
            self.assertNotIn('secrets',node)
        for node in mappings(self.staged):
            self.assertNotIn('GH_TOKEN',node)
            self.assertNotIn('secrets',node)
            self.assertNotEqual(node.get('contents'),'write')

    def test_validation_event_admission_and_selected_cache_mode(self):
        dispatch = self.validation['on']['workflow_dispatch']['inputs']['cache-mode']
        self.assertEqual(dispatch, {'description': 'Intentional prior-cache use',
                                   'type': 'choice', 'options': ['cold', 'warm'], 'default': 'cold'})
        jobs = self.validation['jobs']
        self.assertEqual(set(jobs), {'resolve', 'validate'})
        self.assertEqual(jobs['validate']['needs'], 'resolve')
        cases = [
            ('pull_request', 'labeled', 'validate-staged', '', True, 'cold'),
            ('pull_request', 'labeled', 'validate-staged-warm', '', True, 'warm'),
            ('pull_request', 'labeled', 'unrelated', '', False, None),
            ('pull_request', 'synchronize', 'validate-staged', '', False, None),
            ('pull_request', 'synchronize', 'validate-staged-warm', '', False, None),
            ('push', '', 'validate-staged', '', False, None),
            ('push', '', 'validate-staged-warm', '', False, None),
            ('workflow_dispatch', '', '', '', True, 'cold'),
            ('workflow_dispatch', '', '', 'cold', True, 'cold'),
            ('workflow_dispatch', '', '', 'warm', True, 'warm'),
        ]
        for event, action, label, cache, expected_run, expected_cache in cases:
            with self.subTest(event=event, action=action, label=label, cache=cache):
                context = {'github.event_name': event, 'github.event.label.name': label,
                           'inputs.cache-mode': cache}
                trigger = self.validation['on'].get(event)
                subscribed = event in self.validation['on'] and (
                    event != 'pull_request' or action in trigger['types'])
                admitted = expression_value(jobs['resolve']['if'], context)
                self.assertEqual(subscribed and admitted, expected_run)
                if expected_run:
                    self.assertEqual(expression_value(jobs['validate']['with']['cache-mode'], context),
                                     expected_cache)

    def test_heavy_stages_and_real_dependencies_have_separate_jobs(self):
        from producer.staged import DEPENDENCIES
        jobs=self.staged['jobs']
        self.assertEqual(set(jobs),{'compiler',*DEPENDENCIES})
        for stage,dependencies in DEPENDENCIES.items():
            job=jobs[stage]
            self.assertEqual(set(job['needs']),{'compiler',*dependencies})
            self.assertLess(int(job['timeout-minutes']),360)
            self.assertEqual(job['strategy']['matrix']['include'],[
                {'architecture':'amd64','runner':'ubuntu-24.04'},
                {'architecture':'arm64','runner':'ubuntu-24.04-arm'}])
            commands=[step['run'] for step in job['steps'] if 'run' in step]
            self.assertEqual(len(commands),2)
            self.assertIn('producer.compiler_artifact load',commands[0])
            self.assertIn('scripts/run-native-stage.py',commands[1])
            self.assertEqual('--discard-archive' in commands[0], stage == 'iso')
            self.assertEqual('--discard-input-archives' in commands[1], stage == 'iso')
            self.assertNotIn('scripts/build-release.sh',commands[1])
            downloads=[step['with']['name'] for step in job['steps']
                       if step.get('uses','').startswith('actions/download-artifact@')]
            self.assertEqual(set(downloads),{'shirube-compiler-${{ matrix.architecture }}', *{'shirube-stage-${{ matrix.architecture }}-'+d for d in dependencies}})

    def test_only_unsigned_key_free_compilation_payloads_are_cached(self):
        jobs=self.staged['jobs']
        for stage,job in jobs.items():
            nodes=list(mappings(job))
            restores=[n for n in nodes if n.get('uses','').startswith('actions/cache/restore@')]
            self.assertEqual(bool(restores),stage in {'native','vpp','kea','salt','runtime'})
            for restore in restores:
                self.assertEqual(restore['if'],"inputs.cache-mode == 'warm'")
                self.assertNotIn('restore-keys',restore['with'])
                key=restore['with']['key']
                self.assertIn('hashFiles',key)
                self.assertNotIn('inputs.commit',key)
                self.assertNotIn('github.sha',key)
            for step in job['steps']:
                if step.get('uses','').startswith('docker/setup-buildx-action@'):
                    self.assertEqual(step['with']['cache-binary'],'false')
                if step.get('uses','').startswith('docker/build-push-action@'):
                    self.assertEqual(step['with']['push'],'false')
                    self.assertEqual(stage,'compiler')
        compiler=[s for s in jobs['compiler']['steps'] if s.get('id')=='image'][0]['with']
        self.assertEqual(compiler['no-cache'],"${{ inputs.cache-mode == 'cold' }}")
        self.assertNotIn('github.run_id',compiler['cache-to'])
        self.assertIn('hashFiles',compiler['cache-to'])
        self.assertTrue(compiler['cache-from'].startswith("${{ inputs.cache-mode == 'warm' &&"))
        self.assertTrue(compiler['cache-from'].endswith("|| '' }}"))

    def test_cold_seeds_warm_without_prior_cache_or_consumer_builds(self):
        jobs=self.staged['jobs']
        producer=jobs['compiler']['steps']
        builds=[s for s in producer if s.get('uses','').startswith('docker/build-push-action@')]
        self.assertEqual(len(builds),1)
        from producer.compiler_artifact import BASES
        self.assertTrue(all(base in builds[0]['with']['build-args'] for base in BASES.values()))
        settings=builds[0]['with']
        self.assertNotIn('cache-mode',settings['cache-to'])
        # The same input patterns feed restore/save; neither run ID nor commit
        # attribution changes the content key for a subsequent warm run.
        self.assertEqual(settings['cache-from'].split('hashFiles(',1)[1].split('))',1)[0],
                         settings['cache-to'].split('hashFiles(',1)[1].split(')',1)[0])
        self.assertTrue(any('producer.compiler_artifact export' in s.get('run','') for s in producer))
        for name,job in jobs.items():
            if name=='compiler':continue
            steps=job['steps']
            self.assertFalse(any(s.get('uses','').startswith(('docker/build-push-action@',
                                                                          'docker/setup-buildx-action@')) for s in steps))
            download=next(s for s in steps if s.get('with',{}).get('name')=='shirube-compiler-${{ matrix.architecture }}')
            self.assertEqual(download['uses'],'actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093')
            self.assertNotIn('run-id',download['with'])  # download is scoped to this run
            loader=next(s for s in steps if s.get('id')=='image')
            execute=next(s for s in steps if 'scripts/run-native-stage.py' in s.get('run',''))
            self.assertLess(steps.index(download),steps.index(loader))
            self.assertLess(steps.index(loader),steps.index(execute))
            self.assertEqual(loader['env']['RUN_ID'],'${{ github.run_id }}')
            self.assertNotIn('continue-on-error',loader)
            saves=[s for s in steps if s.get('uses','').startswith('actions/cache/save@')]
            restores=[s for s in steps if s.get('uses','').startswith('actions/cache/restore@')]
            self.assertEqual(len(saves),len(restores))
            for save,restore in zip(saves,restores):
                self.assertEqual(restore['if'],"inputs.cache-mode == 'warm'")
                self.assertEqual(save['if'],"(github.event_name == 'workflow_dispatch' || github.event_name == 'pull_request') && success() && steps.cache.outputs.cache-hit != 'true'")
                self.assertEqual(save['with'],restore['with'])
                self.assertIn('--cache',execute['run'])
            if name=='kernel':
                self.assertFalse(saves)
                self.assertNotIn('--cache',execute['run'])

    def test_actual_cache_expressions_event_mode_hit_success_matrix(self):
        jobs = self.staged['jobs']
        compiler = next(s['with'] for s in jobs['compiler']['steps']
                        if s.get('uses', '').startswith('docker/build-push-action@'))
        saves = [(name, step) for name, job in jobs.items() for step in job['steps']
                 if step.get('uses', '').startswith('actions/cache/save@')]
        self.assertEqual([name for name, _ in saves], ['native', 'vpp', 'kea', 'salt', 'runtime'])
        for event in ('pull_request_target', 'workflow_dispatch', 'pull_request', 'push',
                      'workflow_call', 'schedule'):
            for mode in ('cold', 'warm'):
                for hit in ('true', 'false', ''):
                    for success in (True, False):
                        for arch in ('amd64', 'arm64'):
                            with self.subTest(event=event, mode=mode, hit=hit,
                                              success=success, architecture=arch):
                                context = {'github.event_name': event, 'inputs.cache-mode': mode,
                                           'steps.cache.outputs.cache-hit': hit,
                                           'success()': success, 'matrix.architecture': arch}
                                allowed = event in ('workflow_dispatch', 'pull_request')
                                scope = 'scope=shirube-staged-compiler-' + arch + '-fixture-input-hash'
                                self.assertEqual(expression_value(compiler['cache-to'], context),
                                                 'type=gha,mode=max,ignore-error=true,' + scope
                                                 if allowed else '')
                                self.assertEqual(expression_value(compiler['cache-from'], context),
                                                 'type=gha,' + scope if mode == 'warm' else '')
                                self.assertEqual(expression_value(compiler['no-cache'], context),
                                                 mode == 'cold')
                                for name, save in saves:
                                    self.assertEqual(expression_value(save['if'], context),
                                                     allowed and success and hit != 'true', name)
                                    restore = next(s for s in jobs[name]['steps']
                                                   if s.get('uses', '').startswith('actions/cache/restore@'))
                                    self.assertEqual(expression_value(restore['if'], context),
                                                     mode == 'warm', name)

    def test_publication_keeps_two_architecture_and_uploaded_artifact_gates(self):
        self.assertEqual(self.release['on'],{'pull_request_target':{'types':['closed'],'branches':['main']}})
        jobs=self.release['jobs']
        self.assertEqual(jobs['resolve']['if'],'github.event.pull_request.merged == true')
        self.assertEqual(set(jobs['resolve']['outputs']), {'eligible','noop','commit','version'})
        self.assertNotIn('timeout_minutes', Path('producer/actions.py').read_text())
        self.assertEqual(jobs['stage']['strategy']['matrix']['architecture'],['amd64','arm64'])
        self.assertEqual(jobs['stage']['needs'],['resolve','build'])
        self.assertEqual(jobs['stage']['steps'][-1]['run'],'python3 -m producer.actions stage')
        publish=jobs['publish']
        self.assertEqual(publish['needs'],['resolve','build','stage'])
        self.assertIn("needs.build.result == 'success'",publish['if'])
        self.assertIn("needs.stage.result == 'success'",publish['if'])
        verify,upload,promote=publish['steps'][1:]
        self.assertEqual(verify['run'],'python3 -m producer.actions publish')
        self.assertEqual(upload['id'],'assets')
        self.assertEqual(upload['with']['if-no-files-found'],'error')
        self.assertEqual(promote['run'],'python3 -m producer.actions promote')
        self.assertEqual(self.release['concurrency'],{'group':'shirube-os-release','queue':'max','cancel-in-progress':'false'})
        for job in jobs.values():
            if 'runs-on' in job:self.assertEqual(job['runs-on'],'ubuntu-24.04')
            if 'timeout-minutes' in job:self.assertLess(int(job['timeout-minutes']),360)

    def test_test_events_names_permissions_and_runners(self):
        test = workflow('test.yml')
        # Exact mapping excludes push (branches/tags), closed, dispatch and merge_group.
        self.assertEqual(test['on'], {'pull_request': {
            'types': ['opened', 'synchronize', 'reopened', 'ready_for_review']}})
        self.assertEqual(test['name'], 'Test')
        self.assertEqual(test['permissions'], {'contents': 'read'})
        self.assertEqual(set(test['jobs']), {'test', 'hosted-capability'})
        unit = test['jobs']['test']
        self.assertNotIn('name', unit)  # GitHub uses the job ID as its check name.
        self.assertEqual(unit['runs-on'], 'ubuntu-24.04')
        self.assertEqual([s.get('name') for s in unit['steps'] if 'run' in s], [
            'Unit and failure-path tests', 'Shell syntax',
            'Workflow structure, expressions and security context'])
        hosted = test['jobs']['hosted-capability']
        self.assertEqual(hosted['name'], 'Native hosted capability (${{ matrix.architecture }})')
        self.assertEqual(hosted['runs-on'], '${{ matrix.runner }}')
        self.assertEqual(hosted['strategy'], {'fail-fast': 'false', 'matrix': {'include': [
            {'architecture': 'amd64', 'runner': 'ubuntu-24.04'},
            {'architecture': 'arm64', 'runner': 'ubuntu-24.04-arm'}]}})
        for job in test['jobs'].values():
            self.assertNotIn('permissions', job)

    def test_native_trial_remains_manual_feature_branch_diagnostic(self):
        trial = workflow('native-trial.yml')
        self.assertEqual(set(trial['on']), {'workflow_dispatch'})
        stage = trial['on']['workflow_dispatch']['inputs']['stage']
        self.assertEqual(stage['default'], 'iso')
        self.assertEqual(stage['options'], ['iso', 'release'])
        self.assertEqual(trial['permissions'], {'contents': 'read'})
        job = trial['jobs']['trial']
        self.assertEqual(job['if'], "github.repository == 'michibiki-io/shirube-os' && startsWith(github.ref, 'refs/heads/feature/')")
        compiler, = [s for s in job['steps']
                     if s.get('uses', '').startswith('docker/build-push-action@')]
        self.assertEqual(compiler['with']['cache-from'], COMPILER_SCOPE)
        self.assertEqual(compiler['with']['cache-to'], COMPILER_SCOPE + ',mode=max')
        commands = [s['run'] for s in job['steps'] if 'run' in s]
        self.assertEqual(commands, [
            'python3 -m producer.native_trial "$TRIAL_ARCHITECTURE" --stage "$TRIAL_STAGE" --storage "$TRIAL_STORAGE" --report "$TRIAL_STORAGE/native-trial-$TRIAL_ARCHITECTURE.json"'])


if __name__ == '__main__':
    unittest.main()
