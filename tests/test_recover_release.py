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
            self.assertEqual(recovery.upload(asset, len(data), digest(data), deadline=float('inf')), expected)
            self.assertEqual(connect.call_args.args, ('uploads.github.com',))
            method, url, _, headers = session.request.call_args.args
            self.assertEqual((method,url), ('POST', f'/repos/{recovery.REPO}/releases/{recovery.RELEASE_ID}/assets?name=verified.iso'))
            self.assertEqual(headers['Content-Length'], str(len(data)))
            response.status = 422
            with self.assertRaisesRegex(recovery.AmbiguousUpload, 'HTTP 422'):
                recovery.upload(asset, len(data), digest(data), deadline=float('inf'))
            self.assertEqual(session.request.call_count, 2)  # one per attempted input, no retry
            identity.return_value = {'draft':False, 'body':'notes'}
            with self.assertRaisesRegex(ValueError, 'no longer the original draft'):
                recovery.upload(asset, len(data), digest(data), deadline=float('inf'))
            self.assertEqual(session.request.call_count, 2)

    def test_socket_timeout_and_201_body_timeout_have_distinct_retry_eligibility(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'GH_TOKEN':'dummy-token'}), \
                patch.object(recovery,'release_identity',return_value={'draft':True,'body':'notes'}), \
                patch.object(recovery,'DRAFT_BODY_SHA256',digest(b'notes')), patch.object(recovery,'HTTPSConnection') as connect:
            f=Path(tmp)/'canonical.iso'; f.write_bytes(b'payload')
            session=connect.return_value
            session.getresponse.side_effect=TimeoutError('socket details must not appear')
            with self.assertRaisesRegex(recovery.AmbiguousUpload,'^response headers: timeout$') as fail:
                recovery.upload(f,7,digest(b'payload'),deadline=float('inf'))
            self.assertTrue(fail.exception.retryable)
            self.assertEqual(connect.call_args.kwargs['timeout'],recovery.UPLOAD_IDLE_SECONDS)
            session.getresponse.side_effect=None
            session.getresponse.return_value.status=201
            session.getresponse.return_value.read.side_effect=TimeoutError('details must not appear')
            with self.assertRaisesRegex(recovery.AmbiguousUpload,'^response body: timeout$') as fail:
                recovery.upload(f,7,digest(b'payload'),deadline=float('inf'))
            self.assertFalse(fail.exception.retryable)
            self.assertEqual(session.close.call_count,2)

    def test_slow_ownership_api_cannot_start_post_past_append_budget(self):
        now = [1]
        def identity():
            now[0] = 1900
            return {'draft':True,'body':'notes'}
        wanted={'canonical.iso':{'size':7,'digest':'sha256:'+digest(b'payload')}}
        with tempfile.TemporaryDirectory() as tmp, patch.object(recovery,'release_identity',side_effect=identity), \
                patch.object(recovery,'DRAFT_BODY_SHA256',digest(b'notes')), patch.object(recovery,'HTTPSConnection') as connect, \
                patch.object(recovery.time,'monotonic',side_effect=lambda:now[0]):
            f=Path(tmp)/'canonical.iso'; f.write_bytes(b'payload')
            with self.assertRaisesRegex(ValueError,'insufficient append time'):
                recovery.append_missing(f,wanted,{},2000)
            connect.return_value.request.assert_not_called()

    def test_capacity_covers_two_extracted_payloads_and_one_inflight_zip(self):
        sizes=[p.size for p in recovery.CANDIDATES.values()]
        need=sum(sizes)+max(sizes)+recovery.HEADROOM
        with patch.object(recovery.shutil,'disk_usage') as usage:
            usage.return_value.free=need-1
            with self.assertRaisesRegex(ValueError,'Not enough space'):
                recovery.capacity(Path('fixture'))
            usage.return_value.free=need
            recovery.capacity(Path('fixture'))

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

    def test_append_reconciles_timeout_after_commit_despite_delayed_visibility(self):
        old = {'old.iso': {'id':1,'name':'old.iso','size':1,'digest':'sha256:'+'a'*64}}
        new = {'id':2,'name':'new.iso','size':2,'digest':'sha256:'+'b'*64}
        latest = {**old,'new.iso':new}
        expected = {n:{k:a[k] for k in ('size','digest')} for n,a in latest.items()}
        failure = recovery.AmbiguousUpload('response headers: timeout', retryable=True)
        with patch.object(recovery,'upload',side_effect=failure) as upload, \
                patch.object(recovery,'read_owned_inventory',side_effect=[old,old,latest]) as read, \
                patch.object(recovery.time,'sleep') as backoff, patch.object(recovery.time,'monotonic',return_value=1):
            self.assertEqual(recovery.append_missing(Path('new.iso'),expected,old,2000),latest)
        upload.assert_called_once_with(Path('new.iso'),2,'b'*64,deadline=2000)
        self.assertEqual(read.call_count,3)
        self.assertEqual([c.args[0] for c in backoff.call_args_list], [1,5,15])

    def test_append_repeats_only_one_timeout_after_three_unchanged_absent_reads(self):
        new = {'id':2,'name':'new.iso','size':2,'digest':'sha256:'+'b'*64}
        expected = {'new.iso': {k:new[k] for k in ('size','digest')}}
        timeout = recovery.AmbiguousUpload('response headers: timeout',retryable=True)
        for result, reads, expected_second_success in [
                (new,[{}, {}, {}, {'new.iso':new}],True),
                (recovery.AmbiguousUpload('response: HTTP 422'),[{}, {}, {}, {'new.iso':new}],True),
                (timeout,[{}, {}, {}, {}, {}, {}],False)]:
            with self.subTest(result=result), patch.object(recovery,'upload',side_effect=[timeout,result]) as upload, \
                    patch.object(recovery,'read_owned_inventory',side_effect=reads), patch.object(recovery.time,'sleep'), \
                    patch.object(recovery.time,'monotonic',return_value=1):
                if expected_second_success:
                    self.assertEqual(recovery.append_missing(Path('new.iso'),expected,{},2000),{'new.iso':new})
                else:
                    with self.assertRaisesRegex(ValueError,'not confirmed'):
                        recovery.append_missing(Path('new.iso'),expected,{},2000)
                self.assertEqual(upload.call_count,2)

    def test_append_stops_on_definite_success_or_non_retryable_ambiguous_absence(self):
        new = {'id':2,'name':'new.iso','size':2,'digest':'sha256:'+'b'*64}
        expected = {'new.iso': {k:new[k] for k in ('size','digest')}}
        for response in (new, recovery.AmbiguousUpload('HTTP 502'), recovery.AmbiguousUpload('response body: timeout')):
            with self.subTest(response=response), patch.object(recovery,'upload',side_effect=[response]) as upload, \
                    patch.object(recovery,'read_owned_inventory',return_value={}) as read, patch.object(recovery.time,'sleep'), \
                    patch.object(recovery.time,'monotonic',return_value=1):
                with self.assertRaisesRegex(ValueError,'not confirmed'):
                    recovery.append_missing(Path('new.iso'),expected,{},2000)
                upload.assert_called_once()
                self.assertEqual(read.call_count,3)

    def test_failed_readback_conflicts_and_budget_never_grant_retry(self):
        before = {'old.iso':{'id':1,'name':'old.iso','size':1,'digest':'sha256:'+'a'*64}}
        new = {'id':2,'name':'new.iso','size':2,'digest':'sha256:'+'b'*64}
        other = {'id':3,'name':'other.iso','size':3,'digest':'sha256:'+'c'*64}
        expected = {n:{k:a[k] for k in ('size','digest')} for n,a in
                    {**before,'new.iso':new,'other.iso':other}.items()}
        outcomes = [RuntimeError('read failed'),
                    {**before,'other.iso':other},
                    {'new.iso':new},
                    {'old.iso':dict(before['old.iso'],id=99)},
                    {**before,'new.iso':dict(new,digest='sha256:'+'e'*64)}]
        timeout = recovery.AmbiguousUpload('response headers: timeout',retryable=True)
        for observed in outcomes:
            with self.subTest(observed=observed), patch.object(recovery,'upload',side_effect=[timeout]) as upload, \
                    patch.object(recovery,'read_owned_inventory',side_effect=[observed]), patch.object(recovery.time,'sleep'), \
                    patch.object(recovery.time,'monotonic',return_value=1):
                with self.assertRaises((ValueError,RuntimeError)):
                    recovery.append_missing(Path('new.iso'),expected,before,2000)
                upload.assert_called_once()
        with patch.object(recovery,'upload',return_value=new) as upload, \
                patch.object(recovery,'read_owned_inventory',return_value={**before,'new.iso':dict(new,id=11)}), \
                patch.object(recovery.time,'sleep'), patch.object(recovery.time,'monotonic',return_value=1):
            with self.assertRaisesRegex(ValueError,'live ID differs'):
                recovery.append_missing(Path('new.iso'),expected,before,2000)
            upload.assert_called_once()
        with patch.object(recovery,'upload') as upload, patch.object(recovery.time,'monotonic',return_value=1):
            with self.assertRaisesRegex(ValueError,'insufficient append time'):
                recovery.append_missing(Path('new.iso'),expected,before,100)
            upload.assert_not_called()
        with patch.object(recovery,'upload',side_effect=[timeout]) as upload, \
                patch.object(recovery,'read_owned_inventory',return_value=before), patch.object(recovery.time,'sleep'), \
                patch.object(recovery.time,'monotonic',side_effect=[1,1700]):
            with self.assertRaisesRegex(ValueError,'insufficient append time'):
                recovery.append_missing(Path('new.iso'),expected,before,2000)
            upload.assert_called_once()

    def test_stage_validates_full_canonical_and_remote_bytes_before_or_after_any_reentry(self):
        by_arch = {a:(f'shirube-os-{recovery.VERSION}-{a}.iso',a.encode()) for a in recovery.CANDIDATES}
        payload = dict(by_arch.values())
        canonical = {**payload, **{n:n.encode() for n in recovery.METADATA}}
        names = list(canonical)
        scenarios = [((),None), ((names[0],),None), ((names[0],names[2]),None), ((names[0],names[1],names[2]),None), (tuple(names),None),
                     ((names[0],),'unknown'), ((names[0],),'digest'), ((names[0],),'starter'),
                     ((names[0],),'zero-size'), ((names[0],),'no-digest'), ((names[0],),'duplicate-id'),
                     ((names[0],),'duplicate-name'), ((names[2],),'metadata-mismatch'),
                     ((names[0],),'arm-invalid'), ((names[0],),'remote-byte'), ((names[0],),'metadata-byte'),
                     ((names[0],),'aggregate-mismatch')]
        for seed, failure in scenarios:
            with self.subTest(seed=seed,failure=failure), tempfile.TemporaryDirectory() as tmp:
                scratch = Path(tmp)/'runner'
                remote = {n:canonical[n] for n in seed}
                stages, posted, rec = [], [], []
                def uploaded(name, blob):
                    return {'name':name, 'size':len(blob),'digest':'sha256:'+digest(blob),
                            'id':len(rec)+1, 'state':'uploaded'}
                for name in seed:
                    rec.append(uploaded(name,remote[name]))
                if failure=='unknown':
                    rec.append(uploaded('unexpected.txt',b'x'))
                elif failure in ('digest','metadata-mismatch'):
                    rec[0]['digest']='sha256:'+'9'*64
                elif failure=='starter':
                    rec[0]['state']='starter'
                elif failure=='zero-size':
                    rec[0]['size']=0
                elif failure=='no-digest':
                    rec[0]['digest']=None
                elif failure=='duplicate-id':
                    rec.append(dict(uploaded(names[1],canonical[names[1]]),id=rec[0]['id']))
                elif failure=='duplicate-name':
                    rec.append(dict(rec[0],id=99))
                def archive(pin,destination):
                    name, data=by_arch[pin.name.removeprefix('shirube-candidate-')]
                    with zipfile.ZipFile(destination,'w') as z:
                        z.writestr('manifest.json','{}'); z.writestr('SHA256SUMS','fixture')
                        z.writestr(name,data)
                def validator(source,phase,destination,arch=None):
                    if arch:
                        stages.append(arch)
                        if arch=='arm64' and failure=='arm-invalid': raise ValueError('arm rejected')
                        name,blob=by_arch[arch]
                        self.assertEqual((destination/name).read_bytes(),blob)
                        return {'assets':[{'name':name,'size':len(blob),'sha256':digest(blob)}]}
                    stages.append(destination.name+'-aggregate')
                    for name, data in payload.items(): self.assertEqual((destination/name).read_bytes(),data)
                    for a in by_arch: (destination/f'candidate-{a}.json').unlink()
                    for n in recovery.METADATA:
                        data=canonical[n]
                        if destination.name=='public' and failure=='aggregate-mismatch': data=b'wrong'
                        (destination/n).write_bytes(data)
                def upload(path,size,sha,*,deadline):
                    stages.append('upload:'+path.name); posted.append(path.name)
                    data=path.read_bytes()
                    self.assertEqual((size,sha),(len(data),digest(data)))
                    self.assertEqual(data,canonical[path.name])
                    self.assertNotIn(path.name,remote)
                    remote[path.name]=data; rec.append(uploaded(path.name,data))
                    return recovery.asset_identity(rec[-1])
                def download(record,directory):
                    name=record['name']; stages.append('download:'+name)
                    if name in payload:
                        self.assertFalse(any((scratch/'canonical'/n).exists() for n in payload))
                    else:
                        self.assertTrue((scratch/'canonical'/name).is_file())
                    if (failure=='remote-byte' and name in payload or
                            failure=='metadata-byte' and name in recovery.METADATA):
                        raise ValueError('independent download differs')
                    (directory/name).write_bytes(remote[name])
                def current():
                    return {'id':recovery.RELEASE_ID,'draft':True,'body':'pinned','assets':rec[:]}
                with patch.object(recovery,'controller_context',return_value=(ROOT,CONTEXT)), \
                        patch.object(recovery,'utcnow',return_value=NOW), patch.object(recovery,'state_root',return_value=scratch), \
                        patch.object(recovery,'capacity'), patch.object(recovery,'original_evidence'), \
                        patch.object(recovery,'owned_draft',side_effect=current), \
                        patch.object(recovery,'read_owned_inventory',side_effect=lambda:recovery.inventory(rec)), \
                        patch.object(recovery,'original_archive',side_effect=archive), \
                        patch.object(recovery,'original_validator',side_effect=validator), \
                        patch.object(recovery,'upload',side_effect=upload), patch.object(recovery,'download_asset',side_effect=download), \
                        patch.object(recovery.time,'sleep'):
                    if failure:
                        with self.assertRaises(ValueError): recovery.stage()
                        self.assertFalse((scratch/'verified-draft.json').exists())
                        if failure=='metadata-byte':
                            self.assertEqual(posted,[names[1],names[2]])  # stop before second metadata POST
                        elif failure in ('remote-byte','aggregate-mismatch'):
                            self.assertEqual(posted,[names[1]])  # payload only
                        else:
                            self.assertEqual(posted,[])  # untrusted initial state: no POST
                        continue
                    recovery.stage()
                self.assertEqual(posted,[n for n in names if n not in seed])
                self.assertEqual(stages[:3],['amd64','arm64','canonical-aggregate'])
                self.assertLess(stages.index('download:'+names[1]),stages.index('public-aggregate'))
                for metadata in recovery.METADATA:
                    self.assertGreater(stages.index('download:'+metadata),stages.index('public-aggregate'))
                    if metadata not in seed:
                        self.assertGreater(stages.index('upload:'+metadata),stages.index('public-aggregate'))
                receipt = json.loads((scratch/'verified-draft.json').read_text())
                self.assertEqual(receipt['assets'],recovery.inventory(rec))
                self.assertEqual(receipt['context'],CONTEXT)
                self.assertEqual({p.name:p.read_bytes() for p in (scratch/'public').iterdir()},canonical)

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
        resume_paths = paths-{recovery.WORKFLOW}
        resume_plan = release_plan.classify(resume_paths, consumers)
        self.assertEqual((resume_plan['publication'],resume_plan['validation'],resume_plan['automatic_execution']),
                         ('none','focused','focused'))
        self.assertFalse(relevant(resume_paths, ROOT, ROOT))
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
