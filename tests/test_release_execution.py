"""Required scope remains conservative while automatic execution may defer it."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from producer import actions, release_plan as policy
from tests.test_workflow_cache_ci import workflow, expression_value


class ExecutionPolicyTests(unittest.TestCase):
    def test_required_scope_vs_execution_complete_semantic_matrix(self):
        for paths,publication,required,automatic in [
            (['containers/native/Dockerfile'],'none','candidate','focused'),
            (['scripts/kernel_compile_cache.py','producer/actions.py','tests/test_kernel_seed.py','docs/STAGED-BUILD.md'], 'none','candidate','focused'),
            (['.github/workflows/staged-candidate.yml','producer/release_plan.py','.github/workflows/test.yml'], 'none','candidate','focused'),
            (['producer/actions.py','tests/test_release_plan.py','README.md'],'none','focused','focused'),
            (['guest/product'],'publish','candidate','candidate'),
            (['containers/native/Dockerfile','guest/product'],'publish','candidate','candidate'),
            (['unknown','containers/native/Dockerfile'],'hold','candidate','candidate'),
            (['unknown','guest/product'],'hold','candidate','candidate'),
            (['tests/fixtures/security/README.md','scripts/run-native-stage.py'],'none','candidate','candidate'),
            (['producer/security_contract.py'],'none','candidate','candidate')]:
            with self.subTest(paths=paths):
                plan=policy.plan(paths,'a'*40,run='123',attempt='1')
                self.assertEqual((plan['publication'],plan['validation'],plan['automatic_execution']),
                                 (publication,required,automatic))
                self.assertEqual(plan['prior_guest_evidence'],'NOT_REUSED')
                pending=plan['execution_deferred_gates']
                self.assertEqual(pending,['candidate','aggregate'] if required=='candidate' and automatic=='focused' else [])
                self.assertEqual(plan['source_commit'],'a'*40)

    def test_renames_deletions_mixed_and_incomplete_inventory_fail_conservatively(self):
        for previous,new,expected in [('guest/old','docs/new.md','candidate'),
                                     ('unknown','scripts/new.py','candidate'),
                                     ('docs/old.md','scripts/new.py','focused'),
                                     ('tests/fixtures/old','scripts/new.py','candidate')]:
            paths=actions.changed_file_paths([[{'filename':new,'status':'renamed','previous_filename':previous}]],1)
            self.assertEqual(policy.classify(paths)['automatic_execution'],expected)
        for path,expected in [('guest/deleted','candidate'),('schemas/removed','candidate'),
                              ('containers/native/removed','focused')]:
            paths=actions.changed_file_paths([[{'filename':path,'status':'removed'}]],1)
            self.assertEqual(policy.classify(paths)['automatic_execution'],expected)
        malformed=[([],1),([[]],1),([[{'filename':'scripts/a','status':'modified'}]],2),
                   ([[{'filename':'scripts/a','status':'renamed'}]],1),
                   ([[{'filename':'../scripts/a','status':'modified'}]],1),
                   ([[{'filename':'scripts/a','status':'bad'}]],1),
                   ([[{'filename':'scripts/a','status':'modified'}]]*2,2),
                   ([[{'filename':'scripts/a','status':'modified'}]],True)]
        for pages,count in malformed:
            with self.subTest(pages=pages,count=count),self.assertRaises(ValueError):actions.changed_file_paths(pages,count)

    def test_deferral_never_masks_failure_cancellation_or_missing_focused(self):
        plan=policy.plan(['scripts/run-native-stage.py'],'a'*40)
        for focused in ('success','failure','cancelled','skipped',None):
            for candidate in ('success','failure','cancelled','skipped',None):
                for aggregate in ('success','failure','cancelled','skipped',None):
                    with self.subTest(focused=focused,candidate=candidate,aggregate=aggregate):
                        final,ok=policy.outcomes(plan,dict(focused=focused,candidate=candidate,aggregate=aggregate,seed='success'))
                        expected=focused=='success' and candidate not in {'failure','cancelled'} and aggregate not in {'failure','cancelled'}
                        self.assertEqual(ok,expected)
                        self.assertEqual(final['status']=='failed',not expected)
                        if expected and (candidate!='success' or aggregate!='success'):
                            self.assertEqual(final['status'],'pending-required-validation')
                            self.assertIn('required-but-deferred',final['gates'].values())
                        elif expected:self.assertEqual(final['status'],'passed-required-scope')
        # Seed-only results cannot discharge any full scope, including unknowns.
        for paths in (['scripts/run-native-stage.py'],['unknown'],['guest/product'],['schemas/acceptance']):
            final,ok=policy.outcomes(policy.plan(paths,'a'*40),{'focused':'success','seed':'success','manual':'success','guest':'success'})
            self.assertNotEqual(final['gates']['candidate'],'passed')
            self.assertNotEqual(final['gates']['aggregate'],'passed')
            self.assertNotEqual(final['gates']['publication'],'passed')
            self.assertEqual(ok,paths==['scripts/run-native-stage.py'])

    def test_forged_deferral_for_unknown_acceptance_or_product_is_not_green(self):
        for path in ('unknown','guest/product','schemas/acceptance'):
            plan=policy.plan([path],'a'*40)
            plan.update(automatic_execution='focused',execution_deferred_gates=['candidate','aggregate'])
            final,ok=policy.outcomes(plan,{'focused':'success'})
            self.assertFalse(ok);self.assertEqual(final['gates']['candidate'],'required-but-unrun')

    def test_actual_merge_workflow_expressions_select_no_full_or_seed_for_infra(self):
        jobs=workflow('release.yml')['jobs']
        self.assertFalse(any('seed' in j.get('uses','') for j in jobs.values()))
        self.assertEqual(jobs['resolve']['outputs']['automatic-execution'],'${{ steps.plan.outputs.automatic-execution }}')
        for paths in (['scripts/kernel_compile_cache.py'],['guest/product'],['unknown'],['schemas/acceptance'],['docs/a.md']):
            plan=policy.plan(paths,'a'*40)
            ctx={'needs.resolve.outputs.eligible':str(plan['publication']=='publish').lower(),
                 'needs.resolve.outputs.noop':'false','needs.resolve.outputs.publication':plan['publication'],
                 'needs.resolve.outputs.automatic-execution':plan['automatic_execution']}
            build=expression_value(jobs['build']['if'],ctx)
            candidate=expression_value(jobs['candidate-validation']['if'],ctx)
            self.assertEqual(build,plan['publication']=='publish')
            self.assertEqual(candidate,plan['publication']!='publish' and plan['automatic_execution']=='candidate')
        self.assertIn("needs.candidate-check.result == 'success'",jobs['stage']['if'])
        self.assertIn("needs.build.result == 'success'",jobs['publish']['if'])
        self.assertEqual(workflow('validate-staged.yml')['jobs']['validate-main']['with']['commit'],
                         '${{ needs.resolve.outputs.commit }}')

    def test_evidence_json_summary_warning_and_immutable_source_run_binding(self):
        for focused in ('success','failure','cancelled','skipped',None):
            with tempfile.TemporaryDirectory() as temp:
                root=Path(temp);plan=policy.plan(['scripts/run-native-stage.py'],'a'*40,run='123',attempt='1',
                    inputs={'scripts/run-native-stage.py':{'before':'b'*64,'after':'c'*64}})
                plan['seed_trigger'] = {'changed_files': 1, 'pages': [[{'filename': 'scripts/run-native-stage.py', 'status': 'modified'}]]}
                original=json.dumps(plan);(root/'plan.json').write_text(original)
                env={'SHIRUBE_PLAN_DIRECTORY':temp,'SHIRUBE_SOURCE_COMMIT':'a'*40,'GITHUB_RUN_ID':'123',
                     'GITHUB_RUN_ATTEMPT':'2','GITHUB_STEP_SUMMARY':str(root/'summary'),
                     'SHIRUBE_JOB_RESULTS':json.dumps({'resolve':{'result':focused},'candidate-validation':{'result':'skipped'},
                        'candidate-check':{'result':'skipped'},'seed':{'result':'success'}})}
                stdout=io.StringIO()
                with patch.dict(os.environ,env),patch.object(actions,'identity') as identity,contextlib.redirect_stdout(stdout):
                    if focused=='success':actions.evidence()
                    else:
                        with self.assertRaises(ValueError):actions.evidence()
                identity.assert_called_once_with('a'*40)
                final=json.loads((root/'result.json').read_text())
                self.assertEqual(final['status'],'pending-required-validation' if focused=='success' else 'failed')
                self.assertEqual(final['dependency_changes'],plan['dependency_changes'])
                self.assertEqual(final['seed_trigger'], {'changed_files': 1})
                self.assertNotIn('\"pages\"', (root/'summary').read_text())
                self.assertEqual(final['plan_run_attempt'],'1');self.assertEqual(final['evidence_run_attempt'],'2')
                self.assertIn('required-but-deferred',(root/'summary').read_text())
                self.assertIn('::warning::PENDING',stdout.getvalue())
                self.assertEqual((root/'plan.json').read_text(),original)
                for key,value in [('SHIRUBE_SOURCE_COMMIT','b'*40),('GITHUB_RUN_ID','999')]:
                    with patch.dict(os.environ,dict(env,**{key:value})),patch.object(actions,'identity'):
                        with self.assertRaisesRegex(ValueError,'source/run'):actions.evidence()
