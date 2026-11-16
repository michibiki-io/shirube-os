"""Writer-only metadata visibility retry contracts; no network or real waits."""
import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch
from urllib.error import HTTPError, URLError

from scripts import kernel_compile_cache as host
from tests import test_kernel_seed_automatic as automatic


def missing(code=404):
    return HTTPError('https://api.github.com/repos/fixture/actions/artifacts/4',
                     code, 'fixture', {}, io.BytesIO(b'private remote body'))


class MetadataRetryTests(unittest.TestCase):
    def run_lookup(self, responses, *, deadline=600, now=None, overshoot=0):
        now = [0] if now is None else now
        sleeps = []
        def sleep(delay):
            sleeps.append(delay)
            now[0] += delay + overshoot
        api = SimpleNamespace(deadline=deadline, json=Mock(side_effect=responses))
        return api, now, sleeps, patch.object(host.time, 'monotonic', side_effect=lambda: now[0]), patch.object(host.time, 'sleep', side_effect=sleep)

    def test_immediate_and_each_transient_404_return_identical_metadata(self):
        metadata = {'id': 4}
        for failures in range(5):
            with self.subTest(failures=failures):
                errors = [missing() for _ in range(failures)]
                api, _, sleeps, clock, sleeper = self.run_lookup([*errors, metadata])
                with clock, sleeper:
                    self.assertIs(host.uploaded_export_metadata(api, 4), metadata)
                self.assertEqual(api.json.call_args_list, [call('actions/artifacts/4')] * (failures+1))
                self.assertEqual(sleeps, [1, 2, 4, 8][:failures])
                self.assertTrue(all(e.fp is None or e.fp.closed for e in errors))

    def test_persistent_404_is_five_requests_and_four_bounded_sleeps(self):
        errors = [missing() for _ in range(5)]
        api, _, sleeps, clock, sleeper = self.run_lookup(errors)
        with clock, sleeper, self.assertRaises(HTTPError) as raised:
            host.uploaded_export_metadata(api, 4)
        self.assertIs(raised.exception, errors[-1])
        self.assertEqual(api.json.call_count, 5)
        self.assertEqual(sleeps, [1, 2, 4, 8])
        self.assertTrue(all(e.fp is None or e.fp.closed for e in errors))

    def test_expired_initial_budget_starts_neither_request_nor_sleep(self):
        api, _, sleeps, clock, sleeper = self.run_lookup([{'id': 4}], deadline=0)
        with clock, sleeper, self.assertRaises(TimeoutError):
            host.uploaded_export_metadata(api, 4)
        api.json.assert_not_called()
        self.assertEqual(sleeps, [])

    def test_insufficient_budget_does_not_begin_sleep_or_next_request(self):
        for budget in (0.5, 1):
            with self.subTest(budget=budget):
                api, _, sleeps, clock, sleeper = self.run_lookup([missing()], deadline=budget)
                with clock, sleeper, self.assertRaises(TimeoutError):
                    host.uploaded_export_metadata(api, 4)
                self.assertEqual(api.json.call_count, 1)
                self.assertEqual(sleeps, [])

    def test_request_consumes_budget_and_sleep_overshoot_stops_next_request(self):
        api, now, sleeps, clock, sleeper = self.run_lookup([missing()], deadline=1)
        def expire(path):
            now[0] = 1
            raise missing()
        api.json.side_effect = expire
        with clock, sleeper, self.assertRaises(TimeoutError):
            host.uploaded_export_metadata(api, 4)
        self.assertEqual(api.json.call_count, 1)
        self.assertEqual(sleeps, [])
        api, _, sleeps, clock, sleeper = self.run_lookup([missing(), {'id': 4}], deadline=2, overshoot=1)
        with clock, sleeper, self.assertRaises(TimeoutError):
            host.uploaded_export_metadata(api, 4)
        self.assertEqual(api.json.call_count, 1)
        self.assertEqual(sleeps, [1])

    def test_non_404_transport_parse_and_other_failures_are_never_retried(self):
        for error in [missing(c) for c in (401, 403, 500, 503)] + [URLError('fixture'),
                      TimeoutError('fixture'), json.JSONDecodeError('fixture', '', 0), ValueError('fixture')]:
            with self.subTest(error=type(error).__name__, code=getattr(error, 'code', None)):
                api, _, sleeps, clock, sleeper = self.run_lookup([error])
                with clock, sleeper, self.assertRaises(type(error)) as raised:
                    host.uploaded_export_metadata(api, 4)
                self.assertIs(raised.exception, error)
                self.assertEqual(api.json.call_count, 1)
                self.assertEqual(sleeps, [])


class WriterRetryTests(unittest.TestCase):
    def test_cli_transient_404_preserves_validated_index_for_all_kinds(self):
        cli = automatic.IndexCLITests()
        for kind in host.PRODUCER_KINDS:
            for arch in ('amd64', 'arm64'):
                with self.subTest(kind=kind, arch=arch):
                    remote = automatic.IndexedRemote(kind, arch)
                    original = remote.json
                    attempts = []
                    def query(path):
                        attempts.append(path)
                        if len(attempts) == 1:
                            raise missing()
                        return original(path)
                    remote.json = query
                    with patch.object(host.time, 'sleep') as sleep:
                        cli.invoke(remote, *cli.context(remote))
                    self.assertEqual(attempts[:2], ['actions/artifacts/4'] * 2)
                    sleep.assert_called_once_with(1)

    def test_cli_exhaustion_and_invalid_metadata_leave_no_index(self):
        cli = automatic.IndexCLITests()
        for case in ('404', 'expired', 'non404', 'parse', 'invalid', 'invalid-after404'):
            with self.subTest(case=case):
                remote = automatic.IndexedRemote()
                original = remote.json
                if case == 'expired':
                    remote.deadline = 0
                if case.startswith('invalid'):
                    remote.export['digest'] = 'sha256:' + '0'*64
                errors = {'404': [missing() for _ in range(5)], 'non404': [missing(403)],
                          'parse': [json.JSONDecodeError('fixture', '', 0)],
                          'invalid-after404': [missing(), remote.export]}
                remote.json = Mock(side_effect=errors[case]) if case in errors else Mock(side_effect=original)
                expected = HTTPError if case in ('404', 'non404') else TimeoutError if case == 'expired' else ValueError
                with patch.object(host.time, 'sleep') as sleep:
                    cli.invoke(remote, *cli.context(remote), reject=True, error=expected)
                self.assertEqual(remote.json.call_count, 5 if case == '404' else 2 if case == 'invalid-after404' else 0 if case == 'expired' else 1)
                self.assertEqual(sleep.call_args_list, [call(1), call(2), call(4), call(8)] if case == '404' else [call(1)] if case == 'invalid-after404' else [])
