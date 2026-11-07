"""Render declared shell path roots without rewriting filenames or new paths."""
from pathlib import PurePosixPath
import re


def package_script(script, output, snapshot, stream_build, local_debs):
    roots=dict(zip(('/output','/snapshot','/stream-build','/local-debs'),
                   map(str,(output,snapshot,stream_build,local_debs))))
    for value in roots.values():
        if not re.fullmatch(r'/[A-Za-z0-9_./%+-]+',value) or '..' in PurePosixPath(value).parts:
            raise ValueError('Unsafe native recipe path')
    pattern=r'(?<![A-Za-z0-9_./-])(?:'+ '|'.join(map(re.escape,roots))+r')(?=$|[/\s\'\"])'
    return re.sub(pattern,lambda match:roots[match.group()],script)
