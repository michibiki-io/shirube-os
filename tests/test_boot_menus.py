"""Generated titles must reflect the actual selected console without ABI changes."""
from pathlib import Path
import tempfile
import unittest
import subprocess
from unittest.mock import patch
from producer.branding import boot_menus


class BootMenuTests(unittest.TestCase):
    def test_catalog_rejects_efi_only_amd64_and_disabled_bios_entries(self):
        from producer.boot_media import inspect
        efi='El Torito boot img : 2 UEFI y none 0x0000 0x00 10368 95\n'
        bios='El Torito boot img : 1 BIOS y none 0x0000 0x00 4 2687\n'
        with patch('producer.boot_media.subprocess.run',return_value=subprocess.CompletedProcess([],0,efi,'')):
            self.assertEqual(inspect('fixture.iso','arm64'),['UEFI'])
            with self.assertRaises(ValueError):inspect('fixture.iso','amd64')
        for catalog,passes in [(bios+efi,True),(bios.replace('BIOS y','BIOS n')+efi,False)]:
            with patch('producer.boot_media.subprocess.run',return_value=subprocess.CompletedProcess([],0,'',catalog)):
                if passes:self.assertEqual(inspect('fixture.iso','amd64'),['BIOS','UEFI'])
                else:
                    with self.assertRaises(ValueError):inspect('fixture.iso','amd64')

    def test_generated_titles_and_hostname_keep_kernel_paths_and_console_order(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);path=root/'boot/grub/grub.cfg';path.parent.mkdir(parents=True)
            entries=[]
            for name,args in [('Live system (vyos)','console=ttyS0,115200 console=tty0'),
                ('Live system (vyos fail-safe mode)','nomodeset console=ttyS0,115200 console=tty0'),
                ('Live system (vyos)','console=tty0 console=ttyS0,115200')]:
                entries.append('menuentry "'+name+'" {\n linux /live/vmlinuz-6.6.128-vyos hostname=vyos '+args+'\n initrd /live/initrd.img-6.6.128-vyos\n}\n')
            path.write_text(''.join(entries))
            self.assertEqual(boot_menus(root,'amd64','v2026.03.0'),3)
            text=path.read_text()
            self.assertIn('Shirube OS v2026.03.0 Live (local console)',text)
            self.assertIn('Live (local console, safe mode)',text)
            self.assertIn('Live (serial console)',text)
            self.assertIn('--hotkey=s',text)
            self.assertNotIn('hostname=vyos',text)
            self.assertEqual(text.count('/live/vmlinuz-6.6.128-vyos'),3)
            self.assertEqual(text.count('console=ttyS0,115200 console=tty0'),2)
            self.assertEqual(text.count('console=tty0 console=ttyS0,115200'),1)
            boot_menus(root,'amd64','v2026.03.0')
            self.assertEqual(path.read_text(),text)

    def test_arm_duplicate_local_entry_gets_explicit_pl011_serial_selection(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);path=root/'boot/grub/grub.cfg';path.parent.mkdir(parents=True)
            entry='menuentry "Live system (vyos)" --hotkey=l {\n linux /live/vmlinuz-6.6.128-vyos hostname=vyos console=ttyAMA0,115200 console=tty0\n initrd /live/initrd.img-6.6.128-vyos\n}\n'
            path.write_text('set default=0\n'+entry+entry.replace('--hotkey=l ',''))
            boot_menus(root,'arm64','v2026.03.0');text=path.read_text()
            self.assertIn('set default=shirube-live-serial',text)
            self.assertEqual(text.count('--id=shirube-live-serial'),1)
            self.assertEqual(text.count('Live (serial console)'),1)
            self.assertEqual(text.count('console=tty0 console=ttyAMA0,115200'),1)
            self.assertEqual(text.count('--hotkey=s'),1)
            self.assertEqual(text.count('/live/vmlinuz-6.6.128-vyos'),2)
            boot_menus(root,'arm64','v2026.03.0');self.assertEqual(path.read_text(),text)
