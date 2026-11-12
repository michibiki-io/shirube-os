"""Tiny offline recovery fixtures; never call live gh or build a guest/image."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from producer import actions
from producer import recover_release as recovery


def digest(data):
    return hashlib.sha256(data).hexdigest()


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.content = {'manifest.json': b'manifest fixture', 'SHA256SUMS': b'checksum fixture'}
        self.content.update({f'asset-{i}': b'x' for i in range(180)})
        self.assets = [{'id': i+1, 'name': name, 'size': len(data), 'state': 'uploaded',
                        'digest': 'sha256:'+digest(data), 'uploader': {'id': 10}}
                       for i, (name, data) in enumerate(self.content.items())]
        self.metadata = {a['name']: (a['id'], a['size'], a['digest'][7:])
                         for a in self.assets[:2]}
        self.live = {'id': recovery.RELEASE, 'tag_name': recovery.VERSION,
                     'name': 'Shirube OS '+recovery.VERSION, 'target_commitish': recovery.SOURCE,
                     'draft': True, 'prerelease': False,
                     'body': f'Source commit: `{recovery.SOURCE}`', 'assets': self.assets}
        self.run = {'id': recovery.RUN, 'run_attempt': 1, 'repository': {
                    'id': recovery.REPOSITORY_ID, 'full_name': actions.REPO},
                    'head_sha': recovery.HEAD, 'path': '.github/workflows/release.yml',
                    'event': 'pull_request_target', 'status': 'completed', 'conclusion': 'failure'}
        self.pr = {'number': 21, 'merged': True, 'merge_commit_sha': recovery.SOURCE,
                   'head': {'sha': recovery.HEAD}, 'base': {'ref': 'main',
                   'repo': {'id': recovery.REPOSITORY_ID}}}
        self.artifact = {'id': recovery.ARTIFACT,
                         'name': f'shirube-os-{recovery.VERSION}-{recovery.SOURCE}-1',
                         'size_in_bytes': 19162306263, 'expired': False,
                         'expires_at': '2027-02-10T00:00:00Z',
                         'digest': 'sha256:'+recovery.ARTIFACT_DIGEST,
                         'workflow_run': {'id': recovery.RUN, 'head_sha': recovery.HEAD,
                                          'repository_id': recovery.REPOSITORY_ID}}
        self.jobs = [{'id': i+1, 'name': name, 'run_id': recovery.RUN, 'run_attempt': 1,
                      'status': 'completed', 'conclusion': 'success'}
                     for i, name in enumerate(sorted(recovery.PREDECESSORS))]
        self.jobs.append({'id': 131154518909, 'name': 'publish', 'run_id': recovery.RUN,
                          'run_attempt': 1, 'status': 'completed', 'conclusion': 'failure',
                          'steps': [{'name': n, 'number': i+1, 'status': 'completed', 'conclusion': c}
                                    for i, (n, c) in enumerate([(recovery.VERIFY_STEP, 'success'),
                                    (recovery.UPLOAD_STEP, 'success'), (recovery.PUBLISH_STEP, 'failure')])]})
        self.calls = []
        self.tag_refs = [{'ref': f'refs/tags/{recovery.VERSION}',
                          'object': {'type': 'commit', 'sha': recovery.SOURCE}}]
        self.tag_objects = {}
        self.after_download = lambda: None
        self.edit_mode = 'success'
        self.controller = Path('/unused/controller')
        self.source = Path('/unused/original-source')
        for target, name, value in [(recovery, 'ASSET_BYTES', sum(map(len, self.content.values()))),
                                     (recovery, 'METADATA', self.metadata)]:
            patcher = patch.object(target, name, value); patcher.start(); self.addCleanup(patcher.stop)
        patcher = patch.object(actions, 'gh', side_effect=self.gh)
        self.gh_mock = patcher.start(); self.addCleanup(patcher.stop)

    def gh(self, *args, **kwargs):
        self.calls.append(args)
        if args[:2] == ('release', 'edit'):
            if self.edit_mode != 'still-draft':
                self.live['draft'] = False
                if not self.tag_refs:
                    self.tag_refs = [{'ref': f'refs/tags/{recovery.VERSION}',
                                      'object': {'type': 'commit', 'sha': recovery.SOURCE}}]
                self.live['body'] = Path(args[args.index('--notes-file')+1]).read_text()
            if self.edit_mode in ('uncertain', 'still-draft'):
                raise subprocess.CalledProcessError(1, ['gh', 'release', 'edit'])
            if self.edit_mode == 'changed': self.assets[0]['id'] += 1000
            return ''
        prefix = f'repos/{actions.REPO}/'
        path = args[-1].removeprefix(prefix)
        if args[:4] == ('api', '--paginate', '--slurp', prefix+'releases'):
            return [[copy.deepcopy(self.live)]]
        if path == f'actions/runs/{recovery.RUN}': return copy.deepcopy(self.run)
        if path == 'pulls/21': return copy.deepcopy(self.pr)
        if path == f'actions/artifacts/{recovery.ARTIFACT}': return copy.deepcopy(self.artifact)
        if path == f'actions/runs/{recovery.RUN}/attempts/1/jobs?per_page=100':
            return [{'total_count': len(self.jobs), 'jobs': copy.deepcopy(self.jobs[:13])},
                    {'total_count': len(self.jobs), 'jobs': copy.deepcopy(self.jobs[13:])}]
        if path == f'releases/{recovery.RELEASE}': return copy.deepcopy(self.live)
        if path == f'releases/{recovery.RELEASE}/assets?per_page=100':
            return [copy.deepcopy(self.assets[:100]), copy.deepcopy(self.assets[100:])]
        if path == f'releases/tags/{recovery.VERSION}': return copy.deepcopy(self.live)
        if path == f'git/matching-refs/tags/{recovery.VERSION}': return [copy.deepcopy(self.tag_refs)]
        if path.startswith('git/tags/'): return {'object': copy.deepcopy(self.tag_objects[path[9:]])}
        self.fail(f'Unexpected/forbidden gh operation: {args}')

    def download(self, version, root):
        self.assertEqual(version, recovery.VERSION)
        root.mkdir()
        for name, data in self.content.items(): (root/name).write_bytes(data)
        self.after_download()

    def execute(self):
        with patch.object(recovery, 'controller_context', return_value=(self.controller, self.source, 'c'*40)), \
                patch.object(recovery, 'clean_root') as clean, \
                patch.object(actions, 'download', side_effect=self.download) as download, \
                patch.object(recovery, 'validate_original') as validate, \
                patch.object(recovery.shutil, 'disk_usage', return_value=type('Usage', (), {'free': 2**40})()):
            result = recovery.recover()
            download.assert_called_once()
            validate.assert_called_once()
            clean.assert_any_call(self.source, recovery.SOURCE)
            return result

    def no_mutation(self):
        self.assertFalse(any(c[:2] == ('release', 'edit') for c in self.calls))

    def test_unequal_head_source_success_one_download_one_edit(self):
        self.assertNotEqual(recovery.HEAD, recovery.SOURCE)
        self.assertEqual(self.execute(), 'RECOVERED')
        self.assertEqual(sum(c[:2] == ('release', 'edit') for c in self.calls), 1)
        self.assertIn('no aggregate ZIP was downloaded', self.live['body'])
        self.assertIn('`'+'c'*40+'`', self.live['body'])
        self.assertIn(recovery.SOURCE, self.live['body'])

    def test_run_pr_artifact_mismatch_and_missing_evidence(self):
        for attr in ('run', 'pr', 'artifact'):
            original = copy.deepcopy(getattr(self, attr))
            for key in original:
                with self.subTest(attr=attr, missing=key):
                    value = copy.deepcopy(original); del value[key]; setattr(self, attr, value)
                    with self.assertRaises((ValueError, KeyError)): recovery.evidence()
                    self.no_mutation()
            setattr(self, attr, original)
        for attr, key, value in [('run', 'run_attempt', 2), ('run', 'event', 'push'),
                                 ('run', 'conclusion', 'success'), ('pr', 'merged', False),
                                 ('pr', 'merge_commit_sha', recovery.HEAD),
                                 ('artifact', 'expired', True), ('artifact', 'size_in_bytes', 1),
                                 ('artifact', 'digest', 'sha256:'+'a'*64),
                                 ('artifact', 'expires_at', '2099-01-01T00:00:00Z')]:
            original = copy.deepcopy(getattr(self, attr))
            with self.subTest(attr=attr, key=key):
                getattr(self, attr)[key] = value
                with self.assertRaises(ValueError): recovery.evidence()
            setattr(self, attr, original)

    def test_absent_draft_tag_allows_recovery(self):
        self.tag_refs = []
        self.assertEqual(self.execute(), 'RECOVERED')

    def test_existing_lightweight_and_annotated_exact_tag_allow_recovery(self):
        # The normal success test covers lightweight; use an annotated tag here.
        self.tag_refs[0]['object'] = {'type': 'tag', 'sha': 'a'*40}
        self.tag_objects['a'*40] = {'type': 'commit', 'sha': recovery.SOURCE}
        self.tag_refs.append({'ref': f'refs/tags/{recovery.VERSION}0',
                              'object': {'type': 'commit', 'sha': recovery.HEAD}})
        self.assertEqual(self.execute(), 'RECOVERED')

    def test_wrong_existing_tag_rejects_before_download_or_edit(self):
        self.tag_refs[0]['object']['sha'] = recovery.HEAD
        with patch.object(actions, 'download') as download:
            with self.assertRaisesRegex(ValueError, 'tag source mismatch'): self.execute()
            download.assert_not_called()
        self.no_mutation()

    def test_conflicting_tag_introduced_at_final_boundary_rejects_before_edit(self):
        self.tag_refs = []
        original = recovery.unchanged
        count = 0
        def unchanged(snapshot):
            nonlocal count
            count += 1
            if count == 2:
                self.tag_refs = [{'ref': f'refs/tags/{recovery.VERSION}',
                                  'object': {'type': 'commit', 'sha': recovery.HEAD}}]
            return original(snapshot)
        with patch.object(recovery, 'unchanged', side_effect=unchanged):
            with self.assertRaisesRegex(ValueError, 'tag source mismatch'): self.execute()
        self.no_mutation()

    def test_exact_tag_absence_duplicates_bad_chains_and_api_errors_fail_closed(self):
        exact = copy.deepcopy(self.tag_refs[0])
        for refs in ([], [dict(exact, ref=f'refs/tags/{recovery.VERSION}0')],
                     [exact, exact], [dict(exact, object={'type': 'tree', 'sha': recovery.SOURCE})],
                     [dict(exact, object={'type': 'commit', 'sha': 'bad'})],
                     [dict(exact, object={'type': 'tag', 'sha': 'a'*40})]):
            with self.subTest(refs=refs):
                self.tag_refs = refs
                self.tag_objects['a'*40] = {'type': 'tag', 'sha': 'a'*40}
                with self.assertRaises(ValueError): recovery.published_tag()
        self.tag_refs = [dict(exact, ref=f'refs/tags/{recovery.VERSION}0')]
        recovery.published_tag(allow_absent=True)
        with patch.object(recovery, 'pages', side_effect=subprocess.CalledProcessError(1, ['gh'])):
            with self.assertRaises(subprocess.CalledProcessError): recovery.published_tag(allow_absent=True)
        self.no_mutation()

    def test_all_predecessors_and_step_provenance_fail_closed(self):
        original = copy.deepcopy(self.jobs)
        for index in range(26):
            with self.subTest(job=index):
                self.jobs = copy.deepcopy(original); self.jobs[index]['conclusion'] = 'skipped'
                with self.assertRaises(ValueError): recovery.evidence()
        variants = [original[:-1], original+[original[0]], original[:-1]+[original[0]]]
        for field, value in [('run_attempt', 2), ('run_id', 1), ('status', 'in_progress')]:
            jobs = copy.deepcopy(original); jobs[0][field] = value; variants.append(jobs)
        for field, value in [('id', 1), ('conclusion', 'success')]:
            jobs = copy.deepcopy(original); jobs[-1][field] = value; variants.append(jobs)
        for index in range(3):
            for field, value in [('conclusion', 'skipped'), ('name', 'unknown'), ('number', 0)]:
                jobs = copy.deepcopy(original); jobs[-1]['steps'][index][field] = value; variants.append(jobs)
        jobs = copy.deepcopy(original); jobs[-1]['steps'].append(jobs[-1]['steps'][0]); variants.append(jobs)
        for jobs in variants:
            with self.subTest(jobs=jobs[-1]['name']):
                self.jobs = jobs
                with self.assertRaises(ValueError): recovery.evidence()
        self.no_mutation()

    def test_initial_unowned_malformed_missing_and_ambiguous_release(self):
        original = copy.deepcopy(self.live)
        for field, value in [('id', 1), ('target_commitish', recovery.HEAD), ('body', ''),
                             ('name', 'wrong'), ('prerelease', True), ('draft', None)]:
            with self.subTest(field=field):
                self.live[field] = value
                with self.assertRaises(ValueError): recovery.candidate()
                self.live = copy.deepcopy(original)
        for field, value in [('id', 3), ('size', 2), ('digest', 'sha256:'+'a'*64),
                             ('state', 'new'), ('name', '../escape')]:
            with self.subTest(asset_field=field):
                assets = copy.deepcopy(self.assets); self.assets[0][field] = value
                with self.assertRaises(ValueError): recovery.candidate()
                self.assets = assets
        self.assets[1]['name'] = self.assets[0]['name']
        with self.assertRaises(ValueError): recovery.candidate()
        self.no_mutation()

    def test_changed_inventory_after_download_and_at_final_boundary(self):
        for point in ('download', 'boundary'):
            self.assets[10]['id'] = 11
            self.calls.clear()
            if point == 'download':
                self.after_download = lambda: self.assets[10].update(id=999)
                with self.assertRaises(ValueError): self.execute()
            else:
                self.after_download = lambda: None
                original = recovery.unchanged
                count = 0
                def unchanged(snapshot):
                    nonlocal count
                    count += 1
                    if count == 2: self.assets[10]['id'] = 999
                    return original(snapshot)
                with patch.object(recovery, 'unchanged', side_effect=unchanged):
                    with self.assertRaises(ValueError): self.execute()
            self.no_mutation()

    def test_ownership_races_and_expiry_at_publication_boundary_fail_closed(self):
        original = copy.deepcopy(self.live)
        artifact = copy.deepcopy(self.artifact)
        for field, value in [('id', 99), ('body', 'changed'), ('target_commitish', recovery.HEAD), ('expired', True)]:
            self.live = copy.deepcopy(original); self.artifact = copy.deepcopy(artifact); self.calls.clear()
            def after_download():
                if field == 'expired': self.artifact[field] = value
                else: self.live[field] = value
            self.after_download = after_download
            with self.subTest(field=field):
                with self.assertRaises(ValueError): self.execute()
                self.no_mutation()

    def test_supersession_before_download_and_before_edit(self):
        for point in ('initial', 'boundary'):
            with patch.object(actions, 'require_not_superseded', side_effect=
                              ValueError('superseded') if point == 'initial' else [None, ValueError('superseded')]):
                with self.assertRaisesRegex(ValueError, 'superseded'): self.execute()
                self.no_mutation()

    def test_insufficient_space_refuses_before_download(self):
        with patch.object(recovery, 'controller_context'), patch.object(recovery, 'evidence'), \
                patch.object(recovery, 'candidate', return_value=self.live), \
                patch.object(actions, 'require_not_superseded'), \
                patch.object(actions, 'download') as download, \
                patch.object(recovery.shutil, 'disk_usage', return_value=type('Usage', (), {'free': 0})()):
            # Supply identities without touching disk or network.
            with patch.object(recovery, 'controller_context', return_value=(self.controller, self.source, 'c'*40)):
                with self.assertRaisesRegex(ValueError, 'Insufficient free space'): recovery.recover()
            download.assert_not_called()
            self.no_mutation()

    def test_uncertain_edit_reconciles_without_second_edit_or_validation(self):
        self.edit_mode = 'uncertain'
        self.assertEqual(self.execute(), 'RECOVERED')
        self.assertEqual(sum(c[:2] == ('release', 'edit') for c in self.calls), 1)

    def test_uncertain_still_draft_and_changed_readback_fail_without_retry(self):
        for mode in ('still-draft', 'changed'):
            self.live['draft'] = True; self.calls.clear(); self.edit_mode = mode
            with self.assertRaises(ValueError): self.execute()
            self.assertEqual(sum(c[:2] == ('release', 'edit') for c in self.calls), 1)

    def test_published_rerun_only_verifies_despite_supersession(self):
        self.live['draft'] = False
        self.artifact['expired'] = True  # published bytes outlive the upload evidence
        with patch.object(actions, 'require_not_superseded', side_effect=AssertionError('must not allocate')):
            self.assertEqual(self.execute(), 'NO_OP')
        self.no_mutation()

    def test_wrong_published_tag_and_changed_bytes_are_rejected(self):
        self.tag_refs[0]['object']['sha'] = recovery.HEAD
        with self.assertRaises(ValueError): recovery.published_tag()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)/'assets'; self.download(recovery.VERSION, root)
            (root/'asset-0').write_bytes(b'y')
            with self.assertRaises(ValueError): recovery.check_bytes(root, self.assets)


class SourceContextTests(unittest.TestCase):
    def test_original_validator_uses_own_modules_lock_fixtures_and_no_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)/'original-source'; source.mkdir()
            package = source/'producer'; package.mkdir(); (package/'__init__.py').touch()
            (source/'source-lock.json').write_text('original lock')
            (package/'source_fixtures.py').write_text("VALUE = 'original fixture'\n")
            (package/'actions.py').write_text('def identity(commit):\n    assert commit == '+repr(recovery.SOURCE)+'\n')
            (package/'release.py').write_text('''from pathlib import Path
import os
from .source_fixtures import VALUE
def validate(root, commit, version):
    assert Path.cwd() == Path(__file__).resolve().parents[1]
    assert (Path(__file__).resolve().parents[1]/'source-lock.json').read_text() == 'original lock'
    assert VALUE == 'original fixture'
    assert not any(k in os.environ for k in ('GH_TOKEN', 'GITHUB_TOKEN', 'PYTHONPATH'))
    assert commit == '''+repr(recovery.SOURCE)+''' and version == '''+repr(recovery.VERSION)+'''
    assert (root/'tiny').read_text() == 'bytes'
''')
            root = Path(tmp)/'assets'; root.mkdir(); (root/'tiny').write_text('bytes')
            with patch.dict(os.environ, {'GH_TOKEN': 'must-not-propagate', 'PYTHONPATH': '/controller'}):
                recovery.validate_original(source, root)
            (source/'source-lock.json').write_text('changed lock')
            run = subprocess.run
            with patch.object(recovery.subprocess, 'run', side_effect=lambda *a, **kw: run(*a, **kw, capture_output=True)):
                with self.assertRaises(subprocess.CalledProcessError): recovery.validate_original(source, root)

    def test_clean_root_rejects_dirty_wrong_head_nested_and_shared_git(self):
        path = Path('/tmp/original-source')
        for values in [[str(path), 'wrong'], [str(path), recovery.SOURCE, 'dirty'],
                       ['/tmp'], [str(path), recovery.SOURCE, '', '/shared/.git']]:
            with self.subTest(values=values), patch.object(Path, 'is_dir', return_value=True), \
                    patch.object(Path, 'is_symlink', return_value=False), \
                    patch.object(subprocess, 'check_output', side_effect=values):
                with self.assertRaises(ValueError): recovery.clean_root(path, recovery.SOURCE)

    def test_controller_requires_exact_main_dispatch_context_and_separate_roots(self):
        controller = Path(recovery.__file__).resolve().parents[1]
        sha = 'c'*40
        env = {'GITHUB_SHA': sha, 'GITHUB_EVENT_NAME': 'workflow_dispatch',
               'GITHUB_REF': 'refs/heads/main', 'GITHUB_REPOSITORY': actions.REPO,
               'GITHUB_REPOSITORY_ID': str(recovery.REPOSITORY_ID),
               'GITHUB_WORKFLOW_REF': f'{actions.REPO}/{recovery.WORKFLOW}@refs/heads/main',
               'GITHUB_WORKFLOW_SHA': sha, 'GITHUB_RUN_ID': '999', 'GITHUB_RUN_ATTEMPT': '1'}
        # Octokit's official workflow_dispatch payload uses refs/heads/<branch>:
        # https://github.com/octokit/webhooks/blob/main/payload-examples/api.github.com/workflow_dispatch/payload.json
        event = {'ref': 'refs/heads/main', 'inputs': {}, 'repository': {'id': recovery.REPOSITORY_ID, 'full_name': actions.REPO}}
        run = {'id': 999, 'run_attempt': 1, 'repository': {'id': recovery.REPOSITORY_ID},
               'event': 'workflow_dispatch', 'head_branch': 'main', 'head_sha': sha, 'path': recovery.WORKFLOW}
        def api(path): return {'commit': {'sha': sha}} if path == 'branches/main' else run
        with tempfile.TemporaryDirectory() as tmp:
            payload = Path(tmp)/'event'; payload.write_text(json.dumps(event)); env['GITHUB_EVENT_PATH'] = str(payload)
            with patch.object(recovery, 'api', side_effect=api), patch.object(recovery, 'clean_root') as clean, \
                    patch.object(subprocess, 'run'), patch.object(Path, 'cwd', return_value=controller):
                with patch.dict(os.environ, env, clear=True):
                    self.assertEqual(recovery.controller_context(), (controller, controller.parent/'original-source', sha))
                    clean.assert_any_call(controller.parent/'original-source', recovery.SOURCE)
                for key in env:
                    broken = dict(env); broken.pop(key)
                    with self.subTest(missing=key), patch.dict(os.environ, broken, clear=True):
                        with self.assertRaises((ValueError, KeyError)): recovery.controller_context()
                for key, value in [('GITHUB_REF', 'refs/heads/feature/#24'), ('GITHUB_EVENT_NAME', 'push'),
                                   ('GITHUB_SHA', recovery.SOURCE), ('GITHUB_WORKFLOW_SHA', 'd'*40)]:
                    with self.subTest(key=key), patch.dict(os.environ, dict(env, **{key: value}), clear=True):
                        with self.assertRaises(ValueError): recovery.controller_context()
                for changed in [dict(event, inputs={'target': 'anything'})] + [
                        dict(event, ref=ref) for ref in (
                            None, '', 'main', 'master', 'feature', 'refs/heads/master',
                            'refs/heads/feature/#24', 'refs/tags/main', 'refs/tags/'+recovery.VERSION,
                            'refs/remotes/origin/main', 'refs/pull/24/merge')]:
                    payload.write_text(json.dumps(changed))
                    with self.subTest(payload=changed), patch.dict(os.environ, env, clear=True):
                        with self.assertRaises(ValueError): recovery.controller_context()

    def test_workflow_has_only_pinned_checkouts_permissions_and_no_inputs(self):
        import yaml
        from scripts.check_workflow_concurrency import UniqueLoader, validate
        path = Path(recovery.__file__).resolve().parents[1]/recovery.WORKFLOW
        workflow = yaml.load(path.read_text(), Loader=UniqueLoader)
        validate(workflow, release=True)
        self.assertEqual(workflow.get('on', workflow.get(True)), {'workflow_dispatch': None})
        self.assertEqual(workflow['permissions'], {})
        self.assertEqual(workflow['concurrency'], {'group': 'shirube-os-release', 'queue': 'max', 'cancel-in-progress': False})
        job = workflow['jobs']['recover']
        self.assertIn("github.ref == 'refs/heads/main'", job['if'])
        self.assertEqual(job['permissions'], {'contents': 'write', 'actions': 'read', 'pull-requests': 'read'})
        first, second, command = job['steps']
        for step in (first, second):
            self.assertEqual(step['uses'], 'actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1')
            self.assertIs(step['with']['persist-credentials'], False)
        self.assertEqual(first['with']['ref'], '${{ github.sha }}')
        self.assertEqual(first['with']['fetch-depth'], 0)
        self.assertEqual(second['with']['ref'], recovery.SOURCE)
        self.assertEqual([first['with']['path'], second['with']['path']], ['controller', 'original-source'])
        self.assertEqual(command['run'], 'python3 -B -m producer.recover_release')

    def test_isolated_shallow_fixture_validator_preserves_byte_gate_license_and_secret_checks(self):
        from tests.test_release import CHECKS, ReleaseTests
        repository = Path(recovery.__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            seed = Path(tmp)/'fixture-source'; seed.mkdir()
            # Task-owned code fixture only: no historical release bytes/history.
            shutil.copytree(repository/'producer', seed/'producer',
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            shutil.copyfile(repository/'source-lock.json', seed/'source-lock.json')
            subprocess.run(['git', 'init', '--quiet', str(seed)], check=True)
            subprocess.run(['git', '-C', str(seed), 'add', 'producer', 'source-lock.json'], check=True)
            subprocess.run(['git', '-C', str(seed), '-c', 'user.name=Recovery fixture',
                            '-c', 'user.email=fixture@example.invalid', '-c', 'commit.gpgsign=false',
                            '-c', 'core.hooksPath=/dev/null', 'commit', '--quiet', '-m',
                            'Isolated validator code fixture'], check=True)
            fixture_commit = subprocess.check_output(
                ['git', '-C', str(seed), 'rev-parse', 'HEAD'], text=True).strip()
            source = Path(tmp)/'original-source'
            subprocess.run(['git', 'clone', '--quiet', '--depth=1', '--no-local',
                            seed.as_uri(), str(source)], check=True)
            self.assertEqual(subprocess.check_output(
                ['git', '-C', str(source), 'rev-parse', '--is-shallow-repository'], text=True).strip(), 'true')
            self.assertNotEqual(fixture_commit, recovery.SOURCE)
            self.assertNotEqual(subprocess.run(
                ['git', '-C', str(source), 'cat-file', '-e', recovery.SOURCE+'^{commit}'],
                capture_output=True).returncode, 0)
            # Patch only this unit fixture's identity; the production pin stays 4498….
            source_patch = patch.object(recovery, 'SOURCE', fixture_commit)
            source_patch.start(); self.addCleanup(source_patch.stop)
            recovery.clean_root(source, fixture_commit)
            cases = [(mode, None, None) for mode in ('success', 'license', 'secret', 'bytes')]
            cases += [('gate', arch, gate) for arch in ('amd64', 'arm64') for gate in sorted(CHECKS)]
            for mode, architecture, gate in cases:
                with self.subTest(mode=mode, architecture=architecture, gate=gate):
                    fixture = ReleaseTests('test_complete_metadata_contract')
                    fixture.setUp()
                    try:
                        fixture.commit, fixture.version = recovery.SOURCE, recovery.VERSION
                        fixture.manifest.update(source_commit=fixture.commit, version=fixture.version)
                        for asset in fixture.manifest['assets']:
                            old = fixture.root/asset['name']
                            name = asset['name'].replace('v2026.03.0', recovery.VERSION)
                            old.rename(fixture.root/name); asset['name'] = name
                            if asset['role'] == 'evidence':
                                data = json.loads((fixture.root/name).read_text())
                                data.update(source_commit=fixture.commit, version=fixture.version)
                                (fixture.root/name).write_text(json.dumps(data))
                                asset.update(size=(fixture.root/name).stat().st_size, sha256=actions.sha256(fixture.root/name))
                        if mode == 'gate': fixture.manifest['architectures'][architecture]['checks'][gate] = 'NOT_RUN'
                        if mode == 'license':
                            path = fixture.root/'amd64-licenses.json'
                            data = json.loads(path.read_text()); data['components'][0]['redistributable'] = False
                            path.write_text(json.dumps(data))
                            asset = next(a for a in fixture.manifest['assets'] if a['name'] == path.name)
                            asset.update(size=path.stat().st_size, sha256=actions.sha256(path))
                        if mode == 'secret': fixture.add_source_archive('amd64', b'-----BEGIN PRIVATE KEY-----\n'+b'B'*64+b'\n')
                        fixture.write()
                        if mode == 'bytes': (fixture.root/f'shirube-os-{recovery.VERSION}-amd64.iso').write_bytes(b'tampered')
                        if mode == 'success': recovery.validate_original(source, fixture.root)
                        else:
                            # Capture expected rejection diagnostics without hiding unexpected failures.
                            run = subprocess.run
                            with patch.object(recovery.subprocess, 'run', side_effect=lambda *a, **kw: run(*a, **kw, capture_output=True)):
                                with self.assertRaises(subprocess.CalledProcessError) as rejected:
                                    recovery.validate_original(source, fixture.root)
                            diagnostic = {'gate': 'A required check failed',
                                          'license': 'Missing or unresolved component license clearance',
                                          'secret': 'Private signing key in source bundle',
                                          'bytes': 'Missing, differing or oversized release asset'}[mode]
                            self.assertIn(diagnostic, rejected.exception.stderr.decode())
                    finally:
                        fixture.doCleanups()
            recovery.clean_root(source, recovery.SOURCE)
