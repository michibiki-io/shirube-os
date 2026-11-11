import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from producer import actions


class ActionsTests(unittest.TestCase):
    def setUp(self):
        self.version = 'v2026.03.0'
        self.commit = 'a'*40
        self.draft = {'tag_name': self.version, 'draft': True,
                      'target_commitish': self.commit, 'assets': []}
        self.records = [self.draft]
        self.environment = {'SHIRUBE_VERSION': self.version,
                            'SHIRUBE_SOURCE_COMMIT': self.commit,
                            'SHIRUBE_ACTIONS_ARTIFACT_ID': '123',
                            'SHIRUBE_ACTIONS_ARTIFACT_DIGEST': 'b'*64}

    def release_api(self, *args, **kwargs):
        if args == ('api', '--paginate', '--slurp', f'repos/{actions.REPO}/releases'):
            return [self.records[:1], self.records[1:]]
        if args == ('api', f'repos/{actions.REPO}/releases/tags/{self.version}'):
            published = [record for record in self.records
                         if record['tag_name'] == self.version and not record['draft']]
            if not published:
                raise subprocess.CalledProcessError(1, ['gh', *args], stderr='HTTP 404: Not Found')
            return published[0]
        if args == ('api', f'repos/{actions.REPO}/actions/artifacts/123'):
            return {'expired': False, 'digest': 'sha256:'+'b'*64,
                    'workflow_run': {'head_sha': self.commit, 'id': 456}}
        if args[:2] in [('release', 'upload'), ('release', 'edit')] or args[:3] == ('api', '--method', 'DELETE'):
            return ''
        self.fail(f'Unexpected GitHub operation: {args}')

    def test_draft_lookup_uses_all_release_pages_when_tag_endpoint_returns_404(self):
        self.records.insert(0, dict(self.draft, tag_name='v2026.03.1'))
        with patch.object(actions, 'gh', side_effect=self.release_api) as gh:
            with self.assertRaises(subprocess.CalledProcessError):
                actions.get_release(self.version)
            gh.reset_mock()
            self.assertEqual(actions.require_draft(self.version, self.commit), self.draft)
            gh.assert_called_once_with('api', '--paginate', '--slurp',
                                       f'repos/{actions.REPO}/releases', json_result=True)

    def test_missing_or_ambiguous_draft_is_rejected(self):
        for records, error in [([], subprocess.CalledProcessError),
                               ([dict(self.draft, tag_name='v2026.03.1')], subprocess.CalledProcessError),
                               ([self.draft, dict(self.draft)], ValueError),
                               ([self.draft, dict(self.draft, target_commitish='b'*40)], ValueError),
                               ([self.draft, dict(self.draft, draft=False)], ValueError)]:
            with self.subTest(records=records):
                self.records = records
                with patch.object(actions, 'gh', side_effect=self.release_api):
                    with self.assertRaises(error):
                        actions.require_draft(self.version, self.commit)

    def test_unowned_or_ambiguous_draft_blocks_every_mutation_entry_point(self):
        environment = dict(self.environment, SHIRUBE_ARCHITECTURE='amd64',
                           SHIRUBE_CANDIDATE_DIRECTORY='unused-fixture')
        for records in [[dict(self.draft, target_commitish='b'*40)],
                        [self.draft, dict(self.draft)]]:
            self.records = records
            for command in [actions.stage, actions.publish, actions.promote]:
                with self.subTest(records=records, command=command.__name__), \
                        patch.dict(os.environ, environment), patch.object(actions, 'identity'), \
                        patch.object(actions, 'validate', return_value={'assets': []}), \
                        patch.object(actions, 'gh', side_effect=self.release_api) as gh:
                    with self.assertRaises(ValueError):
                        command()
                    self.assertTrue(all(call.args == ('api', '--paginate', '--slurp',
                                                      f'repos/{actions.REPO}/releases')
                                        for call in gh.call_args_list))

    def test_published_lookup_keeps_the_tag_endpoint(self):
        self.records = [dict(self.draft, draft=False)]
        with patch.object(actions, 'gh', side_effect=self.release_api) as gh:
            self.assertEqual(actions.get_release(self.version), self.records[0])
            gh.assert_called_once_with('api', f'repos/{actions.REPO}/releases/tags/{self.version}',
                                       json_result=True)

    def test_stage_replaces_only_owned_architecture_assets_from_draft(self):
        prefix = f'shirube-os-{self.version}-amd64'
        self.draft['assets'] = [{'id': 1, 'name': prefix+'-old.iso'},
                                {'id': 2, 'name': 'candidate-amd64.json'},
                                {'id': 3, 'name': f'shirube-os-{self.version}-arm64.iso'}]
        manifest = {'assets': [{'name': prefix+'.iso'}]}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root/(prefix+'.iso')).write_bytes(b'candidate')
            environment = dict(self.environment, SHIRUBE_ARCHITECTURE='amd64',
                               SHIRUBE_CANDIDATE_DIRECTORY=temporary)
            with patch.dict(os.environ, environment), patch.object(actions, 'identity'), \
                    patch.object(actions, 'validate', return_value=manifest), \
                    patch.object(actions, 'gh', side_effect=self.release_api) as gh:
                actions.stage()
            mutations = [call.args for call in gh.call_args_list
                         if call.args[0] == 'release' or '--method' in call.args]
            self.assertEqual(mutations, [
                ('api', '--method', 'DELETE', f'repos/{actions.REPO}/releases/assets/1'),
                ('api', '--method', 'DELETE', f'repos/{actions.REPO}/releases/assets/2'),
                ('release', 'upload', self.version, '--repo', actions.REPO,
                 str(root/(prefix+'.iso')), str(root/'candidate-amd64.json'))])
            self.assertEqual(json.loads((root/'candidate-amd64.json').read_text()), manifest)
            self.assertEqual(len(gh.call_args_list), len(mutations)+4)

    def test_publish_aggregates_owned_draft_without_publishing(self):
        self.draft['assets'] = [{'id': 1, 'name': 'candidate-amd64.json'},
                                {'id': 2, 'name': 'candidate-arm64.json'}]
        with tempfile.TemporaryDirectory() as temporary:
            public = Path(temporary)/'public'
            staged = None
            def download(version, destination):
                nonlocal staged
                self.assertEqual(version, self.version)
                if staged is not None:
                    shutil.copytree(staged, destination)
                    return
                staged = destination
                destination.mkdir()
                for arch in ['amd64', 'arm64']:
                    name = f'shirube-os-{self.version}-{arch}.iso'
                    (destination/name).write_bytes(arch.encode())
                    candidate = {'schema_version': 1, 'manifest_kind': 'release',
                                 'provider': 'michibiki-io', 'project': 'shirube-os',
                                 'version': self.version, 'source_commit': self.commit,
                                 'source_clean': True, 'upstream': {'version': '2026.03'},
                                 'architectures': {arch: {}}, 'assets': [{'name': name}]}
                    (destination/f'candidate-{arch}.json').write_text(json.dumps(candidate))
            with patch.dict(os.environ, dict(self.environment, SHIRUBE_PUBLIC_DIRECTORY=str(public))), \
                    patch.object(actions, 'identity'), patch.object(actions, 'validate') as validate, \
                    patch.object(actions, 'download', side_effect=download) as downloads, \
                    patch.object(actions, 'gh', side_effect=self.release_api) as gh:
                actions.publish()
            manifest = json.loads((public/'manifest.json').read_text())
            self.assertEqual(set(manifest['architectures']), {'amd64', 'arm64'})
            self.assertEqual(len(manifest['assets']), 2)
            self.assertFalse(list(public.glob('candidate-*.json')))
            self.assertTrue((public/'SHA256SUMS').is_file())
            self.assertEqual(downloads.call_count, 2)
            self.assertEqual(validate.call_count, 2)
            self.assertEqual([call.args[3] for call in gh.call_args_list if '--method' in call.args],
                             [f'repos/{actions.REPO}/releases/assets/1',
                              f'repos/{actions.REPO}/releases/assets/2'])
            self.assertEqual([call.args[:2] for call in gh.call_args_list if call.args[0] == 'release'],
                             [('release', 'upload')])

    def test_published_publish_and_promote_reruns_only_verify(self):
        self.records = [dict(self.draft, draft=False)]
        for command in [actions.publish, actions.promote]:
            with self.subTest(command=command.__name__), patch.dict(os.environ, self.environment), \
                    patch.object(actions, 'identity'), patch.object(actions, 'verify_published') as verify, \
                    patch.object(actions, 'gh', side_effect=self.release_api) as gh:
                command()
                verify.assert_called_once_with(self.version, self.commit)
                self.assertEqual([call.args for call in gh.call_args_list], [
                    ('api', '--paginate', '--slurp', f'repos/{actions.REPO}/releases'),
                    ('api', f'repos/{actions.REPO}/releases/tags/{self.version}')])

    def test_promote_owned_draft_checks_artifact_before_one_transition(self):
        with patch.dict(os.environ, self.environment), patch.object(actions, 'identity'), \
                patch.object(actions, 'download'), patch.object(actions, 'validate') as validate, \
                patch.object(actions, 'gh', side_effect=self.release_api) as gh:
            actions.promote()
        validate.assert_called_once()
        calls = [call.args for call in gh.call_args_list]
        self.assertEqual(calls[-2], ('api', f'repos/{actions.REPO}/actions/artifacts/123'))
        self.assertEqual(calls[-1][:5], ('release', 'edit', self.version, '--repo', actions.REPO))
        self.assertEqual(calls[-1][-1], '--draft=false')
        self.assertEqual(len(calls), 4)

    def test_uncertain_promote_reconciles_published_release_without_retry(self):
        for published in [True, False]:
            with self.subTest(published=published):
                self.draft['draft'] = True
                def gh(*args, **kwargs):
                    if args[:2] == ('release', 'edit'):
                        self.draft['draft'] = not published
                        raise subprocess.CalledProcessError(1, ['gh', *args])
                    return self.release_api(*args, **kwargs)
                with patch.dict(os.environ, self.environment), patch.object(actions, 'identity'), \
                        patch.object(actions, 'download'), patch.object(actions, 'validate'), \
                        patch.object(actions, 'verify_published') as verify, \
                        patch.object(actions, 'gh', side_effect=gh) as api:
                    if published:
                        actions.promote()
                        verify.assert_called_once_with(self.version, self.commit)
                    else:
                        with self.assertRaises(subprocess.CalledProcessError):
                            actions.promote()
                        verify.assert_not_called()
                calls = [call.args for call in api.call_args_list]
                self.assertEqual(sum(call[:2] == ('release', 'edit') for call in calls), 1)
                self.assertEqual(calls[-1], ('api', f'repos/{actions.REPO}/releases/tags/{self.version}'))

    def test_draft_asset_limit_rejected_before_deletion_or_upload(self):
        manifest = {'assets': [{'name': f'shirube-os-v2026.03.0-amd64-{i}'} for i in range(500)]}
        live = {'assets': [{'name': f'shirube-os-v2026.03.0-arm64-{i}'} for i in range(500)]}
        with patch.dict(os.environ, {'SHIRUBE_ARCHITECTURE': 'amd64', 'SHIRUBE_VERSION': 'v2026.03.0',
                'SHIRUBE_SOURCE_COMMIT': 'a'*40, 'SHIRUBE_CANDIDATE_DIRECTORY': 'unused-fixture'}), \
                patch.object(actions, 'identity'), patch.object(actions, 'validate', return_value=manifest), \
                patch.object(actions, 'require_draft', return_value=live), \
                patch.object(actions, 'gh') as gh:
            with self.assertRaisesRegex(ValueError, 'release asset count limit'):
                actions.stage()
            gh.assert_not_called()

    def test_eligible_commit_reserves_one_stream_revision_despite_breaking_subject(self):
        import subprocess
        with tempfile.TemporaryDirectory() as temporary:
            event=Path(temporary)/'event.json';out=Path(temporary)/'output'
            event.write_text(json.dumps({'action':'closed','number':1,'pull_request':{'merged':True,
                'changed_files':1,'commits':1,'base':{'ref':'main'},'merge_commit_sha':'a'*40}}))
            created=[]
            def gh(*args,**kwargs):
                if args[0]=='api' and args[-1].endswith('/files'):return [[{'filename':'producer/pipeline.py'}]]
                if args[0]=='api' and args[-1].endswith('/commits'):
                    return [[{'sha':'c'*40,'commit':{'message':'feat!: change\n\nBREAKING CHANGE: configuration'}}]]
                self.assertEqual(args[:3],('release','create','v2026.03.4'))
                self.assertIn('--draft',args);self.assertEqual(args[args.index('--target')+1],'a'*40)
                notes=Path(args[args.index('--notes-file')+1]).read_text()
                self.assertIn('Upstream Stream: `2026.03`',notes)
                self.assertIn('BREAKING CHANGE: configuration',notes);created.append(args)
            def git(*args):return 'b'*40
            def run(args,**kwargs):return subprocess.CompletedProcess(args,1 if 'show-ref' in args else 0)
            prior={'draft':False,'prerelease':False,'tag_name':'v2026.03.3','target_commitish':'b'*40}
            with patch.dict(os.environ,{'GITHUB_EVENT_PATH':str(event),'GITHUB_EVENT_NAME':'pull_request','GITHUB_OUTPUT':str(out)}),\
                patch.object(actions,'identity'),patch.object(actions,'gh',side_effect=gh),\
                patch.object(actions,'git',side_effect=git),patch.object(actions,'releases',return_value=[prior]),\
                patch.object(actions.subprocess,'run',side_effect=run):
                actions.resolve()
            self.assertEqual(len(created),1)
            self.assertIn('version=v2026.03.4',out.read_text())

    def test_published_or_different_source_release_is_never_mutated(self):
        for release in [dict(self.draft, draft=False), dict(self.draft, target_commitish='b'*40)]:
            self.records = [release]
            with patch.object(actions, 'gh', side_effect=self.release_api):
                with self.assertRaises(ValueError):actions.require_draft('v2026.03.0','a'*40)

    def test_published_rerun_verifies_without_build_or_release_creation(self):
        with tempfile.TemporaryDirectory() as temporary:
            event=Path(temporary)/'event.json';out=Path(temporary)/'output'
            event.write_text(json.dumps({'action':'closed','number':1,'pull_request':{'merged':True,'changed_files':1,'base':{'ref':'main'},'merge_commit_sha':'a'*40}}))
            def gh(*args,**kwargs):
                self.assertEqual(args[0],'api')
                return [[{'filename':'producer/pipeline.py'}]]
            def git(*args):
                if args==('status','--porcelain'):return ''
                return 'a'*40
            with patch.dict(os.environ,{'GITHUB_EVENT_PATH':str(event),'GITHUB_EVENT_NAME':'pull_request','GITHUB_OUTPUT':str(out)}),patch.object(actions,'gh',side_effect=gh),patch.object(actions,'git',side_effect=git),patch.object(actions,'releases',return_value=[{'draft':False,'target_commitish':'main','tag_name':'v2026.03.0'}]),patch.object(actions,'verify_published') as verify:
                actions.resolve();verify.assert_called_once_with('v2026.03.0','a'*40)
            self.assertIn('noop=true',out.read_text())

    def test_incomplete_changed_file_pages_fail_before_any_release(self):
        with tempfile.TemporaryDirectory() as temporary:
            event=Path(temporary)/'event.json';event.write_text(json.dumps({'number':1,'pull_request':{'changed_files':2}}))
            with patch.dict(os.environ,{'GITHUB_EVENT_PATH':str(event)}),patch.object(actions,'gh',return_value=[[{'filename':'README.md'}]]),patch.object(actions,'releases') as releases:
                with self.assertRaises(ValueError):actions.resolve()
                releases.assert_not_called()

    def test_missing_actions_artifact_proof_blocks_promotion(self):
        with patch.dict(os.environ,{'SHIRUBE_VERSION':'v2026.03.0','SHIRUBE_SOURCE_COMMIT':'a'*40},clear=True),patch.object(actions,'identity'),patch.object(actions,'gh',side_effect=self.release_api) as gh:
            with self.assertRaises(ValueError):actions.promote()
            self.assertEqual([call.args for call in gh.call_args_list],
                             [('api', '--paginate', '--slurp', f'repos/{actions.REPO}/releases')]*2)


class ReleaseReservationTests(unittest.TestCase):
    def setUp(self):
        self.records = []
        self.created = []
        self.ancestry_checks = []
        self.verified = []

    def resolve(self, commit):
        def gh(*args, **kwargs):
            if args == ('api', '--paginate', '--slurp', f'repos/{actions.REPO}/pulls/1/files'):
                return [[{'filename': 'producer/pipeline.py'}]]
            if args == ('api', '--paginate', '--slurp', f'repos/{actions.REPO}/pulls/1/commits'):
                return [[{'sha': commit, 'commit': {'message': 'fix: recover candidate'}}]]
            if args == ('api', '--paginate', '--slurp', f'repos/{actions.REPO}/releases'):
                # One release per page ensures allocation and retries use every page.
                return [[dict(record)] for record in self.records]
            self.assertEqual(args[:2], ('release', 'create'))
            self.assertIn('--draft', args)
            self.assertEqual(args[args.index('--target')+1], commit)
            version = args[2]
            self.assertNotIn(version, [record['tag_name'] for record in self.records])
            self.created.append((version, commit))
            self.records.append({'draft': True, 'prerelease': False,
                                 'tag_name': version, 'target_commitish': commit})

        def git(*args):
            self.assertEqual(args[0], 'rev-parse')
            for record in self.records:
                if args[1] == record['tag_name']+'^{commit}' and not record['draft']:
                    return record['target_commitish']
            self.fail(f'Unexpected tag lookup (drafts have no tag): {args}')

        def run(args, **kwargs):
            if args[:3] == ['git', 'show-ref', '--verify']:
                return subprocess.CompletedProcess(args, 1)
            self.assertEqual(args[:3], ['git', 'merge-base', '--is-ancestor'])
            self.assertTrue(kwargs['check'])
            self.ancestry_checks.append(tuple(args[3:]))
            return subprocess.CompletedProcess(args, 0)

        with tempfile.TemporaryDirectory() as temporary:
            event = Path(temporary)/'event.json'
            event.write_text(json.dumps({'action': 'closed', 'number': 1, 'pull_request': {
                'merged': True, 'changed_files': 1, 'commits': 1,
                'base': {'ref': 'main'}, 'merge_commit_sha': commit}}))
            with patch.dict(os.environ, {'GITHUB_EVENT_PATH': str(event),
                                        'GITHUB_EVENT_NAME': 'pull_request'}), \
                    patch.object(actions, 'identity'), patch.object(actions, 'emit') as emit, \
                    patch.object(actions, 'gh', side_effect=gh), \
                    patch.object(actions, 'git', side_effect=git), \
                    patch.object(actions.subprocess, 'run', side_effect=run), \
                    patch.object(actions, 'verify_published',
                                 side_effect=lambda version, target: self.verified.append((version, target))):
                actions.resolve()
                return emit.call_args.args[0]

    def test_failed_draft_reservation_advances_next_merge_and_preserves_retries(self):
        # A fails after reservation, before a tag or any published release exists.
        self.assertEqual(self.resolve('a'*40)['version'], 'v2026.03.0')
        reserved_a = dict(self.records[0])
        self.assertEqual(self.resolve('b'*40)['version'], 'v2026.03.1')
        reserved_b = dict(self.records[1])
        # A third commit sees both paginated reservations, not just the first.
        self.assertEqual(self.resolve('c'*40)['version'], 'v2026.03.2')
        for commit, version in [('a'*40, 'v2026.03.0'), ('b'*40, 'v2026.03.1')]:
            retry = self.resolve(commit)
            self.assertEqual(retry['version'], version)
            self.assertEqual(retry['noop'], 'false')
        self.assertEqual(self.records[:2], [reserved_a, reserved_b])
        self.assertEqual(self.created, [('v2026.03.0', 'a'*40), ('v2026.03.1', 'b'*40),
                                        ('v2026.03.2', 'c'*40)])
        self.assertFalse(self.ancestry_checks)

    def test_highest_draft_revision_is_reserved_but_ancestry_uses_published_release(self):
        self.records = [
            {'draft': False, 'prerelease': False, 'tag_name': 'v2026.03.3', 'target_commitish': 'a'*40},
            {'draft': True, 'prerelease': False, 'tag_name': 'v2026.03.9', 'target_commitish': 'b'*40},
            {'draft': True, 'prerelease': True, 'tag_name': 'v2026.03.10', 'target_commitish': 'c'*40}]
        self.assertEqual(self.resolve('d'*40)['version'], 'v2026.03.11')
        self.assertEqual(self.ancestry_checks, [('a'*40, 'd'*40)])

    def test_older_baseline_reservations_allow_new_stream_revision_zero(self):
        self.records = [
            {'draft': False, 'prerelease': False, 'tag_name': 'v2026.02.3', 'target_commitish': 'a'*40},
            {'draft': True, 'prerelease': False, 'tag_name': 'v2026.02.4', 'target_commitish': 'b'*40}]
        self.assertEqual(self.resolve('c'*40)['version'], 'v2026.03.0')
        self.assertEqual(self.ancestry_checks, [('a'*40, 'c'*40)])

    def test_newer_published_or_reserved_baseline_rejects_downgrade(self):
        for draft in [False, True]:
            with self.subTest(draft=draft):
                self.records = [{'draft': draft, 'prerelease': False,
                                 'tag_name': 'v2026.04.0', 'target_commitish': 'a'*40}]
                with self.assertRaisesRegex(ValueError, 'baseline downgrade'):
                    self.resolve('b'*40)
                self.assertFalse(self.created)

    def test_published_rerun_remains_noop_with_other_commit_draft_reservations(self):
        self.records = [
            {'draft': False, 'prerelease': False, 'tag_name': 'v2026.03.0', 'target_commitish': 'a'*40},
            {'draft': True, 'prerelease': False, 'tag_name': 'v2026.03.1', 'target_commitish': 'b'*40}]
        original = [dict(record) for record in self.records]
        result = self.resolve('a'*40)
        self.assertEqual((result['version'], result['noop']), ('v2026.03.0', 'true'))
        self.assertEqual(self.verified, [('v2026.03.0', 'a'*40)])
        self.assertEqual(self.records, original)
        self.assertFalse(self.created)
