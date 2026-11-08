"""Download retries must stay bounded and preserve non-network failures."""
from pathlib import Path
import subprocess
import tempfile
import unittest

from producer.debootstrap import RETRY_FUNCTION, configure_retry


class DebootstrapTests(unittest.TestCase):
    def test_network_failure_recovers_or_stops_after_three_attempts(self):
        for failures, status, expected_count, expected_status in [
            (2, 4, 3, 0), (9, 4, 3, 4), (1, 5, 1, 5), (1, 8, 1, 8),
        ]:
            with self.subTest(failures=failures, status=status):
                script = RETRY_FUNCTION + '''
count=0
sleep () { :; }
wget () {
 count=$((count + 1))
 [ "$#" -eq 3 ] && [ "$1" = '-O' ] && [ "$2" = 'a b' ] &&
 [ "$3" = 'https://example.invalid/fixed' ] || return 99
 [ "$count" -gt "$failures" ] || return "$failure_status"
}
shirube_wget -O 'a b' https://example.invalid/fixed
result=$?
printf '%s %s' "$count" "$result"
'''
                result = subprocess.run(
                    ['sh', '-c', 'failures=$1; failure_status=$2; ' + script,
                     'retry-test', str(failures), str(status)],
                    check=True, capture_output=True, text=True)
                self.assertEqual(result.stdout, f'{expected_count} {expected_status}')

    def test_only_expected_debootstrap_call_sites_are_modified(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'functions'
            original = 'wget $@ 2>&1\nwget ${NVSWITCH:+"$NVSWITCH"} "$@"\n'
            path.write_text(original)
            configure_retry(path)
            patched = path.read_text()
            self.assertTrue(patched.startswith(RETRY_FUNCTION))
            self.assertIn('shirube_wget "$@" 2>&1', patched)
            with self.assertRaises(ValueError): configure_retry(path)
            self.assertEqual(path.read_text(), patched)
            path.write_text(original.replace('wget $@', 'changed $@'))
            with self.assertRaises(ValueError): configure_retry(path)
