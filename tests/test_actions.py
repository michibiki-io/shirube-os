import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from producer import actions


class ActionsTests(unittest.TestCase):
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
        for release in [{'draft':False,'target_commitish':'a'*40},{'draft':True,'target_commitish':'b'*40}]:
            with patch.object(actions,'get_release',return_value=release):
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
        with patch.dict(os.environ,{'SHIRUBE_VERSION':'v2026.03.0','SHIRUBE_SOURCE_COMMIT':'a'*40},clear=True),patch.object(actions,'identity'),patch.object(actions,'get_release',return_value={'draft':True,'target_commitish':'a'*40}),patch.object(actions,'gh') as gh:
            with self.assertRaises(ValueError):actions.promote()
            gh.assert_not_called()
