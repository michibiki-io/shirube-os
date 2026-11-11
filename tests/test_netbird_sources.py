import json
from pathlib import Path
import tempfile
import unittest
from producer.netbird_sources import internal_package_map


class InternalLicenseScopeTests(unittest.TestCase):
    def test_selected_files_are_hashed_and_directory_exceptions_stay_visible(self):
        with tempfile.TemporaryDirectory() as temporary:
            source=Path(temporary);records=[]
            for name in ['client','management/server']:
                directory=source/name;directory.mkdir(parents=True)
                (directory/'main.go').write_text('fixture selected source')
                records.append({'ImportPath':'github.com/netbirdio/netbird/'+name,
                    'Dir':str(directory),'GoFiles':['main.go']})
            text='\n'.join(map(json.dumps,records));result=internal_package_map(text,source)
            self.assertEqual(result['agpl_directory_packages'],['github.com/netbirdio/netbird/management/server'])
            before=result['packages'][0]['files'][0]['sha256']
            (source/'client/main.go').write_text('changed source')
            self.assertNotEqual(internal_package_map(text,source)['packages'][0]['files'][0]['sha256'],before)
            records[0]['GoFiles']=['../management/server/main.go']
            with self.assertRaisesRegex(ValueError,'Invalid selected'):internal_package_map(json.dumps(records[0]),source)
