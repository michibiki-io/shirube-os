import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from producer import netbird


class NetbirdTests(unittest.TestCase):
    def test_controller_waits_for_the_systemd_stop_deadline(self):
        import configparser
        unit = configparser.ConfigParser(interpolation=None)
        unit.read(Path(__file__).resolve().parents[1]/'guest/shirube-netbird.service')
        with patch.object(netbird.subprocess, 'run') as run:
            netbird.unit('stop', netbird.UNIT)
        self.assertGreaterEqual(run.call_args.kwargs['timeout'],
                                unit.getint('Service', 'TimeoutStopSec')+15)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.state, self.runtime = root/'state', root/'runtime'
        self.stack = [patch.object(netbird, 'STATE', self.state), patch.object(netbird, 'RUNTIME', self.runtime)]
        for item in self.stack:
            item.start(); self.addCleanup(item.stop)

    def test_saved_config_rejects_any_setup_key(self):
        for key in ['setup_key', 'setup-key']:
            with self.assertRaises(ValueError):
                netbird.validate({key: 'not-a-real-key'})
        with self.assertRaises(ValueError):
            netbird.validate({'management_url': 'https://user:password@example.com'})

    def test_inactive_default_and_disable_preserve_identity(self):
        self.state.mkdir(); (self.state/'identity').write_text('instance-only')
        with patch.object(netbird, 'unit') as unit:
            netbird.apply(None)
            netbird.apply({'disable': ''})
            self.assertTrue(all(x.args[0] == 'stop' for x in unit.call_args_list))
        self.assertEqual((self.state/'identity').read_text(), 'instance-only')

    def test_saved_settings_restore_enrolled_peer_after_runtime_loss(self):
        self.state.mkdir()
        (self.state/'identity').write_text('instance-only')
        (self.state/'enrolled.json').write_text(json.dumps({'management_url': 'https://example.com'}))
        with patch.object(netbird, 'unit') as unit:
            netbird.apply({'management_url': 'https://example.com'})
            self.assertEqual([call.args[0] for call in unit.call_args_list], ['stop', 'start'])
        self.assertEqual(json.loads((self.runtime/'settings.json').read_text()),
                         {'management_url': 'https://example.com'})
        self.assertEqual((self.state/'identity').read_text(), 'instance-only')

    def test_changed_management_server_requires_explicit_enrollment(self):
        self.state.mkdir()
        (self.state/'enrolled.json').write_text(json.dumps({'management_url': 'https://old.example.com'}))
        with patch.object(netbird, 'unit') as unit:
            netbird.apply({'management_url': 'https://new.example.com'})
            self.assertEqual([call.args[0] for call in unit.call_args_list], ['stop'])
        self.assertEqual(json.loads((self.runtime/'settings.json').read_text()),
                         {'management_url': 'https://new.example.com'})

    def test_one_shot_key_erased_on_success_failure_and_timeout(self):
        for outcome in ['success', 'failure', 'timeout']:
            with self.subTest(outcome=outcome):
                self.runtime.mkdir(exist_ok=True)
                (self.runtime/'settings.json').write_text(json.dumps({'management_url': 'https://example.com'}))
                def invoke(argv, **kwargs):
                    self.assertNotIn('test-one-shot-token', ' '.join(argv))
                    self.assertNotIn('test-one-shot-token', str(kwargs))
                    self.assertEqual((self.runtime/'enrollment-key').read_text(), 'test-one-shot-token')
                    if outcome == 'timeout':
                        raise subprocess.TimeoutExpired(argv, 90)
                    return subprocess.CompletedProcess(argv, 0 if outcome == 'success' else 1)
                with patch.object(netbird, 'unit'), patch.object(netbird.subprocess, 'run', side_effect=invoke):
                    if outcome == 'success':
                        netbird.enroll(io.StringIO('test-one-shot-token\n'))
                    else:
                        with self.assertRaises((RuntimeError, subprocess.TimeoutExpired)):
                            netbird.enroll(io.StringIO('test-one-shot-token\n'))
                self.assertFalse((self.runtime/'enrollment-key').exists())
                for path in [*self.runtime.glob('*'), *self.state.glob('*')]:
                    self.assertNotIn('test-one-shot-token', path.read_text())
