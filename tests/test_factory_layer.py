import unittest
from unittest.mock import Mock
from producer.vm import require_factory_layer


class FactoryLayerTests(unittest.TestCase):
    def test_reject_changed_installed_base(self):
        guest=Mock();guest.checksum.return_value='a'*64
        require_factory_layer(guest,'/boot/image/root.squashfs','a'*64)
        with self.assertRaisesRegex(ValueError,'differs'):
            require_factory_layer(guest,'/boot/image/root.squashfs','b'*64)
        with self.assertRaisesRegex(ValueError,'required'):
            require_factory_layer(guest,'/boot/image/root.squashfs','')
