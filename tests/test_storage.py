"""Resource admission rejects work before a disk-writing guest is launched."""
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock

from producer.architecture import require_storage, GUEST_STORAGE_GIB


class StorageAdmissionTests(unittest.TestCase):
    def test_guest_budget_includes_the_full_virtual_disk_and_rejects_shortfall(self):
        threshold = GUEST_STORAGE_GIB * 1024**3
        self.assertGreater(GUEST_STORAGE_GIB, 50)
        with patch('shutil.disk_usage', return_value=SimpleNamespace(free=threshold - 1)):
            with self.assertRaisesRegex(ValueError, 'Fresh guest disk stage.*available'):
                require_storage(Path('/owned'), GUEST_STORAGE_GIB, phase='Fresh guest disk stage')
        with patch('shutil.disk_usage', return_value=SimpleNamespace(free=threshold)):
            require_storage(Path('/owned'), GUEST_STORAGE_GIB)

    def test_low_space_after_compiler_retirement_prevents_guest_writes(self):
        import sys
        from producer.pipeline import release_candidate
        retire = Mock()
        with patch.dict(sys.modules, {'image': SimpleNamespace(assemble=lambda *args: {'iso': Path('/owned/os.iso')})}), \
             patch('producer.pipeline.collect_iso_sources', return_value=(Path('/owned'), {'cleared': True})), \
             patch('producer.pipeline.source_set_evidence', return_value={'proof':{'status':'PASS_SOURCE_SET_ONLY'}}), \
             patch('producer.pipeline.verify_branding', return_value=True), \
             patch('producer.architecture.require_storage', side_effect=ValueError('Fresh guest disk stage has insufficient storage')) as admission, \
             patch('producer.vm.build_disks') as guests:
            with self.assertRaisesRegex(ValueError, 'insufficient storage'):
                release_candidate('/sources', '/packages', '/output', 'amd64', 'v2026.03.0', 'a'*40,
                                  SimpleNamespace(verify=None), 'tcg', retire)
        retire.assert_called_once()
        admission.assert_called_once_with(Path('/build-private'), GUEST_STORAGE_GIB, phase='Fresh guest disk stage')
        guests.assert_not_called()
