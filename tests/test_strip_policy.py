import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from producer.strip_policy import preserve_runtime_bindings


class StripPolicyTests(unittest.TestCase):
    def test_source_bound_payloads_are_excluded_and_shell_paths_are_constrained(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mapping = root/'extra/go/exporter/shirube-corresponding-inputs/binary-info.json'
            mapping.parent.mkdir(parents=True)
            hook = root/'hook'
            original = "STRIP_EXCLUDE=`dpkg-query -L libbinutils | grep '.so'`\nprintf '%s\\n' \"$STRIP_EXCLUDE\"\n"
            hook.write_text(original)
            mapping.write_text(json.dumps([{'path': 'usr/sbin/exporter'}]))
            preserve_runtime_bindings(hook, root/'extra')
            # Exercise the generated shell assignment; no strip operation occurs.
            output = subprocess.check_output(['sh', str(hook)], text=True, stderr=subprocess.DEVNULL)
            self.assertIn('/usr/sbin/exporter', output.splitlines())
            for member in ['../etc/shadow', 'usr/../etc/shadow', 'usr/bin/$(command)', 'usr/bin/x\nother']:
                mapping.write_text(json.dumps([{'path': member}]))
                hook.write_text(original)
                with self.assertRaises(ValueError):
                    preserve_runtime_bindings(hook, root/'extra')
                self.assertEqual(hook.read_text(), original)
