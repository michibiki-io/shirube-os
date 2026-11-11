# Copyright (c) 2026 michibiki.io; MIT. Modified 2026-11-08 for Shirube OS console policy.
#!/usr/bin/env python3
"""Extend verified Stream CLI artifacts to accept the ARM PL011 console."""
import argparse
import hashlib
import json
from pathlib import Path

OLD='(ttyS[0-9]+|hvc[0-9]+|usb[0-9]+b.*)'
NEW='(ttyS[0-9]+|ttyAMA[0-9]+|hvc[0-9]+|usb[0-9]+b.*)'
FILES=['opt/vyatta/share/vyatta-cfg/templates/system/console/device/node.def']


def patch_core(root):
    changes=[]
    for name in FILES:
        path=Path(root)/name
        original=path.read_text()
        if original.count(OLD)==1 and NEW not in original:
            updated=original.replace(OLD,NEW)
        elif original.count(NEW)==1 and OLD not in original:
            updated=original
        else:
            raise ValueError('Unexpected Stream serial-console constraint in '+name)
        updated=updated.replace('grep -e ttyS -e hvc','grep -e ttyS -e ttyAMA -e hvc')
        changes.append((path,original,updated))
    # The XML reference cache stores node types/defaults, not value constraints.
    # Extend only the upstream-generated CLI rule after validating its layout.
    result=[]
    for path,original,updated in changes:
        path.write_text(updated)
        result.append({'file':str(path.relative_to(root)),
                       'before_sha256':hashlib.sha256(original.encode()).hexdigest(),
                       'after_sha256':hashlib.sha256(updated.encode()).hexdigest()})
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',default='/')
    a=p.parse_args()
    print(json.dumps({'status':'PASS','arm_console_patch':patch_core(Path(a.root))}))
