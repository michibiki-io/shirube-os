"""Preferred source reuse refuses source/notice ambiguity and binary claims."""
import copy
from pathlib import Path
import runpy
import unittest
from unittest.mock import patch

api=runpy.run_path(str(Path(__file__).resolve().parents[1]/'scripts/export-source-closures.py'))


def ref(member,digest='a'*64):
    return {'asset':'source.tar.gz','member':member,'sha256':digest}


def component(name,version='1'):
    return {'name':name,'version':version,'source_package':name,'source_version':version,
            'source_identity':'a'*64,'source_origin':{},'redistributable':True,'license':'MIT',
            'sources':[ref('source/'+name)],'license_files':[ref('copyright')],
            'build_inputs':[ref('project-source.tar')],'bundled_components':[]}


class SourceClosureTests(unittest.TestCase):
    def test_public_fixture_needs_exact_canonical_asset_member_and_hash(self):
        known=api['fixture_origin'].__globals__['KNOWN']
        with patch.dict(known,{'public:fixed':{'fixture':'a'*64}},clear=True):
            wanted={'fixture':{'sha256':'a'*64}}
            self.assertIsNone(api['fixture_origin']('source.tar.gz',wanted,set()))
            vectors={('source.tar.gz','fixture','a'*64)}
            self.assertEqual(api['fixture_origin']('source.tar.gz',wanted,vectors),'public:fixed')
            wanted['fixture']['sha256']='b'*64
            self.assertIsNone(api['fixture_origin']('source.tar.gz',wanted,vectors))

    def test_mixed_fixture_origins_are_not_combined_into_a_blanket_exception(self):
        known=api['fixture_origin'].__globals__['KNOWN']
        with patch.dict(known,{'one':{'first':'a'*64},'two':{'second':'b'*64}},clear=True):
            wanted={'first':{'sha256':'a'*64},'second':{'sha256':'b'*64}}
            with self.assertRaisesRegex(ValueError,'separate source origins'):
                api['fixture_origin']('source.tar.gz',wanted,
                    {('source.tar.gz',m,r['sha256']) for m,r in wanted.items()})

    def fixture(self):
        alpha=component('alpha');vpp=component('vpp');integration=component('shirube-integration')
        integration['source_origin']={'geoip_source_sha256':'a'*64,'headless_asset_sha256':'b'*64}
        integration['bundled_components']=[component('data:dbip-country-lite')]
        return {'components':[alpha,vpp,integration]}

    def test_target_source_version_must_match_even_when_binary_name_matches(self):
        with self.assertRaisesRegex(ValueError,'version differs'):
            api['select'](self.fixture(),{'alpha':['1','arm64','alpha','2']},['alpha'],[])

    def test_missing_requested_dependency_is_not_silently_omitted(self):
        with self.assertRaisesRegex(ValueError,'closure missing'):
            api['select'](self.fixture(),{},[],[{'name':'unknown','version':'1'}])

    def test_export_selection_does_not_replace_target_architecture_or_binary_origin(self):
        original=self.fixture();before=copy.deepcopy(original)
        fragment=api['select'](original,{'alpha':['1+b2','arm64','alpha','1']},['alpha'],[])
        self.assertEqual(original,before)
        self.assertNotIn('headless_asset_sha256',fragment['geoip']['origin'])
        self.assertEqual(fragment['parents']['alpha']['version'],'1')

    def test_vpp_raw_inputs_and_architecture_recipes_are_not_imported(self):
        fixture=self.fixture();child=component('vpp-native:fixture')
        child['sources']=[ref('source/build/external/downloads/raw.tar.gz'),ref('vpp-preferred-inputs/source.tar.gz')]
        fixture['components'][1]['bundled_components']=[child]
        f=api['select'](fixture,{},[],[]);groups=api['reference_groups'](f)
        self.assertIn('vpp-preferred-inputs/source.tar.gz',groups['source.tar.gz'])
        self.assertNotIn('source/build/external/downloads/raw.tar.gz',groups['source.tar.gz'])
        # The ordinary GeoIP closure still retains its reconstruction inputs.
        self.assertIn('project-source.tar',groups['source.tar.gz'])

    def test_uncleared_vpp_dependency_cannot_inherit_parent_permission(self):
        fixture=self.fixture();child=component('vpp-native:fixture');child['redistributable']=False
        fixture['components'][1]['bundled_components']=[child]
        with self.assertRaisesRegex(ValueError,'Uncleared'):
            api['reference_groups'](api['select'](fixture,{},[],[]))

    def test_conflicting_or_escaping_member_bindings_are_rejected(self):
        fragment=api['select'](self.fixture(),{'alpha':['1','arm64','alpha','1']},['alpha'],[])
        fragment['parents']['alpha']['sources']=[ref('../escape')]
        with self.assertRaisesRegex(ValueError,'Unsafe'):
            api['reference_groups'](fragment)
        fragment['parents']['alpha']['sources']=[ref('same'),ref('same','b'*64)]
        with self.assertRaisesRegex(ValueError,'Conflicting'):
            api['reference_groups'](fragment)
