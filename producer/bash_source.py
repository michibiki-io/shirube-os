"""Avoid creating a tar output inside the directory being read by tar."""
from pathlib import Path
from .sources import sha256

ORIGINAL = 'a448c840bfd94afab3e4b0609c33e73afd22f2308156e040f284fe87a077d923'
ARCHIVE_ORIGINAL = "\ttar c --exclude='bash-src.tar' --exclude='debian' --exclude='.git' \\\n\t      --exclude='stamps' --exclude='bash' --exclude='build-bash' \\\n\t      --exclude='build-min' --exclude='build-static' \\\n\t      -f bash-src.tar .\n\tmkdir -p $(bash_src)\n\ttar x -C $(bash_src) -f bash-src.tar\n\trm -f bash-src.tar\n"
ARCHIVE = "\t# Modified by michibiki-io on 2026-11-10: stream without creating an archive in the input tree.\n\tmkdir -p $(bash_src)\n\tset -o pipefail; tar c --exclude='bash-src.tar' --exclude='debian' --exclude='.git' \\\n\t      --exclude='stamps' --exclude='bash' --exclude='build-bash' \\\n\t      --exclude='build-min' --exclude='build-static' \\\n\t      -f - . | tar x -C $(bash_src) -f -\n"


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
    if text.count(ARCHIVE_ORIGINAL)!=1 or text.count('\nbash-build:\n')!=1:
        raise ValueError('Unexpected Bash archive recipe')
    text=text.replace('\nbash-build:\n',"\nbash-build: before-build\n")
    path.write_text(text.replace(ARCHIVE_ORIGINAL,ARCHIVE))
    return {'original_rules_sha256':ORIGINAL,'delivered_rules_sha256':sha256(path),
            'change':'Order before-build before bash-build; stream tar with pipefail instead of writing an archive inside its input.'}
