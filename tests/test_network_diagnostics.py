"""Keep Nmap presentation source and installed licensing bound to exact bytes."""
from pathlib import Path
import hashlib
import tempfile
import unittest
from unittest.mock import patch
from producer import network_diagnostics as diagnostics


class NetworkDiagnosticsTests(unittest.TestCase):
    def test_repository_payloads_match_reviewed_identity(self):
        for name,digest in [('patches/vyos/force_mtu_host.sh',diagnostics.DELIVERED),
                            ('patches/nmap/shirube-path-mtu.nse',diagnostics.SCRIPT),
                            ('LICENSES/Nmap-NPSL-0.94.txt',diagnostics.NPSL)]:
            self.assertEqual(hashlib.sha256(Path(name).read_bytes()).hexdigest(),digest)

    def test_prepare_refuses_drift_and_retains_selected_tranches(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);replacement=Path('patches/vyos/force_mtu_host.sh')
            self.assertIsNone(diagnostics.prepare(root,replacement))
            original=root/'vyos-1x/vyos-1x/src/op_mode/force_mtu_host.sh'
            original.parent.mkdir(parents=True);original.write_bytes(b'upstream fixture\n')
            with self.assertRaises(ValueError):diagnostics.prepare(root,replacement)
            with patch.object(diagnostics,'ORIGINAL',hashlib.sha256(original.read_bytes()).hexdigest()):
                diagnostics.prepare(root,replacement)
            self.assertEqual(original.read_bytes(),replacement.read_bytes())
            self.assertEqual(original.stat().st_mode & 0o777,0o755)
            diagnostics.prepare(root,replacement)

    def test_install_checks_all_targets_before_writing_and_runtime_rejects_drift(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            (root/'etc').mkdir()
            notice=root/diagnostics.NOTICE_MEMBER;notice.parent.mkdir(parents=True)
            notice.write_text('unrelated')
            script=Path('patches/nmap/shirube-path-mtu.nse')
            license_file=Path('LICENSES/Nmap-NPSL-0.94.txt')
            with self.assertRaises(ValueError):diagnostics.install_script(root,script,license_file)
            self.assertFalse((root/diagnostics.SCRIPT_MEMBER).exists())
            notice.unlink();diagnostics.install_script(root,script,license_file)
            launcher=root/'usr/libexec/vyos/op_mode/force_mtu_host.sh'
            launcher.parent.mkdir(parents=True)
            launcher.write_bytes(Path('patches/vyos/force_mtu_host.sh').read_bytes())
            portscan=launcher.with_name('execute_port-scan.py');portscan.write_bytes(b'argument launcher fixture\n')
            with patch.object(diagnostics,'PORT_SCAN',hashlib.sha256(portscan.read_bytes()).hexdigest()):
                diagnostics.verify(root)
                self.assertEqual(diagnostics.retained_script(root),(root/diagnostics.SCRIPT_MEMBER,notice))
                launcher.write_text('obsolete parser')
                with self.assertRaises(ValueError):diagnostics.verify(root)
