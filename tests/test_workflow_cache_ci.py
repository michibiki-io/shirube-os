"""Offline contracts for release cache restores and PR-only CI events."""
from pathlib import Path
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


class WorkflowCacheCITests(unittest.TestCase):
    def setUp(self):
        self.release = workflow('release.yml')
        self.build_steps = self.release['jobs']['build']['steps']

    def test_release_retains_compiler_and_library_restores(self):
        compiler, = [s for s in self.build_steps
                     if s.get('uses', '').startswith('docker/build-push-action@')]
        self.assertEqual(compiler['with']['cache-from'], COMPILER_SCOPE)
        self.assertEqual(compiler['with']['load'], 'true')
        self.assertEqual(compiler['with']['push'], 'false')
        library, = [s for s in self.build_steps if s.get('id') == 'library-cache']
        self.assertEqual(library['uses'], CACHE_ACTION)
        self.assertEqual(library['with'], {
            'path': '${{ runner.temp }}/shirube-library-store',
            'key': "shirube-libraries-v1-${{ matrix.architecture }}-${{ hashFiles('source-lock.json', 'containers/native/**', 'producer/library_cache.py', 'producer/resources.py') }}",
            'restore-keys': 'shirube-libraries-v1-${{ matrix.architecture }}-\n',
        })
        build, = [s for s in self.build_steps
                  if s.get('run') == 'bash scripts/build-release.sh']
        self.assertEqual(build['env']['SHIRUBE_LIBRARY_CACHE_STORE'], library['with']['path'])
        self.assertEqual(build['env']['BUILDX_BUILDER'], '${{ steps.compiler-builder.outputs.name }}')

    def test_release_has_no_cache_export_save_or_write_override(self):
        for node in mappings(self.release):
            with self.subTest(node=node.get('name', node.get('id', 'mapping'))):
                self.assertNotIn('cache-to', node)
                self.assertNotIn('cache-mode', node)
                # Combined actions/cache has a post-save hook; allow restore only.
                action = node.get('uses', '').split('@')[0].lower()
                if action == 'actions/cache' or action.startswith('actions/cache/'):
                    self.assertEqual(action, 'actions/cache/restore')
        self.assertEqual(self.release['permissions'], {'contents': 'read'})
        self.assertEqual({k: v['permissions'] for k, v in self.release['jobs'].items()}, {
            'resolve': {'contents': 'write', 'pull-requests': 'read'},
            'build': {'contents': 'write', 'actions': 'read'},
            'publish': {'contents': 'write', 'actions': 'read'},
        })

    def test_release_disables_implicit_buildx_binary_cache_saves(self):
        # setup-buildx's toolkit can save downloaded binaries in its post hook,
        # including on custom runners where Buildx was not preinstalled.
        setups = [node for node in mappings(self.release)
                  if node.get('uses', '').startswith('docker/setup-buildx-action@')]
        self.assertTrue(setups)
        for setup in setups:
            self.assertEqual(setup.get('with', {}).get('cache-binary'), 'false')

    def test_release_has_no_unused_shared_save_setup(self):
        text = (WORKFLOWS / 'release.yml').read_text()
        for token in ('SHIRUBE_LIBRARY_CACHE_SHARED_OUTPUT', 'cache-ready',
                      'VERIFIED_CACHE', 'RESTORED_BACKUP', 'shirube-library-verified-',
                      'shirube-library-restored-', 'cache-primary-key'):
            self.assertNotIn(token, text)

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

    def test_release_preserves_merged_source_and_publication_gates(self):
        self.assertEqual(self.release['on'], {
            'pull_request_target': {'types': ['closed'], 'branches': ['main']}})
        self.assertEqual(self.release['concurrency'], {
            'group': 'shirube-os-release', 'queue': 'max', 'cancel-in-progress': 'false'})
        jobs = self.release['jobs']
        self.assertEqual(set(jobs), {'resolve', 'build', 'publish'})
        self.assertEqual(jobs['resolve']['if'], 'github.event.pull_request.merged == true')
        for name, job in jobs.items():
            checkout = job['steps'][0]
            self.assertTrue(checkout['uses'].startswith('actions/checkout@'))
            self.assertEqual(checkout['with']['ref'],
                             '${{ github.event.pull_request.merge_commit_sha }}' if name == 'resolve'
                             else '${{ needs.resolve.outputs.commit }}')
            self.assertEqual(checkout['with']['allow-unsafe-pr-checkout'], 'true')
            self.assertEqual(checkout['with']['persist-credentials'], 'false')
            self.assertEqual(checkout['with']['clean'], 'false')
        resolve = jobs['resolve']['steps']
        self.assertEqual(resolve[0]['with']['fetch-depth'], '0')
        self.assertEqual(resolve[1]['run'],
                         'python3 -m unittest discover -s tests -v\n'
                         'bash -n scripts/build-packages.sh scripts/build-release.sh containers/native/package.sh\n')
        self.assertEqual(resolve[2]['run'], 'python3 -m producer.actions resolve')
        self.assertEqual(jobs['build']['needs'], 'resolve')
        self.assertEqual(jobs['build']['if'],
                         "needs.resolve.outputs.eligible == 'true' && needs.resolve.outputs.noop != 'true'")
        self.assertEqual(self.build_steps[-1]['run'], 'python3 -m producer.actions stage')
        publish = jobs['publish']
        self.assertEqual(publish['needs'], ['resolve', 'build'])
        self.assertEqual(publish['if'], "always() && needs.resolve.result == 'success' && needs.resolve.outputs.eligible == 'true' && needs.resolve.outputs.noop != 'true' && needs.build.result == 'success'")
        verify, upload, promote = publish['steps'][1:]
        self.assertEqual(verify['run'], 'python3 -m producer.actions publish')
        self.assertTrue(upload['uses'].startswith('actions/upload-artifact@'))
        self.assertEqual(upload['id'], 'assets')
        self.assertEqual(upload['with']['if-no-files-found'], 'error')
        self.assertEqual(promote['run'], 'python3 -m producer.actions promote')
        for key, output in [('SHIRUBE_ACTIONS_ARTIFACT_ID', 'artifact-id'),
                            ('SHIRUBE_ACTIONS_ARTIFACT_DIGEST', 'artifact-digest')]:
            self.assertEqual(promote['env'][key], '${{ steps.assets.outputs.' + output + ' }}')

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
