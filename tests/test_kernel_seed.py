"""Dedicated dispatch authority and seed graph; only tiny/mocked builds."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from scripts import kernel_compile_cache as host
from tests import test_kernel_compile_cache as kernel_tests
from tests.test_workflow_cache_ci import workflow, expression_value, mappings


def fixture(arch='amd64'):
    _, run, artifact, receipt, _, job = kernel_tests.ProducerTests().fixture(arch)
    run.update(event='workflow_dispatch', path='.github/workflows/kernel-seed.yml',
               head_sha='a'*40, head_branch='main')
    receipt.pop('source_pr')
    receipt.update(schema_version=2, producer_kind='kernel-seed', workflow_sha='a'*40,
                   source_ref='refs/heads/main')
    artifact['workflow_run']['head_sha'] = 'a'*40
    job.update(name=job['name'].replace('build /', 'seed /'), head_sha='a'*40)
    return run, artifact, receipt, job


class SeedRemote:
    def __init__(self, run, artifact, receipt, job, payload=b'small fixture'):
        self.run, self.artifact, self.receipt, self.job = run, artifact, receipt, job
        self.calls = []
        receipt.update(export_sha256=hashlib.sha256(payload).hexdigest(), export_bytes=len(payload))
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            archive.writestr('export.tar', payload)
            archive.writestr('provenance.json', json.dumps(receipt))
        self.content = buffer.getvalue()
        artifact.update(id=4, name=f"shirube-kernel-compiler-v1-{receipt['architecture']}-{receipt['run_attempt']}",
                        size_in_bytes=len(self.content), digest='sha256:'+hashlib.sha256(self.content).hexdigest())

    def json(self, path):
        self.calls.append(path)
        if path.startswith('actions/workflows/release.yml/'):
            return {'workflow_runs': []}
        if path.startswith('actions/workflows/kernel-seed.yml/'):
            return {'workflow_runs': [self.run]}
        if path == 'actions/runs/10':
            return self.run
        if path == 'actions/runs/10/artifacts?per_page=100':
            return {'total_count': 1, 'artifacts': [self.artifact]}
        if path == 'actions/artifacts/4':
            return self.artifact
        if path == 'actions/runs/10/attempts/1/jobs?per_page=100':
            return {'total_count': 1, 'jobs': [self.job]}
        raise AssertionError('Unexpected API call: '+path)

    def download(self, identity, target, size):
        assert (identity, size) == (4, len(self.content))
        target.write_bytes(self.content)


class SeedAuthorityTests(unittest.TestCase):
    def test_distinct_seed_import_both_arches_and_no_main_tip_or_fake_pr_query(self):
        for arch in ('amd64', 'arm64'):
            remote = SeedRemote(*fixture(arch))
            with tempfile.TemporaryDirectory() as temp:
                self.assertEqual(host.import_cache(remote, Path(temp), arch), 'RESTORED_TRUSTED_ARTIFACT')
                self.assertEqual((Path(temp)/'seed.tar').read_bytes(), b'small fixture')
            self.assertEqual(remote.calls[0], 'actions/workflows/release.yml/runs?event=pull_request_target&status=success&per_page=10')
            self.assertIn('actions/workflows/kernel-seed.yml/runs?event=workflow_dispatch&status=success&per_page=10', remote.calls)
            self.assertIn('actions/runs/10/attempts/1/jobs?per_page=100', remote.calls)
            self.assertFalse(any('pulls/' in p or 'refs/' in p or 'branches/' in p for p in remote.calls))
            self.assertNotIn('source_pr', remote.receipt)
            self.assertNotIn('run_attempt', remote.job)  # Documented GitHub job shape.

    def test_documented_main_path_references_keep_separate_immutable_sha_binding(self):
        for top_suffix in ('','@main','@refs/heads/main'):
            for reuse_suffix in ('main','refs/heads/main','a'*40):
                run,artifact,receipt,job=fixture()
                run['path']+=top_suffix
                run['referenced_workflows'][0]['path']=host.REPO+'/.github/workflows/staged-candidate.yml@'+reuse_suffix
                remote=SeedRemote(run,artifact,receipt,job)
                host.verify_producer(remote,run,artifact,receipt,'amd64')
                run['referenced_workflows'][0]['sha']='b'*40
                with self.assertRaises(ValueError):host.verify_producer(remote,run,artifact,receipt,'amd64')
        for suffix in ('feature/cache','refs/tags/main'):
            run,artifact,receipt,job=fixture()
            run['referenced_workflows'][0]['path']=host.REPO+'/.github/workflows/staged-candidate.yml@'+suffix
            remote=SeedRemote(run,artifact,receipt,job)
            with self.assertRaises(ValueError):host.verify_producer(remote,run,artifact,receipt,'amd64')

    def test_independent_metadata_mutations_never_materialize_seed(self):
        for arch in ('amd64', 'arm64'):
            # Mutate authoritative metadata independently of the signed-by-digest receipt.
            for section, key, value in [
                (0, 'event', 'pull_request_target'), (0, 'path', '.github/workflows/validate-staged.yml'),
                (0, 'status', 'in_progress'), (0, 'conclusion', 'failure'), (0, 'conclusion', 'cancelled'),
                (0, 'head_sha', 'b'*40), (0, 'head_branch', 'feature/seed'), (0, 'run_attempt', 2),
                (0, 'repository', {'full_name':host.REPO,'id':1}),
                (0, 'head_repository', {'full_name':'fork/repo','id':1}),
                (0, 'referenced_workflows', []),
                (1, 'workflow_run', {'id':11,'repository_id':host.REPO_ID,'head_sha':'a'*40}),
                (1, 'workflow_run', {'id':10,'repository_id':1,'head_sha':'a'*40}),
                (1, 'workflow_run', {'id':10,'repository_id':host.REPO_ID,'head_sha':'b'*40}),
                (1, 'expired', True), (1, 'expires_at', '2000-01-01T00:00:00Z'),
                (1, 'created_at', '2026-01-01T00:00:00Z'),
                (2, 'source_commit', 'b'*40), (2, 'workflow_sha', 'b'*40),
                (2, 'source_ref', 'refs/heads/feature/seed'), (2, 'producer_kind', 'release'),
                (2, 'schema_version', 1), (2, 'run_attempt', 2), (2, 'run_id', 11),
                (2, 'architecture', 'foreign'),
                (3, 'name', f'validate-main / kernel ({arch}, ubuntu-24.04)'),
                (3, 'status', 'in_progress'), (3, 'conclusion', 'failure'), (3, 'conclusion', 'cancelled'),
                (3, 'head_sha', 'b'*40), (3, 'run_id', 11)]:
                values = fixture(arch)
                values[section][key] = value
                remote = SeedRemote(*values)
                with self.subTest(arch=arch, section=section, key=key, value=value), tempfile.TemporaryDirectory() as temp:
                    with self.assertRaises(ValueError):host.verify_producer(remote,*values[:3],arch)
                    try:
                        self.assertEqual(host.import_cache(remote, Path(temp), arch),'MISS')
                    except ValueError:
                        pass
                    self.assertFalse((Path(temp)/'seed.tar').exists())

    def test_receipt_claims_cannot_promote_ordinary_validation_or_release(self):
        run, artifact, receipt, job = fixture()
        for path in ('.github/workflows/release.yml', '.github/workflows/validate-staged.yml'):
            run['path'] = path
            with self.assertRaises(ValueError): host.verify_producer(SeedRemote(run,artifact,receipt,job),run,artifact,receipt,'amd64')
        run, artifact, receipt, job = fixture()
        receipt['source_pr'] = 40  # Dispatch must not invent a PR.
        with self.assertRaises(ValueError):host.verify_producer(SeedRemote(run,artifact,receipt,job),run,artifact,receipt,'amd64')

    def test_seed_receipt_cli_admission_rejects_before_api_or_write(self):
        base = dict(PRODUCER_KIND='manual-seed', KERNEL_CACHE_WRITE='true', CACHE_WRITE='false', GITHUB_EVENT_NAME='workflow_dispatch',
                    GITHUB_REPOSITORY=host.REPO, GITHUB_REF='refs/heads/main', GITHUB_SHA='a'*40,
                    GITHUB_WORKFLOW_SHA='a'*40,
                    GITHUB_WORKFLOW_REF=host.REPO+'/.github/workflows/kernel-seed.yml@refs/heads/main',
                    GITHUB_RUN_ID='10', GITHUB_RUN_ATTEMPT='1')
        for arch in ('amd64','arm64'):
            for key,value in [(None,None), ('KERNEL_CACHE_WRITE','false'), ('GITHUB_SHA','b'*40),
                              ('GITHUB_WORKFLOW_SHA','b'*40), ('GITHUB_REF','refs/heads/feature/cache'),
                              ('GITHUB_WORKFLOW_REF',host.REPO+'/.github/workflows/validate-staged.yml@refs/heads/main'),
                              ('GITHUB_REPOSITORY','fork/repo'), ('REPOSITORY_ID',1), ('GITHUB_EVENT_NAME','pull_request')]:
                with self.subTest(arch=arch,key=key), tempfile.TemporaryDirectory() as temp:
                    root=Path(temp); mount=root/'cache';mount.mkdir();(mount/'export.tar').write_bytes(b'results')
                    from tests.test_kernel_seed_automatic import measurements
                    (mount/'metrics.json').write_text(json.dumps(measurements(arch)))
                    event=root/'event';event.write_text(json.dumps({'repository':{'full_name':host.REPO,'id':value if key=='REPOSITORY_ID' else host.REPO_ID}}))
                    env=dict(base,GITHUB_EVENT_PATH=str(event))
                    if key and key!='REPOSITORY_ID':env[key]=value
                    argv=['cache','receipt','--directory',str(mount),'--architecture',arch,'--commit','a'*40]
                    with patch.dict(os.environ,env,clear=True),patch.object(host.sys,'argv',argv),patch.object(host,'API') as api:
                        if key:
                            with self.assertRaises(ValueError):host.main()
                            self.assertFalse((mount/'provenance.json').exists())
                        else:
                            host.main();receipt=json.loads((mount/'provenance.json').read_text())
                            self.assertEqual(receipt['producer_kind'],'manual-seed');self.assertNotIn('source_pr',receipt)
                        api.assert_not_called()


class SeedGraphTests(unittest.TestCase):
    def test_architecture_upload_summary_does_not_claim_run_scope_passed(self):
        steps = workflow('staged-candidate.yml')['jobs']['kernel']['steps']
        step = next(s for s in steps if 'GITHUB_STEP_SUMMARY' in s.get('run', ''))
        self.assertTrue(expression_value(step['if'], {'inputs.purpose': 'seed'}))
        self.assertFalse(expression_value(step['if'], {'inputs.purpose': 'validation'}))
        self.assertEqual(step['env']['ARCHITECTURE'], '${{ matrix.architecture }}')
        for arch in ('amd64', 'arm64'):
            # This job may finish while the other architecture fails or is pending.
            with self.subTest(arch=arch), tempfile.TemporaryDirectory() as temp:
                summary = Path(temp) / 'summary'
                env = dict(SOURCE_COMMIT='a'*40, ARCHITECTURE=arch,
                           GITHUB_RUN_ID='10', GITHUB_RUN_ATTEMPT='2',
                           GITHUB_STEP_SUMMARY=str(summary))
                subprocess.run(['bash', '-e', '-c', step['run']], env=env,
                               capture_output=True, check=True)
                text = summary.read_text()
                self.assertIn(f'Seed {arch} chain uploaded:', text)
                self.assertIn('a'*40 + ' / run 10 attempt 2', text)
                self.assertNotIn('PASSED', text)
                self.assertIn('Run-level seed scope success requires every selected architecture job to succeed '
                              'and a successful run conclusion.', text)
                self.assertIn('Full candidate/aggregate/ISO/guest/Release acceptance: NOT_RUN.', text)
                self.assertIn('Existing pending validation remains pending.', text)

    def test_dedicated_manual_graph_and_effective_permissions(self):
        caller=workflow('kernel-seed.yml')
        self.assertEqual(set(caller['on']),{'workflow_dispatch'})
        self.assertEqual(set(caller['jobs']),{'plan','seed'})
        self.assertEqual(caller['cache-mode'],'read')
        job=caller['jobs']['seed']
        self.assertEqual(job['permissions'],{'contents':'read','actions':'read'})
        self.assertEqual(job['uses'],'./.github/workflows/staged-candidate.yml')
        self.assertEqual(job['with']['commit'],'${{ github.sha }}')
        self.assertEqual(job['with']['purpose'],'seed')
        self.assertNotIn('source-ref',caller['on']['workflow_dispatch']['inputs'])
        for repo in (host.REPO,'fork/repo'):
            for ref in ('refs/heads/main','refs/heads/feature/x','refs/tags/v1'):
                self.assertEqual(expression_value(caller['jobs']['plan']['if'],{'github.repository':repo,'github.ref':ref}),
                                 repo == host.REPO and ref == 'refs/heads/main')
        jobs=workflow('staged-candidate.yml')['jobs']
        for purpose in ('seed','release','validation'):
            selected={name for name,node in jobs.items() if 'if' not in node or expression_value(node['if'],{'inputs.purpose':purpose})}
            self.assertEqual(selected,{'admission','compiler','vpp','kernel'} if purpose == 'seed' else set(jobs))
        for name in ('compiler','vpp','kernel'):
            self.assertEqual(jobs[name]['strategy']['matrix'], '${{ fromJSON(needs.admission.outputs.matrix) }}')
            self.assertEqual(jobs[name].get('permissions',job['permissions']),
                             {'contents':'read'} if name=='compiler' else job['permissions'])
        self.assertEqual(jobs['kernel']['needs'],['admission','compiler','vpp'])
        for name in ('compiler','vpp'):
            self.assertTrue(any('actions/upload-artifact@' in s.get('uses','') for s in jobs[name]['steps']))
        stage_upload=next(s for s in jobs['kernel']['steps'] if s.get('with',{}).get('name','').endswith('}-kernel'))
        self.assertFalse(expression_value(stage_upload['if'],{'inputs.purpose':'seed'}))
        self.assertTrue(expression_value(stage_upload['if'],{'inputs.purpose':'validation'}))
        for node in mappings(caller):
            self.assertNotIn('secrets',node)
            if 'GH_TOKEN' in node:
                self.assertEqual(node['GH_TOKEN'],'${{ github.token }}')
                self.assertEqual(node['PRODUCER_KIND'],'manual-seed')

    def test_executable_inline_seed_admission_and_no_component_writer(self):
        admission=workflow('staged-candidate.yml')['jobs']['admission']['steps'][0]['run']
        base=dict(SOURCE_COMMIT='a'*40,CACHE_MODE='warm',PURPOSE='seed',PRODUCER_KIND='manual-seed',ARCHITECTURES='amd64,arm64',GITHUB_SHA='a'*40,
                  GITHUB_WORKFLOW_SHA='a'*40,GITHUB_REF='refs/heads/main',GITHUB_REPOSITORY=host.REPO,
                  GITHUB_EVENT_NAME='workflow_dispatch',
                  GITHUB_WORKFLOW_REF=host.REPO+'/.github/workflows/kernel-seed.yml@refs/heads/main')
        for key,value in [(None,None),('SOURCE_COMMIT','b'*40),('GITHUB_WORKFLOW_SHA','b'*40),
                          ('GITHUB_WORKFLOW_REF',host.REPO+'/.github/workflows/validate-staged.yml@refs/heads/main'),
                          ('GITHUB_REF','refs/heads/feature/cache'),('GITHUB_REPOSITORY','fork/repo'),
                          ('REPOSITORY_ID',1),('GITHUB_EVENT_NAME','pull_request_target')]:
            for mode in ('cold','warm'):
                with tempfile.TemporaryDirectory() as temp:
                    root=Path(temp);event=root/'event';output=root/'output'
                    event.write_text(json.dumps({'repository':{'full_name':host.REPO,'id':value if key=='REPOSITORY_ID' else host.REPO_ID}}))
                    env=dict(base,CACHE_MODE=mode,GITHUB_EVENT_PATH=str(event),GITHUB_OUTPUT=str(output))
                    if key and key!='REPOSITORY_ID':env[key]=value
                    result=subprocess.run(['bash','-e','-c',admission],env=env,capture_output=True)
                    self.assertEqual(result.returncode==0,key is None)
                    self.assertEqual(output.read_text() if output.exists() else '',
                                     'cache-write=false\nkernel-write=true\nmatrix='+json.dumps({'include':[{'architecture':'amd64','runner':'ubuntu-24.04'}, {'architecture':'arm64','runner':'ubuntu-24.04-arm'}]}, separators=(',',':'))+'\n' if key is None else '')
        jobs=workflow('staged-candidate.yml')['jobs']
        ctx={'needs.admission.outputs.cache-write':'false','needs.admission.outputs.kernel-write':'true',
             'success()':True,'steps.cache.outputs.cache-hit':'false','steps.toolchain.outputs.key':'fixture',
             'github.event_name':'workflow_dispatch','inputs.cache-mode':'warm'}
        compiler=next(s for s in jobs['compiler']['steps'] if s.get('id')=='image')
        self.assertEqual(expression_value(compiler['with']['cache-to'],ctx),'')
        self.assertIn('type=gha',expression_value(compiler['with']['cache-from'],ctx))
        for name in ('native','vpp','kea','salt','runtime'):
            for step in jobs[name]['steps']:
                if step.get('uses','').startswith('actions/cache/save@') or 'GH_TOKEN' in step.get('env',{}):
                    self.assertFalse(expression_value(step['if'],ctx))
        for step in jobs['kernel']['steps']:
            if 'kernel_compile_cache.py receipt' in step.get('run','') or step.get('with',{}).get('name','').startswith('shirube-kernel-compiler-'):
                self.assertTrue(expression_value(step['if'],ctx))
