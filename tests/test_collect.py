"""Source/notice binding fixtures; these do not certify a guest or license."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from producer.collect import netbird_dependencies,approval_for


class ComponentInventoryTests(unittest.TestCase):
    def test_netbird_inventory_is_bound_to_shipped_binary_and_notices(self):
        with tempfile.TemporaryDirectory() as temporary:
            base=Path(temporary);root=base/'root';(root/'etc').mkdir(parents=True)
            doc=root/'usr/share/doc/shirube-integration';doc.mkdir(parents=True)
            binary=root/'usr/libexec/shirube/netbird';binary.parent.mkdir(parents=True);binary.write_bytes(b'fixture client')
            correspondence=base/'inputs';(correspondence/'linked/inputs').mkdir(parents=True)
            archive=correspondence/'linked/inputs'/('a'*64+'.zip');archive.write_bytes(b'fixture source archive')
            notice=doc/'netbird-licenses/notices'/('a'*64+'.txt');notice.parent.mkdir(parents=True);notice.write_bytes(b'fixture module notice')
            standard=doc/'netbird-licenses/go-copyright';standard.write_bytes(b'fixture Go notice')
            digest=lambda path:hashlib.sha256(path.read_bytes()).hexdigest()
            data={'architecture':'amd64','restored_offline_component_rebuild':'PASS',
                'reference_binary_sha256':digest(binary),'compiler':'1.26.10','go_source_sha256':'b'*64,
                'go_notice_sha256':digest(standard),'linked_modules':[{'module':'example.test/module','version':'v1.0.0',
                'source_identity':'h1:fixture','source':{'path':'inputs/'+archive.name,'sha256':digest(archive)},
                'license_notice':{'path':'notices/'+notice.name,'sha256':digest(notice)}}]}
            bom=doc/'netbird-components.json';bom.write_text(json.dumps(data))
            (correspondence/'components.json').write_bytes(bom.read_bytes())
            self.assertEqual(len(netbird_dependencies(root,correspondence,'amd64')),2)
            for victim in [binary,notice,standard]:
                original=victim.read_bytes();victim.write_bytes(b'changed')
                with self.assertRaises(ValueError):netbird_dependencies(root,correspondence,'amd64')
                victim.write_bytes(original)
            with self.assertRaises(ValueError):netbird_dependencies(root,correspondence,'arm64')

    def test_source_specific_approval_does_not_overwrite_another_identity(self):
        entries={'component@1':{'source_identity':'a'},'component@1#b':{'source_identity':'b'}}
        self.assertEqual(approval_for(entries,'component','1','a')['source_identity'],'a')
        self.assertEqual(approval_for(entries,'component','1','b')['source_identity'],'b')
        self.assertNotEqual(approval_for(entries,'component','1','c')['source_identity'],'c')
