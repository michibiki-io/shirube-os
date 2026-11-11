# Copyright (c) 2026 michibiki.io; MIT. Modified 2026-11-08 for Shirube OS console policy.
#!/usr/bin/env python3
"""Guard optional EFI video and use the firmware console on ARM64 EFI."""
import argparse
import hashlib
import json
from pathlib import Path
import re

MARKER='# SHIRUBE_ARM64_EFI_CONSOLE_V1\n'
ARM='[ "${grub_cpu}" = "arm64" -a "${grub_platform}" = "efi" ]'
ARM_SERIAL='[ "${grub_cpu}" = "arm64" -a "${grub_platform}" = "efi" -a "${console_type}" = "ttyS" ]'
BASE='usr/share/vyos/templates/grub'


def patch_template(name, text):
    if text.startswith(MARKER):
        if (ARM_SERIAL if name=='grub_vyos_version.j2' else ARM) not in text:
            raise ValueError('Incomplete ARM GRUB patch: '+name)
        return text
    if name=='grub_common.j2':
        if text.count('    insmod efi_uga\n')!=1:
            raise ValueError('Unknown EFI module template')
        text=text.replace('    insmod efi_uga\n',
            '    if [ -f "${prefix}/${grub_cpu}-${grub_platform}/efi_uga.mod" ]; then\n'
            '        insmod efi_uga\n    fi\n')
        pattern=r'function setup_serial \{\n(.*?)\n\}'
        matches=list(re.finditer(pattern,text,re.S))
        if len(matches)!=1 or 'terminal_input --append serial console' not in matches[0][1]:
            raise ValueError('Unknown serial setup function')
        old=matches[0]
        body='\n'.join('    '+line for line in old[1].splitlines())
        replacement=('function setup_serial {\n    if '+ARM+'; then\n'
            '        terminal_output console\n        terminal_input console\n'
            '    else\n'+body+'\n    fi\n}')
        text=text[:old.start()]+replacement+text[old.end():]
    elif name=='grub_compat.j2':
        start="{% if console_type == 'ttyS' %}\n"
        end='terminal_input serial console\n'
        if text.count(start)!=1 or text.count(end)!=1:
            raise ValueError('Unknown compatibility serial template')
        first=text.index(start);last=text.index(end,first)+len(end)
        old=text[first:last]
        text=text[:first]+'if '+ARM+'; then\nterminal_output console\nterminal_input console\nelse\n'+old+'fi\n'+text[last:]
    elif name=='grub_vyos_version.j2':
        old='    if [ "${console_type}" == "ttyS" ]; then\n'
        if text.count(old)!=1:
            raise ValueError('Unknown Linux console template')
        text=text.replace(old,'    if '+ARM_SERIAL+'; then\n'
            '        set console_opts="console=ttyAMA0,115200"\n'
            '    elif [ "${console_type}" == "ttyS" ]; then\n')
    else:
        raise ValueError('Unexpected GRUB template')
    return MARKER+text


def patch_grub(root):
    root=Path(root)
    changes=[]
    for name in ['grub_common.j2','grub_compat.j2','grub_vyos_version.j2']:
        path=root/BASE/name;original=path.read_text()
        changes.append((path,original,patch_template(name,original)))
    result=[]
    for path,original,updated in changes:
        path.write_text(updated)
        result.append({'file':str(path.relative_to(root)),
            'before_sha256':hashlib.sha256(original.encode()).hexdigest(),
            'after_sha256':hashlib.sha256(updated.encode()).hexdigest()})
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',default='/')
    a=p.parse_args();print(json.dumps({'status':'PASS','arm_grub_patch':patch_grub(a.root)}))
