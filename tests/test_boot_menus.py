"""Generated titles must reflect the actual selected console without ABI changes."""
from pathlib import Path
import tempfile
import unittest
from producer.branding import boot_menus


class BootMenuTests(unittest.TestCase):
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
