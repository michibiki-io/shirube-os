"""Integrity checks for the native GeoIP import and pinned public data."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from producer.geoip import verify_source, verify_database, row_digest, replace_hook


class GeoIPTests(unittest.TestCase):
    def test_pinned_original_data_and_license(self):
        lock=verify_source(Path(__file__).resolve().parents[1])
        self.assertEqual(lock['rows'],732796)
        self.assertEqual(lock['ip_versions'],{'4':362998,'6':369798})

    def test_database_checks_logical_content_not_only_row_count(self):
        with tempfile.TemporaryDirectory() as temp:
            db=Path(temp)/'data.db'
            rows=[('us','8.8.8.0-8.8.8.255',4)]
            digest,counts=row_digest(rows)
            lock={'logical_rows_sha256':digest,'ip_versions':counts}
            with sqlite3.connect(db) as conn:
                conn.execute('CREATE TABLE geoip_ranges (country_code TEXT NOT NULL, range TEXT NOT NULL, version INT NOT NULL)')
                conn.executemany('INSERT INTO geoip_ranges VALUES (?,?,?)',rows)
            self.assertEqual(verify_database(db,lock)['rows'],1)
            with sqlite3.connect(db) as conn:
                conn.execute("UPDATE geoip_ranges SET country_code='jp'")
            with self.assertRaisesRegex(ValueError,'does not match'):
                verify_database(db,lock)

    def test_changed_upstream_hook_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            hook=Path(temp)/'hook';hook.write_text('# different hook\n')
            with self.assertRaisesRegex(ValueError,'hook changed'):
                replace_hook(hook,Path(__file__).resolve().parents[1])
            self.assertEqual(hook.read_text(),'# different hook\n')
