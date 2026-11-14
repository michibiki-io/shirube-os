"""Independent publication and required-evidence boundary fixtures; no OS build."""
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from producer import actions, release_plan as policy

ROOT = Path(__file__).resolve().parents[1]


class TaxonomyTests(unittest.TestCase):
    def test_semantic_matrix_and_extension_families(self):
        cases = [(['guest/new-command.sh'], 'publish', 'candidate'),
                 (['source-lock.json'], 'publish', 'candidate'),
                 (['LICENSES/new.md'], 'publish', 'candidate'),
                 (['producer/branding.py'], 'publish', 'candidate'),
                 (['containers/native/Dockerfile'], 'none', 'candidate'),
                 (['producer/kernel_compile_cache.py'], 'none', 'candidate'),
                 (['producer/compile_new.py'], 'none', 'candidate'),
                 (['producer/actions.py'], 'none', 'focused'),
                 (['producer/release_plan.py'], 'none', 'focused'),
                 (['.github/workflows/release.yml'], 'none', 'focused'),
                 (['.github/workflows/test.yml'], 'none', 'focused'),
                 (['docs/new-guide.md', 'tests/test_new.py'], 'none', 'focused'),
                 (['tests/new_fixture.py'], 'none', 'focused'),
                 (['tests/fixtures/security/README.md'], 'none', 'candidate'),
                 (['schemas/new.json'], 'none', 'candidate'),
                 (['producer/security_contract.py'], 'none', 'candidate'),
                 (['producer/new_unclassified.py'], 'hold', 'candidate'),
                 (['unknown-root'], 'hold', 'candidate'),
                 (['unknown-root', 'guest/product.sh'], 'hold', 'candidate'),
                 (['guest/product.sh', 'containers/native/Dockerfile'], 'publish', 'candidate')]
        for paths, publication, validation in cases:
            with self.subTest(paths=paths):
                result = policy.classify(paths)
                self.assertEqual((result['publication'], result['validation']), (publication, validation))
                self.assertEqual(result['prior_guest_evidence'], 'NOT_REUSED')

    def test_consumer_overrides_docs_and_test_families(self):
        for path in ['docs/boot.md', 'tests/test_boot.py']:
            self.assertEqual(policy.role(path), 'docs' if path.startswith('docs') else 'test')
            self.assertEqual(policy.classify([path], {path})['validation'], 'candidate')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)/'containers/native'; root.mkdir(parents=True)
            (root/'inputs-common.json').write_text(json.dumps({'roots':['tests/test_boot.py'],
                'graph': {'producer/build.py':['docs/boot.md']}}))
            self.assertEqual(policy.declared_consumers(tmp), {'producer/build.py','tests/test_boot.py','docs/boot.md'})
            (root/'inputs-common.json').write_text('{broken')
            with self.assertRaises(ValueError): policy.declared_consumers(tmp)

    def test_real_37_inventory_is_validation_only_and_10_security_product(self):
        # Public PR #37's complete 14-path inventory, independent of clone depth.
        paths = ['.github/workflows/staged-candidate.yml', '.github/workflows/test.yml',
            'containers/native/Dockerfile', 'containers/native/inputs-kernel.json',
            'containers/native/kernel-cc', 'containers/native/package_kernel.sh',
            'docs/STAGED-BUILD.md', 'producer/compile_kernel.py',
            'producer/kernel_compile_cache.py', 'scripts/kernel_compile_cache.py',
            'scripts/run-native-stage.py', 'tests/test_kernel_compile_cache.py',
            'tests/test_release_stage_import.py', 'tests/test_workflow_cache_ci.py']
        result = policy.classify(paths, policy.declared_consumers(ROOT))
        self.assertEqual((result['publication'],result['validation']), ('none','candidate'))
        # v.10's security backports are product inputs, not compiler infrastructure.
        self.assertEqual(policy.classify(['patches/kernel/security.patch',
            'patches/vyos/security.patch'], policy.declared_consumers(ROOT))['publication'], 'publish')

    def test_complete_pagination_rename_and_deletion_union(self):
        for old,new,expected in [('docs/new.md','unknown','hold'),('unknown','docs/new.md','hold'),
                                 ('guest/old','docs/new.md','publish')]:
            pages = [[{'filename':new,'status':'renamed','previous_filename':old}],
                     [{'filename':'producer/actions.py','status':'removed'}]]
            paths=actions.changed_file_paths(pages,2)
            self.assertEqual(policy.classify(paths)['publication'], expected)
        self.assertEqual(policy.classify(actions.changed_file_paths([[{'filename':'guest/old','status':'removed'}]],1))['publication'],'publish')

    def test_required_status_is_not_inferred_from_skips_or_missing_results(self):
        for path in ['guest/product', 'containers/native/Dockerfile', 'unknown-root']:
            plan=policy.plan([path], 'a'*40)
            for actual in ['failure','cancelled','skipped',None]:
                results={'focused':'success','candidate':actual,'aggregate':'success','publication':'skipped'}
                final,ok=policy.outcomes(plan,results)
                self.assertFalse(ok)
                self.assertNotEqual(final['gates']['candidate'],'passed')
        plan=policy.plan(['docs/guide.md'], 'a'*40)
        result,ok=policy.outcomes(plan,{'focused':'success','publication':'skipped'})
        self.assertTrue(ok); self.assertEqual(result['gates']['candidate'],'skipped-not-required')
        self.assertEqual(result['prior_guest_evidence'],'NOT_REUSED')
        plan=policy.plan(['unknown-root'],'a'*40)
        result,ok=policy.outcomes(plan,{'focused':'success','candidate':'success','aggregate':'success'})
        self.assertTrue(ok);self.assertEqual(result['status'],'validated-publication-held')


class ResolutionTests(unittest.TestCase):
    def setUp(self):
        self.event={'action':'closed','number':38,'pull_request':{'merged':True,
            'changed_files':1,'merge_commit_sha':'a'*40,'base':{'ref':'main'}}}
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.file=Path(self.tmp.name)/'event';self.file.write_text(json.dumps(self.event))
        self.env={'GITHUB_EVENT_PATH':str(self.file),'GITHUB_EVENT_NAME':'pull_request_target',
                  'SHIRUBE_PUBLICATION_DECISION':'publish', 'SHIRUBE_PLAN_DIRECTORY':self.tmp.name,'SHIRUBE_SOURCE_COMMIT':'a'*40}

    def test_none_hold_and_control_never_query_or_allocate_even_on_retry(self):
        for path,expected in [('docs/new.md','none'),('tests/test_new.py','none'),
                ('producer/actions.py','none'),('containers/native/Dockerfile','none'),('new-root','hold')]:
            for attempt in ['1','2']:
                with self.subTest(path=path,attempt=attempt), patch.dict(os.environ,{**self.env,'GITHUB_RUN_ATTEMPT':attempt}), \
                    patch.object(actions,'gh',return_value=[[{'filename':path,'status':'modified'}]]) as gh, \
                    patch.object(actions,'identity') as identity, patch.object(actions,'dependency_evidence',return_value={}), \
                    patch.object(actions,'emit') as emit, patch.object(actions,'releases') as releases, \
                    patch.object(actions,'next_version') as version, patch.object(actions,'integration_preflight') as preflight:
                    actions.resolve()
                    self.assertEqual(emit.call_args.args[0]['publication'],expected)
                    self.assertEqual(emit.call_args.args[0]['version'],'v2026.03.0')
                    identity.assert_called_once_with('a'*40)
                    for side_effect in [releases,version,preflight]: side_effect.assert_not_called()
                    self.assertEqual(gh.call_count,1)
                    plan=json.loads((Path(self.tmp.name)/'plan.json').read_text())
                    self.assertTrue(plan['candidate_only']);self.assertEqual(plan['gates']['publication'],'skipped-not-required')

    def test_cumulative_unknown_blocks_before_version_allocation(self):
        with patch.dict(os.environ,self.env), patch.object(actions,'identity'), \
             patch.object(actions,'dependency_evidence',return_value={}),patch.object(actions,'emit') as emit, \
             patch.object(actions,'gh',return_value=[[{'filename':'guest/product','status':'modified'}]]) as gh, \
             patch.object(actions,'releases',return_value=[]), \
             patch.object(actions,'cumulative_history',return_value=(['unknown-root','guest/product'],[{}])), \
             patch.object(actions,'next_version') as version,patch.object(actions,'integration_preflight') as preflight:
            actions.resolve()
            self.assertEqual(emit.call_args.args[0]['publication'],'hold')
            self.assertEqual(emit.call_args.args[0]['version'],'v2026.03.0')
            version.assert_not_called();preflight.assert_not_called();self.assertEqual(gh.call_count,1)
            self.assertEqual(json.loads((Path(self.tmp.name)/'plan.json').read_text())['reason'],'cumulative-unknown-role')

    def test_each_mutation_entry_rejects_candidate_without_release_query(self):
        self.env.update(SHIRUBE_VERSION='v2026.03.0',SHIRUBE_ARCHITECTURE='amd64')
        for path in ['containers/native/Dockerfile','unknown-root','docs/new.md']:
            for command in [actions.stage,actions.publish,actions.promote]:
                with self.subTest(path=path,command=command.__name__),patch.dict(os.environ,self.env), \
                    patch.object(actions,'identity'),patch.object(actions,'gh',return_value=[[{'filename':path,'status':'modified'}]]) as gh, \
                    patch.object(actions,'releases') as releases:
                    with self.assertRaisesRegex(ValueError,'cannot enter'): command()
                    releases.assert_not_called();self.assertEqual(gh.call_count,1)

    def test_invalid_event_cannot_use_product_path_to_authorize_mutation(self):
        with patch.dict(os.environ,{**self.env,'GITHUB_EVENT_NAME':'push'}),patch.object(actions,'gh') as gh:
            with self.assertRaisesRegex(ValueError,'event required'):actions.require_product_event()
            gh.assert_not_called()


class HistoryTests(unittest.TestCase):
    def test_merge_combined_paths_do_not_duplicate_inventory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            def git(*args):return subprocess.check_output(['git',*args],cwd=root,text=True).strip()
            git('init','-q','-b','main');git('config','user.name','Fixture');git('config','user.email','fixture@example.invalid')
            file=root/'guest/product';file.parent.mkdir();file.write_text('first\nmiddle\nlast\n')
            git('add','.');git('commit','-qm','baseline');base=git('rev-parse','HEAD')
            git('checkout','-qb','side');file.write_text('side\nmiddle\nlast\n')
            git('commit','-qam','side product change')
            git('checkout','-q','main');file.write_text('first\nmiddle\nmain\n')
            git('commit','-qam','main product change')
            git('merge','--no-ff','-qm','merge product','side');head=git('rev-parse','HEAD')
            raw=git('diff-tree','-m','--no-commit-id','--name-only','-r',head)
            self.assertEqual(raw.splitlines().count('guest/product'),2)
            cwd=Path.cwd()
            try:
                os.chdir(root)
                paths,commits=actions.cumulative_history(base+'..'+head)
                self.assertEqual(paths,['guest/product']);self.assertEqual(len(commits),3)
                self.assertTrue(all('Change roles: product' in c['message'] for c in commits))
                identities=actions.dependency_evidence(paths,head)
                self.assertNotEqual(identities['guest/product']['before'],identities['guest/product']['after'])
            finally:os.chdir(cwd)

    def test_merge_does_not_reintroduce_pre_baseline_main_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            def git(*args):return subprocess.check_output(['git',*args],cwd=root,text=True).strip()
            git('init','-q','-b','main');git('config','user.name','Fixture');git('config','user.email','fixture@example.invalid')
            (root/'README.md').write_text('root');git('add','.');git('commit','-qm','root')
            git('checkout','-qb','side')
            (root/'guest').mkdir();(root/'guest/product').write_text('product')
            git('add','.');git('commit','-qm','side product')
            git('checkout','-q','main');(root/'already-published-unknown').write_text('published')
            git('add','.');git('commit','-qm','published main');baseline=git('rev-parse','HEAD')
            git('tag','baseline');git('merge','--no-ff','-qm','merge product','side')
            cwd=Path.cwd()
            try:
                os.chdir(root)
                paths,commits=actions.cumulative_history(baseline+'..HEAD')
                self.assertEqual(paths,['guest/product']);self.assertEqual(len(commits),2)
                self.assertTrue(any('publication=publish' in c['message'] for c in commits))
                all_paths,_=actions.cumulative_history('HEAD')
                self.assertIn('README.md',all_paths)  # Root commit still covered.
                self.assertIn('already-published-unknown',all_paths)
            finally:os.chdir(cwd)

    def test_feature_sync_merge_excludes_published_paths_but_keeps_merge_edits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            def git(*args):return subprocess.check_output(['git',*args],cwd=root,text=True).strip()
            git('init','-q','-b','main');git('config','user.name','Fixture');git('config','user.email','fixture@example.invalid')
            (root/'README.md').write_text('root');git('add','.');git('commit','-qm','root')
            git('checkout','-qb','side');(root/'guest').mkdir();(root/'guest/product').write_text('product')
            git('add','.');git('commit','-qm','side product')
            git('checkout','-q','main');(root/'already-published-unknown').write_text('published')
            git('add','.');git('commit','-qm','published main');baseline=git('rev-parse','HEAD')
            git('checkout','-q','side');git('merge','--no-ff','--no-commit','main')
            (root/'merge-only-unknown').write_text('merge resolution')
            git('add','.');git('commit','-qm','sync main with merge edit')
            git('checkout','-q','main');git('merge','--no-ff','-qm','merge side','side')
            cwd=Path.cwd()
            try:
                os.chdir(root)
                paths,commits=actions.cumulative_history(baseline+'..HEAD')
                self.assertEqual(paths,['guest/product','merge-only-unknown'])
                self.assertEqual(len(commits),3)
                sync=next(c for c in commits if c['subject']=='sync main with merge edit')
                self.assertIn('Change roles: unknown',sync['message'])
                self.assertEqual(policy.classify(paths)['publication'],'hold')
            finally:os.chdir(cwd)

    def test_rerun_evidence_keeps_plan_and_current_attempt_distinct(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            decision=policy.plan(['docs/example.md'],'a'*40,run='123',attempt='1')
            (root/'plan.json').write_text(json.dumps(decision))
            env={'SHIRUBE_PLAN_DIRECTORY':tmp,'SHIRUBE_JOB_RESULTS':json.dumps({'resolve':{'result':'success'}}),
                 'GITHUB_STEP_SUMMARY':str(root/'summary'),'GITHUB_RUN_ATTEMPT':'2'}
            with patch.dict(os.environ,env):actions.evidence()
            result=json.loads((root/'result.json').read_text())
            self.assertEqual(result['plan_run_attempt'],'1');self.assertEqual(result['evidence_run_attempt'],'2')
            self.assertEqual(result['status'],'passed-required-scope')
            self.assertEqual(json.loads((root/'plan.json').read_text()),decision)

    def test_workflow_keeps_validation_and_cache_writers_separate(self):
        text=(ROOT/'.github/workflows/release.yml').read_text()
        self.assertIn('plan-artifact: release-plan-${{ github.run_attempt }}',text)
        self.assertIn('name: ${{ needs.resolve.outputs.plan-artifact }}',text)
        self.assertNotIn('name: release-plan-${{ github.run_attempt }}',text.split('  evidence:',1)[1])
        self.assertIn('candidate-validation:',text);self.assertIn('candidate-check:',text)
        self.assertIn('needs: [resolve, build, candidate-validation, candidate-check, stage, publish]',text)
        inline=(ROOT/'.github/workflows/staged-candidate.yml').read_text()
        self.assertIn("and os.environ['PURPOSE'] == 'release'",inline)

class CandidateAggregateTests(unittest.TestCase):
    def test_same_run_architecture_validation_and_aggregate_are_both_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp); records={}
            for arch in ('amd64','arm64'):
                directory=source/('shirube-candidate-'+arch);directory.mkdir()
                name=arch+'.iso';(directory/name).write_bytes(arch.encode())
                records[arch]={'schema_version':1,'manifest_kind':'release','provider':'michibiki-io',
                    'project':'Shirube OS','version':'v2026.03.0','source_commit':'a'*40,
                    'source_clean':True,'upstream':{'version':'2026.03'},
                    'architectures':{arch:{}},'assets':[{'name':name}]}
            seen=[]
            def validator(root,commit,version,**kw):
                self.assertEqual((commit,version),('a'*40,'v2026.03.0'))
                if kw:
                    arch=next(iter(kw['architectures']));seen.append(arch);return records[arch]
                combined=json.loads((root/'manifest.json').read_text())
                self.assertEqual(set(combined['architectures']),{'amd64','arm64'})
                self.assertEqual(set(p.name for p in root.iterdir()),{'amd64.iso','arm64.iso','manifest.json','SHA256SUMS'})
                self.assertIn('manifest.json',(root/'SHA256SUMS').read_text());seen.append('aggregate')
            with patch.dict(os.environ,{'SHIRUBE_SOURCE_COMMIT':'a'*40,'SHIRUBE_VERSION':'v2026.03.0',
                    'SHIRUBE_CANDIDATES_DIRECTORY':tmp}),patch.object(actions,'identity'), \
                    patch.object(actions,'validate',side_effect=validator),patch.object(actions,'gh') as gh:
                actions.validate_candidates();self.assertEqual(seen,['amd64','arm64','aggregate']);gh.assert_not_called()
                for field,value in [('source_commit','b'*40),('version','v2026.03.1'),('upstream',{})]:
                    original=records['arm64'][field];records['arm64'][field]=value
                    with self.assertRaisesRegex(ValueError,'shared source'):actions.validate_candidates()
                    records['arm64'][field]=original
                with patch.object(actions,'aggregate',side_effect=ValueError('aggregate gate failed')):
                    with self.assertRaisesRegex(ValueError,'aggregate gate failed'):actions.validate_candidates()
                gh.assert_not_called()

    def test_held_or_validation_decision_cannot_reach_any_mutation(self):
        for decision in ['none','hold','']:
            with patch.dict(os.environ,{'SHIRUBE_PUBLICATION_DECISION':decision}),patch.object(actions,'gh') as gh:
                with self.assertRaisesRegex(ValueError,'resolved product'):actions.require_product_event()
                gh.assert_not_called()

class DeferredProductTests(unittest.TestCase):
    def test_none_hold_taxonomy_then_product_keeps_history_without_version_churn(self):
        """Real Git history, mocked remote effects; never builds or creates a Release."""
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'checkout';root.mkdir();event_file=Path(tmp)/'event'
            def git(*args):return subprocess.check_output(['git',*args],cwd=root,text=True).strip()
            git('init','-q');git('config','user.name','Fixture');git('config','user.email','fixture@example.invalid')
            def commit(path,text,message):
                file=root/path;file.parent.mkdir(parents=True,exist_ok=True);file.write_text(text)
                git('add','--',path);git('commit','-qm',message);return git('rev-parse','HEAD')
            baseline=commit('source-lock.json',(ROOT/'source-lock.json').read_text(),'baseline')
            records=[{'tag_name':'v2026.03.0','target_commitish':baseline,'draft':False,'prerelease':False}]
            created=[];notes=[];queries=[];current={}
            def api(*args,**kw):
                if args[:3]==('api','--paginate','--slurp') and '/files' in args[3]:
                    return [[{'filename':current['path'],'status':'modified'}]]
                if args[:3]==('api','--paginate','--slurp') and '/compare/' in args[3]:
                    return [{'base_commit':{'sha':current['base']},'total_commits':1,
                             'commits':[{'sha':current['sha'],'commit':{'message':current['message']}}]}]
                if args[:2]==('release','create'):
                    created.append(args[2]);notes.append(Path(args[args.index('--notes-file')+1]).read_text())
                    records.append({'tag_name':args[2],'target_commitish':current['sha'],'draft':True,'prerelease':False})
                    return ''
                raise AssertionError(args)
            def releases():queries.append(current['sha']);return records
            def resolve(path,text,message):
                base=git('rev-parse','HEAD');sha=commit(path,text,message)
                current.update(path=path,sha=sha,base=base,message=message)
                event_file.write_text(json.dumps({'action':'closed','number':1,'pull_request':{
                    'merged':True,'changed_files':1,'commits':1,'merge_commit_sha':sha,
                    'base':{'ref':'main','sha':base},'head':{'sha':sha}}}))
                with patch.object(actions,'emit') as output:actions.resolve()
                return output.call_args.args[0]
            real_git=actions.git
            def published_git(*args):
                return baseline if args==('rev-parse','v2026.03.0^{commit}') else real_git(*args)
            cwd=Path.cwd()
            try:
                os.chdir(root)
                with patch.object(actions,'git',side_effect=published_git), patch.dict(os.environ,{'GITHUB_EVENT_PATH':str(event_file),'GITHUB_EVENT_NAME':'pull_request_target'}), \
                        patch.object(actions,'gh',side_effect=api),patch.object(actions,'releases',side_effect=releases), \
                        patch.object(actions,'integration_preflight'):
                    infra=resolve('containers/native/Dockerfile','infra','compiler infrastructure')
                    held=resolve('unclassified-input','unknown','deferred input')
                    self.assertEqual((infra['publication'],held['publication']),('none','hold'))
                    self.assertEqual(queries,[]);self.assertEqual(created,[])
                    product=resolve('guest/product','fix','product fix deferred')
                    self.assertEqual(product['publication'],'hold');self.assertEqual(created,[])
                    original_role=policy.role
                    def extended(path,consumers=()):
                        return 'build' if path=='unclassified-input' else original_role(path,consumers)
                    with patch.object(policy,'role',side_effect=extended):
                        count=len(queries)
                        taxonomy=resolve('producer/release_plan.py','reviewed role extension','classify deferred input')
                        self.assertEqual(taxonomy['publication'],'none');self.assertEqual(len(queries),count)
                        final=resolve('guest/product','final fix','publish product')
                    self.assertEqual(final['version'],'v2026.03.1');self.assertEqual(created,['v2026.03.1'])
                    for message in ['compiler infrastructure','deferred input','product fix deferred',
                                    'classify deferred input','publish product']:
                        self.assertIn(message,notes[0])
                    self.assertIn('Change roles: build',notes[0]);self.assertEqual(final['publication'],'publish')
            finally:os.chdir(cwd)
