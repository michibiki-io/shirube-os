"""Require enabled architecture-specific El Torito entries in the assembled ISO."""
import re
import subprocess


def inspect(iso, architecture):
    if architecture not in {'amd64', 'arm64'}:
        raise ValueError('Unsupported boot media architecture')
    result=subprocess.run(['xorriso','-indev',str(iso),'-report_el_torito','plain'],
                          check=True,text=True,capture_output=True)
    modes=set(re.findall(r'El Torito boot img\s*:\s*\d+\s+(BIOS|UEFI)\s+y\s',
                         result.stdout+'\n'+result.stderr))
    expected={'BIOS','UEFI'} if architecture=='amd64' else {'UEFI'}
    if modes!=expected:
        raise ValueError('ISO boot catalog does not match required firmware modes')
    return sorted(modes)
