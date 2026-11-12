"""Shared native template generation and checked command execution."""
from pathlib import Path
import shutil
import subprocess


def run(*args, **kwargs):
    return subprocess.run(args,check=True,**kwargs)


def generate(arch):
    root=Path('/opt/stream'); root.mkdir(parents=True)
    multi=subprocess.check_output(['dpkg-architecture','-qDEB_HOST_MULTIARCH'],text=True).strip()
    for template in Path('/src/native').glob('*.py.in'):
        (root/template.name.removesuffix('.in')).write_text(template.read_text().replace('@ARCH@',arch).replace('@MULTIARCH@',multi))
    shutil.copy2('/src/native/external-sources.lock.json',root/'external-sources.lock.json')
    return root
