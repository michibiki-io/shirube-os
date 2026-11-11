import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from producer.go_modules import build_info
from producer.runtime_sources import dependencies
from producer.sources import sha256


class RuntimeSourceTests(unittest.TestCase):
    def test_installed_inventory_and_retained_notices_are_both_bound(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);(root/'guest/etc').mkdir(parents=True);source=root/'source';inputs=source/'shirube-corresponding-inputs';inputs.mkdir(parents=True)
            archive=inputs/'module.zip';archive.write_bytes(b'original source archive')
            notice=inputs/'notice.txt';notice.write_text('Original permission notice')
            records=[{'kind':'go-module','module':'example.org/library','version':'v1.2.3',
                      'file':archive.name,'sha256':sha256(archive),
                      'notice':{'file':notice.name,'sha256':sha256(notice)}}]
            inventory=inputs/'inventory.json';inventory.write_text(json.dumps(records))
            installed=root/'guest/usr/share/doc/example/corresponding-inputs.json';installed.parent.mkdir(parents=True);installed.write_bytes(inventory.read_bytes())
            sdk=inputs/'go.tar.gz';sdk.write_bytes(b'fixed compiler and library source')
            records.append({'kind':'go-sdk-with-standard-library-source','version':'1.23.2',
                            'file':sdk.name,'sha256':sha256(sdk),'notice':records[0]['notice']})
            inventory.write_text(json.dumps(records));installed.write_bytes(inventory.read_bytes())
            binary=root/'guest/usr/bin/example';binary.parent.mkdir(parents=True);binary.write_bytes(b'fixture binary')
            info='fixture: go1.23.2\n\tdep\texample.org/library\tv1.2.3\th1:source\n'
            reports=[{'path':'usr/bin/example','sha256':sha256(binary),'build_info':build_info(info,require_sums=False)}]
            (inputs/'binary-info.json').write_text(json.dumps(reports))
            (installed.parent/'corresponding-binaries.json').write_text(json.dumps(reports))
            with patch('producer.runtime_sources.subprocess.check_output',return_value=info):
                children=dependencies(root/'guest',source,'example')
                self.assertEqual(children[0]['name'],'go:example.org/library')
                binary.write_bytes(b'changed binary')
                with self.assertRaisesRegex(ValueError,'Go binary differs'):dependencies(root/'guest',source,'example')
            notice.write_text('replacement notice')
            with self.assertRaisesRegex(ValueError,'missing or differs'):dependencies(root/'guest',source,'example')
            installed.write_text('[]')
            with self.assertRaisesRegex(ValueError,'inventory missing or differs'):dependencies(root/'guest',source,'example')
