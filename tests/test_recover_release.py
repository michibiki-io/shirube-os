"""Small fixtures for the one-time publication controller; no live mutations."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from producer import actions, release_plan
from producer import recover_release as recovery
from producer.kernel_seed import relevant
from tests.test_workflow_cache_ci import expression_value, workflow

NOW = datetime(2026, 11, 20, tzinfo=timezone.utc)
HEAD = '6' * 40
RUN = '44055555555'
CONTEXT = {'run_id': RUN, 'attempt': '1', 'controller': HEAD}
ROOT = Path(__file__).resolve().parents[1]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def run_binding(run, branch, head):
    return {'id': int(run), 'head_branch': branch, 'head_sha': head,
            'repository_id': recovery.REPOSITORY_ID, 'head_repository_id': recovery.REPOSITORY_ID}


def archive_pin(pin):
    return {'id': pin.id, 'name': pin.name, 'size_in_bytes': pin.size,
            'digest': 'sha256:'+pin.digest, 'expired': False, 'expires_at': pin.expiry,
            'workflow_run': run_binding(recovery.ORIGINAL_RUN, 'feature/#44', recovery.EVENT_HEAD)}


class Sources(unittest.TestCase):
    def test_exact_inherited_archive_metadata_before_download(self):
        pin = recovery.CANDIDATES['amd64']
        expected = archive_pin(pin)
        with patch.object(recovery, 'utcnow', return_value=NOW):
            with patch.object(recovery, 'api', return_value=expected), patch.object(recovery, 'stream') as stream:
                recovery.original_archive(pin, Path('archive.zip'))
                stream.assert_called_once_with(f'actions/artifacts/{pin.id}/zip', Path('archive.zip'),
                                               pin.size, pin.digest)
            mutations = ({'id': pin.id+1}, {'digest': 'sha256:'+'1'*64}, {'expired': True},
                         {'name': 'replacement'}, {'expires_at': '2999-01-01T00:00:00Z'},
                         {'size_in_bytes': pin.size-1}, {'workflow_run': {**expected['workflow_run'], 'id': 1}},
                         {'workflow_run': {**expected['workflow_run'], 'head_sha': recovery.SOURCE}})
            for change in mutations:
                with self.subTest(change=change), patch.object(recovery, 'api', return_value={**expected, **change}), \
                        patch.object(recovery, 'stream') as stream:
                    with self.assertRaises(ValueError):
                        recovery.original_archive(pin, Path('archive.zip'))
                    stream.assert_not_called()
        with patch.object(recovery, 'utcnow', return_value=datetime.fromisoformat(pin.expiry)), \
                patch.object(recovery, 'api', return_value=expected):
            with self.assertRaisesRegex(ValueError, 'expired'):
                recovery.check_pin(pin)

    def test_exact_job_inventory_and_results(self):
        attempt = 2
        names = recovery.BUILD_JOBS | recovery.SKIPPED_JOBS | {'candidate-check', 'evidence'}
        jobs = [{'id': i, 'run_id': recovery.ORIGINAL_RUN, 'run_attempt': attempt, 'status': 'completed',
                 'name': n, 'conclusion': ('skipped' if n in recovery.SKIPPED_JOBS else
                                           'failure' if n == 'evidence' else 'success')}
                for i,n in enumerate(sorted(names), 1)]
        base = {'total_count': len(jobs), 'jobs': jobs}
        with patch.object(recovery, 'pages', return_value=[base]):
            self.assertEqual(set(recovery.attempt_jobs(attempt)), names)
        for name, change in [('build / admission', {'conclusion': 'skipped'}),
                             ('candidate-check', {'conclusion': 'cancelled'}),
                             ('stage', {'conclusion': 'success'}),
                             ('publish', {'conclusion': 'failure'}),
                             ('evidence', {'run_attempt': 1}),
                             ('resolve', {'run_id': 1})]:
            altered = [{**j, **(change if j['name']==name else {})} for j in jobs]
            with self.subTest(name=name), patch.object(recovery, 'pages', return_value=[{**base, 'jobs': altered}]):
                with self.assertRaises(ValueError):
                    recovery.attempt_jobs(attempt)
        with patch.object(recovery, 'pages', return_value=[{**base, 'jobs': jobs[:-1]}]):
            with self.assertRaisesRegex(ValueError, 'inventory'):
                recovery.attempt_jobs(attempt)

    def test_zip_inventory_is_validated_before_extraction(self):
        for file_name, mode, bound in [('asset.iso', stat.S_IFREG, 30),
                                        ('../untrusted.txt', stat.S_IFREG, 30),
                                        ('asset.iso', stat.S_IFLNK, 30),
                                        ('asset.iso', stat.S_IFREG, 2)]:
            with self.subTest(name=file_name, mode=mode, bound=bound), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                info = zipfile.ZipInfo(file_name)
                info.create_system = 3
                info.external_attr = mode << 16
                with zipfile.ZipFile(root/'archive.zip', 'w') as z:
                    z.writestr('manifest.json', '{}')
                    z.writestr('SHA256SUMS', 'sums')
                    z.writestr(info, 'payload')
                if file_name == 'asset.iso' and mode == stat.S_IFREG and bound == 30:
                    recovery.extract_candidate(root/'archive.zip', root/'out', bound)
                    self.assertEqual((root/'out/asset.iso').read_text(), 'payload')
                else:
                    with self.assertRaises(ValueError):
                        recovery.extract_candidate(root/'archive.zip', root/'out', bound)
                    self.assertFalse((root/'out').exists())

    def test_stream_rejects_wrong_bytes_truncation_and_oversize(self):
        class Child:
            def __init__(self, data, returncode):
                import io
                self.stdout = io.BytesIO(data)
                self.returncode = returncode
                self.killed = False
            def __enter__(self): return self
            def __exit__(self, *error): self.stdout.close()
            def wait(self): return self.returncode
            def kill(self): self.killed = True
        payload = b'fixture'
        for data, code in ((payload,0),(b'changed',0),(b'partial'[:-1],0),(b'oversized',0),(payload,2)):
            with self.subTest(data=data, code=code), tempfile.TemporaryDirectory() as tmp:
                child = Child(data, code)
                dst = Path(tmp)/'asset'
                with patch.object(recovery.subprocess, 'Popen', return_value=child) as start:
                    if (data,code)==(payload,0):
                        recovery.stream('releases/assets/123', dst, len(payload), digest(payload), binary=True)
                        self.assertEqual(dst.read_bytes(), payload)
                        self.assertIn('Accept: application/octet-stream', start.call_args.args[0])
                    else:
                        with self.assertRaises(ValueError):
                            recovery.stream('releases/assets/123', dst, len(payload), digest(payload), binary=True)
                        self.assertEqual(child.killed, len(data)>len(payload))

    def test_controller_rejects_non_main_or_noncurrent_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            controller = root/'controller'
            data = {'ref': 'refs/heads/main', 'inputs': None,
                    'repository': {'id': recovery.REPOSITORY_ID, 'full_name': recovery.REPO}}
            event = root/'event.json'
            env = {'GITHUB_SHA': HEAD, 'GITHUB_WORKFLOW_SHA': HEAD, 'GITHUB_RUN_ID': RUN,
                   'GITHUB_RUN_ATTEMPT': '1', 'GITHUB_EVENT_NAME': 'workflow_dispatch',
                   'GITHUB_REF': 'refs/heads/main', 'GITHUB_REPOSITORY': recovery.REPO,
                   'GITHUB_REPOSITORY_ID': str(recovery.REPOSITORY_ID), 'GITHUB_EVENT_PATH': str(event),
                   'GITHUB_WORKFLOW_REF': f'{recovery.REPO}/{recovery.WORKFLOW}@refs/heads/main',
                   'GITHUB_WORKSPACE': str(root)}
            run = {'id': int(RUN), 'run_attempt': 1, 'status': 'in_progress', 'path': recovery.WORKFLOW,
                   'event': 'workflow_dispatch', 'head_branch': 'main', 'head_sha': HEAD,
                   'repository': {'id': recovery.REPOSITORY_ID}, 'head_repository': {'id': recovery.REPOSITORY_ID}}
            event.write_text(json.dumps(data))
            with patch.object(recovery, '__file__', str(controller/'producer/recover_release.py')), \
                    patch.object(Path, 'cwd', return_value=controller), patch.object(recovery, 'clean_checkout') as clean, \
                    patch.object(recovery.subprocess, 'run') as git:
                with patch.dict(os.environ, env, clear=True), patch.object(recovery, 'api', side_effect=[run, {'commit': {'sha': HEAD}}]):
                    self.assertEqual(recovery.controller_context(), (root/'original-source', CONTEXT))
                self.assertEqual([c.args for c in clean.call_args_list],
                                 [(controller,HEAD),(root/'original-source', recovery.SOURCE)])
                git.assert_called_once_with(['git','merge-base','--is-ancestor',recovery.SOURCE,HEAD], check=True)
                for change in ({'GITHUB_REF': 'refs/tags/main'}, {'GITHUB_SHA': recovery.SOURCE},
                               {'GITHUB_RUN_ATTEMPT': '2'}, {'GITHUB_REPOSITORY': 'other/fork'},
                               {'GITHUB_EVENT_NAME': 'pull_request_target'}):
                    with self.subTest(change=change), patch.dict(os.environ, {**env, **change}, clear=True), \
                            patch.object(recovery, 'api') as api:
                        with self.assertRaises(ValueError):
                            recovery.controller_context()
                        api.assert_not_called()
                with patch.dict(os.environ, env, clear=True), patch.object(recovery, 'api', return_value={**run,'head_sha':recovery.SOURCE}):
                    with self.assertRaises(ValueError):
                        recovery.controller_context()
                event.write_text(json.dumps({**data,'inputs': {'version': recovery.VERSION}}))
                with patch.dict(os.environ, env, clear=True):
                    with self.assertRaisesRegex(ValueError, 'inputs'):
                        recovery.controller_context()

    def test_later_main_uses_fresh_compare_and_refuses_detached_or_wrong_bases(self):
        advanced = '7'*40  # Not required to exist in the checkout's local graph.
        branch = {'commit': {'sha': advanced}}
        proof = {'base_commit':{'sha':HEAD}, 'merge_base_commit':{'sha':HEAD},
                 'status':'ahead', 'behind_by':0, 'ahead_by':2}
        with patch.object(recovery,'api',side_effect=[branch,proof]) as api:
            recovery.verify_controller_on_main(HEAD)
        self.assertEqual([call.args for call in api.call_args_list],
                         [('branches/main',), (f'compare/{HEAD}...{advanced}?per_page=1',)])
        for changed in ({'status':'diverged','behind_by':1}, {'merge_base_commit':{'sha':'0'*40}},
                        {'status':'behind','ahead_by':0}, {'base_commit':{'sha':advanced}},
                        {'ahead_by':0}):
            with self.subTest(changed=changed), patch.object(recovery,'api',side_effect=[branch,{**proof,**changed}]):
                with self.assertRaisesRegex(ValueError,'does not contain'):
                    recovery.verify_controller_on_main(HEAD)
        with patch.object(recovery, 'api', return_value={'commit': {'sha': HEAD}}) as api:
            recovery.verify_controller_on_main(HEAD)
        api.assert_called_once_with('branches/main')

    def test_original_validator_uses_separate_source_and_exact_archives_without_credentials(self):
        source, payload = Path('/fixture/source'), Path('/fixture/assets')
        with patch.dict(os.environ, {'GH_TOKEN': 'dummy-token', 'PYTHONPATH': '/wrong/path'}), \
                patch.object(recovery.subprocess, 'check_output', return_value='{"fixture": true}') as run:
            self.assertEqual(recovery.original_validator(source, 'candidate', payload, 'amd64'), {'fixture': True})
            self.assertIsNone(recovery.original_validator(source, 'aggregate', payload))
        for call, arch in zip(run.call_args_list, ['amd64', '']):
            self.assertEqual(call.args[0][1:4], ['-I', '-B', '-c'])
            self.assertEqual(call.args[0][-5:], [str(source), str(payload), recovery.SOURCE, recovery.VERSION, arch])
            self.assertEqual(call.kwargs['cwd'], source)
            self.assertEqual(set(call.kwargs['env']), {'PATH', 'LANG'})


class Publication(unittest.TestCase):
    def test_known_draft_cannot_be_replaced_or_superseded(self):
        body = 'The pinned draft notes'
        live = {'id': recovery.RELEASE_ID, 'tag_name': recovery.VERSION, 'draft': True,
                'body': body, 'assets': [], 'target_commitish': recovery.SOURCE, 'prerelease': False}
        with patch.object(recovery, 'DRAFT_BODY_SHA256', digest(body.encode())), \
                patch.object(recovery, 'live_release', return_value=live), patch.object(recovery, 'check_tag') as tag:
            with patch.object(actions, 'releases', return_value=[live]):
                self.assertEqual(recovery.draft({}), live)
            tag.assert_called_once_with(optional=True)
            for all_releases in ([{**live,'id':1}], [live,{**live,'id':1}],
                                 [live,{'tag_name':'v2026.03.15','draft':False,'prerelease':False}]):
                with patch.object(actions, 'releases', return_value=all_releases):
                    with self.assertRaises(ValueError):
                        recovery.draft({})
            for change in ({'draft':False}, {'body':'edited'},
                           {'assets':[{'id':1,'name':'unexpected','size':1,'digest':'sha256:'+'f'*64,'state':'uploaded'}]}):
                with patch.object(actions, 'releases', return_value=[live]), \
                        patch.object(recovery, 'live_release', return_value={**live, **change}):
                    with self.assertRaises(ValueError):
                        recovery.draft({})

    def test_upload_uses_fixed_release_id_and_verified_response(self):
        data = b'recovered'
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'GH_TOKEN':'dummy-token'}), \
                patch.object(recovery, 'HTTPSConnection') as connect, patch.object(recovery.time, 'sleep'), \
                patch.object(recovery, 'release_identity') as identity, \
                patch.object(recovery, 'DRAFT_BODY_SHA256', digest(b'notes')):
            identity.return_value = {'draft':True, 'body':'notes'}
            asset = Path(tmp)/'verified.iso'
            asset.write_bytes(data)
            session = connect.return_value
            response = session.getresponse.return_value
            response.status = 201
            expected = {'id':7, 'name':asset.name, 'size':len(data), 'digest':'sha256:'+digest(data)}
            response.read.return_value = json.dumps(dict(expected, state='uploaded')).encode()
            self.assertEqual(recovery.upload(asset, len(data), digest(data)), expected)
            self.assertEqual(connect.call_args.args, ('uploads.github.com',))
            method, url, _, headers = session.request.call_args.args
            self.assertEqual((method,url), ('POST', f'/repos/{recovery.REPO}/releases/{recovery.RELEASE_ID}/assets?name=verified.iso'))
            self.assertEqual(headers['Content-Length'], str(len(data)))
            response.status = 422
            with self.assertRaisesRegex(ValueError, 'not confirmed'):
                recovery.upload(asset, len(data), digest(data))
            self.assertEqual(session.request.call_count, 2)  # one per attempted input, no retry
            identity.return_value = {'draft':False, 'body':'notes'}
            with self.assertRaisesRegex(ValueError, 'no longer the original draft'):
                recovery.upload(asset, len(data), digest(data))
            self.assertEqual(session.request.call_count, 2)

    def test_new_actions_proof_requires_exact_controller_run_and_artifact(self):
        env = {'SHIRUBE_ACTIONS_ARTIFACT_ID':'6789', 'SHIRUBE_ACTIONS_ARTIFACT_DIGEST':'a'*64}
        record = {'id':6789, 'digest':'sha256:'+'a'*64, 'expired':False, 'expires_at':'2099-12-31T00:00:00Z',
                  'name':f'shirube-os-{recovery.VERSION}-{recovery.SOURCE}-1',
                  'workflow_run':run_binding(RUN, 'main', HEAD)}
        with patch.dict(os.environ, env, clear=True):
            with patch.object(recovery, 'api', return_value=record):
                self.assertEqual(recovery.check_new_upload(CONTEXT), ('6789','a'*64))
            for changed in ({'name':'old-attempt'}, {'digest':'sha256:'+'b'*64}, {'expired': True},
                            {'workflow_run':run_binding(RUN, 'main', recovery.SOURCE)},
                            {'workflow_run':run_binding(recovery.ORIGINAL_RUN, 'main', HEAD)}):
                with self.subTest(changed=changed), patch.object(recovery, 'api', return_value={**record, **changed}):
                    with self.assertRaises(ValueError):
                        recovery.check_new_upload(CONTEXT)
        with patch.dict(os.environ, {}, clear=True), patch.object(recovery, 'api') as api:
            with self.assertRaises(ValueError):
                recovery.check_new_upload(CONTEXT)
            api.assert_not_called()

    def test_capacity_refuses_before_evidence_and_mutation(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(recovery, 'controller_context', return_value=(ROOT,CONTEXT)), \
                patch.object(recovery, 'state_root', return_value=Path(tmp)/'runner'), \
                patch.object(recovery.shutil, 'disk_usage') as storage, \
                patch.object(recovery, 'original_evidence') as evidence, patch.object(recovery, 'upload') as upload:
            storage.return_value.free = 0
            with self.assertRaisesRegex(ValueError, 'Not enough space'):
                recovery.stage()
            evidence.assert_not_called()
            upload.assert_not_called()

    def test_stage_uses_remote_bytes_before_aggregate_metadata_or_ready_receipt(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(recovery, 'controller_context', return_value=(ROOT,CONTEXT)), \
                patch.object(recovery, 'state_root', return_value=Path(tmp)/'runner'), \
                patch.object(recovery, 'capacity'), patch.object(recovery, 'original_evidence'), patch.object(recovery, 'draft'), \
                patch.object(recovery, 'extract_candidate'), patch.object(recovery, 'original_validator') as validate, \
                patch.object(recovery, 'original_archive', side_effect=lambda pin,dst: dst.touch()), \
                patch.object(recovery, 'upload') as upload, patch.object(recovery, 'download_asset') as download:
            names = [f'shirube-os-{recovery.VERSION}-{arch}.iso' for arch in recovery.CANDIDATES]
            records = [dict(id=i, name=n, size=1, digest='sha256:'+'a'*64) for i,n in enumerate(names,1)]
            validate.side_effect = [{'assets':[dict(name=n, size=1, sha256='a'*64)]} for n in names]
            upload.side_effect = records
            download.side_effect = ValueError('Downloaded digest differs')
            with self.assertRaisesRegex(ValueError, 'digest'):
                recovery.stage()
            self.assertEqual([c.args[-1] for c in validate.call_args_list], list(recovery.CANDIDATES))
            self.assertEqual([c.args[0].name for c in upload.call_args_list], names)
            download.assert_called_once_with(records[0], Path(tmp)/'runner/public')
            self.assertFalse((Path(tmp)/'runner/verified-draft.json').exists())

    def test_stage_verified_output_is_from_independent_downloads_and_pinned_manifests(self):
        with tempfile.TemporaryDirectory() as tmp:
            scratch = Path(tmp)/'runner'
            content, staged, observations = {}, {}, []
            assets = [('amd64', b'amd64 bytes'), ('arm64', b'arm64 bytes')]

            def asset_name(arch):
                return f'shirube-os-{recovery.VERSION}-{arch}.iso'

            def candidate_archive(pin, destination):
                arch = pin.name.removeprefix('shirube-candidate-')
                payload = dict(assets)[arch]
                with zipfile.ZipFile(destination, 'w') as archive:
                    archive.writestr('manifest.json', '{}')
                    archive.writestr('SHA256SUMS', 'fixture')
                    archive.writestr(asset_name(arch), payload)

            def validator(source, phase, root, arch=None):
                observations.append(phase + (arch or ''))
                if arch:
                    path = root/asset_name(arch)
                    self.assertEqual(path.read_bytes(), dict(assets)[arch])
                    return {'architectures': {arch: {'generation': arch*8}},
                            'assets': [{'name': path.name, 'size': path.stat().st_size,
                                        'sha256': digest(path.read_bytes())}]}
                for architecture, payload in assets:
                    self.assertEqual((root/asset_name(architecture)).read_bytes(), payload)
                    (root/f'candidate-{architecture}.json').unlink()
                for name in recovery.METADATA:
                    (root/name).write_text(name)

            def upload(path, size, sha):
                blob = path.read_bytes()
                self.assertEqual((len(blob), digest(blob)), (size, sha))
                content[path.name] = blob
                staged[path.name] = {'name':path.name, 'size':size, 'id':len(staged)+1,
                                     'digest':'sha256:'+sha,'state':'uploaded'}
                return recovery.asset_identity(staged[path.name])

            def download(asset, directory):
                name = asset['name']
                observations.append('download:'+name)
                self.assertEqual(asset, recovery.asset_identity(staged[name]))
                (directory/name).write_bytes(content[name])

            def draft(expected):
                self.assertEqual(expected, recovery.inventory(staged.values()))
                return {'id':recovery.RELEASE_ID,'draft':True,'body':'pinned','assets':list(staged.values())}

            with patch.object(recovery,'controller_context',return_value=(ROOT,CONTEXT)), \
                    patch.object(recovery,'state_root',return_value=scratch), patch.object(recovery,'capacity'), \
                    patch.object(recovery,'original_evidence'), patch.object(recovery,'draft',side_effect=draft), \
                    patch.object(recovery,'original_archive',side_effect=candidate_archive), \
                    patch.object(recovery,'original_validator',side_effect=validator), \
                    patch.object(recovery,'upload',side_effect=upload), patch.object(recovery,'download_asset',side_effect=download):
                recovery.stage()
            receipt = json.loads((scratch/'verified-draft.json').read_text())
            self.assertEqual(receipt['assets'], recovery.inventory(staged.values()))
            self.assertEqual(receipt['context'], CONTEXT)
            self.assertEqual({p.name:p.read_bytes() for p in (scratch/'public').iterdir()}, content)
            self.assertEqual(observations, ['candidateamd64','candidatearm64'] +
                         ['download:'+asset_name(arch) for arch,_ in assets] + ['aggregate'] +
                         ['download:'+name for name in recovery.METADATA])

    def test_promotion_never_repeats_or_mutates_on_stale_proof(self):
        for condition in ('success','uncertain','bad-upload','expired','changed'):
            with self.subTest(condition=condition), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                live = {'id':recovery.RELEASE_ID,'body':'release notes','draft':True,'assets':[]}
                receipt = dict(context=CONTEXT, assets={}, snapshot=actions.release_snapshot(live))
                if condition=='changed':
                    receipt['snapshot'] = actions.release_snapshot({**live,'body':'changed'})
                (root/'verified-draft.json').write_text(json.dumps(receipt))
                with patch.object(recovery,'controller_context',return_value=(ROOT,CONTEXT)), \
                        patch.object(recovery,'state_root',return_value=root), patch.object(recovery,'original_evidence'), \
                        patch.object(recovery,'check_new_upload',return_value=('123','a'*64)) as proof, \
                        patch.object(recovery,'draft',return_value=live), patch.object(recovery,'verify_published') as verify, \
                        patch.object(recovery,'utcnow',return_value=NOW), patch.object(actions,'gh') as gh:
                    if condition=='uncertain':
                        gh.side_effect = subprocess.CalledProcessError(1,'gh')
                    if condition=='bad-upload':
                        proof.side_effect = ValueError('wrong artifact')
                    if condition=='expired':
                        recovery.utcnow.return_value = datetime.fromisoformat(recovery.CANDIDATES['amd64'].expiry)
                    if condition in ('success','uncertain'):
                        recovery.promote()
                        self.assertEqual(gh.call_args.args[:4], ('api','--method','PATCH',f'repos/{recovery.REPO}/releases/{recovery.RELEASE_ID}'))
                        gh.assert_called_once()
                        verify.assert_called_once()
                        self.assertIs(verify.call_args.args[0]['draft'], False)
                    else:
                        with self.assertRaises(ValueError):
                            recovery.promote()
                        gh.assert_not_called()
                        verify.assert_not_called()


class WorkflowIsolation(unittest.TestCase):
    def test_only_exact_recovery_paths_keep_publish_seed_and_candidate_build_off(self):
        paths = {recovery.WORKFLOW,'producer/recover_release.py','tests/test_recover_release.py','docs/RELEASE.md'}
        consumers = release_plan.declared_consumers(ROOT)
        self.assertTrue({recovery.WORKFLOW,'producer/recover_release.py'} <= release_plan.CONTROL_FILES)
        self.assertFalse(relevant(paths, ROOT, ROOT))
        jobs = workflow('release.yml')['jobs']
        for files, publication, kind in [(paths,'none','focused'),
                 (paths|{'producer/recover_release_extra.py'},'hold','candidate'),
                 (paths|{'guest/new-command'},'publish','candidate'),
                 (paths|{'source-lock.json'},'publish','candidate')]:
            plan = release_plan.classify(files, consumers)
            self.assertEqual((plan['publication'],plan['automatic_execution']),(publication,kind))
            ctx = {'needs.resolve.outputs.eligible':str(publication=='publish').lower(),
                   'needs.resolve.outputs.noop':'false','needs.resolve.outputs.publication':publication,
                   'needs.resolve.outputs.automatic-execution':kind}
            self.assertEqual(expression_value(jobs['build']['if'],ctx), publication=='publish')
            self.assertEqual(expression_value(jobs['candidate-validation']['if'],ctx),
                             publication!='publish' and kind=='candidate')
        rename = actions.changed_file_paths([[{'status':'renamed','previous_filename':'guest/old',
                                               'filename':recovery.WORKFLOW}]],1)
        self.assertEqual(release_plan.classify(rename)['publication'],'publish')
        with self.assertRaisesRegex(ValueError,'Incomplete'):
            actions.changed_file_paths([[{'status':'added','filename':recovery.WORKFLOW}]],2)

    def test_no_automatic_trigger_no_rebuild_and_same_queue(self):
        raw = workflow('recover-release.yml')
        self.assertEqual(raw['on'], {'workflow_dispatch': ''})
        self.assertEqual(raw['concurrency'], workflow('release.yml')['concurrency'])
        self.assertEqual(set(raw['jobs']), {'recover'})
        job = raw['jobs']['recover']
        self.assertNotIn('uses',job)
        self.assertGreaterEqual(int(job['timeout-minutes']),120)
        self.assertNotIn('continue-on-error',job)
        steps = job['steps']
        self.assertEqual(len(steps),5)
        self.assertEqual(steps[1]['with']['ref'],recovery.SOURCE)
        self.assertTrue(all(s['with']['persist-credentials'] == 'false' for s in steps[:2]))
        self.assertEqual(steps[3]['id'],'assets')
        self.assertTrue(steps[3]['with']['name'].startswith(f'shirube-os-{recovery.VERSION}-{recovery.SOURCE}-'))
        self.assertEqual(steps[3]['with']['if-no-files-found'],'error')
        self.assertNotIn('retention-days', steps[3]['with'])  # Use normal repository retention.
        self.assertTrue(steps[4]['run'].endswith(' promote'))
        self.assertFalse(any(s.get('if') or s.get('continue-on-error') for s in steps))
