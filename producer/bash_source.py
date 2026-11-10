"""Avoid creating a tar output inside the directory being read by tar."""
from pathlib import Path
from .sources import sha256

ORIGINAL = 'a448c840bfd94afab3e4b0609c33e73afd22f2308156e040f284fe87a077d923'
COMMAND = "\ttar c --exclude='bash-src.tar' --exclude='debian' --exclude='.git' \\\n"
NOTICE = ('\t# Modified by michibiki-io on 2026-11-10: create the excluded archive\n'
          '\t# before tar reads this directory, so its own creation cannot change it.\n'
          '\ttouch bash-src.tar\n')


def prepare(sources):
    root=Path(sources).resolve()
    path=root/'vyatta-bash_4.1-3+vyos2+current2/vyatta-bash/debian/rules'
    if not path.exists() and not path.is_symlink():
        return None  # A selected package tranche may omit bash entirely.
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
        raise ValueError('Unsafe Bash packaging source')
    if sha256(path)!=ORIGINAL:
        raise ValueError('Unexpected Bash packaging source')
    text=path.read_text()
    if text.count(COMMAND)!=1:
        raise ValueError('Unexpected Bash archive recipe')
    path.write_text(text.replace(COMMAND,NOTICE+COMMAND))
    return {'original_rules_sha256':ORIGINAL,'delivered_rules_sha256':sha256(path),
            'change':'Precreate the excluded tar output; preserve all tar error checks.'}
