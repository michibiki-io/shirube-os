"""New queue syntax must not hide invalid or cancelling release queues."""
import importlib.util
from pathlib import Path
import unittest
import yaml

spec=importlib.util.spec_from_file_location('workflow_queue',Path(__file__).resolve().parents[1]/'scripts/check_workflow_concurrency.py')
checker=importlib.util.module_from_spec(spec);spec.loader.exec_module(checker)


class WorkflowConcurrencyTests(unittest.TestCase):
    def test_duplicate_queue_is_rejected_without_rejecting_yaml_merge_overrides(self):
        with self.assertRaises(ValueError):
            yaml.load('concurrency:\n  queue: max\n  queue: single\n',Loader=checker.UniqueLoader)
        loaded=yaml.load('defaults: &defaults {queue: single}\nconcurrency: {<<: *defaults, queue: max}\n',Loader=checker.UniqueLoader)
        checker.validate(loaded,release=True)

    def test_release_requires_a_queue_that_preserves_pending_merges(self):
        for group in [None,'release',{'group':'release'},{'group':'release','queue':'single'}]:
            with self.assertRaises(ValueError):checker.validate({'concurrency':group},release=True)
        checker.validate({'concurrency':{'group':'release','queue':'max','cancel-in-progress':False}},release=True)

    def test_root_and_job_queues_reject_unknown_modes_and_cancellation(self):
        for group in [{'queue':'unknown'},{'queue':'max','cancel-in-progress':True},
                      {'queue':'max','cancel-in-progress':'${{ github.ref != "main" }}'}]:
            for workflow in [{'concurrency':group},{'jobs':{'test':{'concurrency':group}}}]:
                with self.assertRaises(ValueError):checker.validate(workflow)
        checker.validate({'jobs':{'test':{'concurrency':{'queue':'max'}}}})
        checker.validate({'concurrency':{'queue':'single','cancel-in-progress':True}})
