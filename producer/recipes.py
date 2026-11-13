"""Render declared shell path roots without rewriting filenames or new paths."""
from pathlib import PurePosixPath
import re


def package_script(script, output, snapshot, stream_build, local_debs, jobs=None):
    if jobs is not None:
        if type(jobs) is not int or not 1<=jobs<=64:raise ValueError('Invalid native job concurrency')
        script=re.sub(r'(?<![A-Za-z0-9_])-j2(?=\s|$)', '-j'+str(jobs),script)
        for old,new in [('MAKEFLAGS=-j2','MAKEFLAGS=-j'+str(jobs)),('DEB_BUILD_OPTIONS=parallel=2','DEB_BUILD_OPTIONS=parallel='+str(jobs)),('OPAMJOBS=2','OPAMJOBS='+str(jobs)),(' JOBS=2',' JOBS='+str(jobs))]:script=script.replace(old,new)
    roots=dict(zip(('/output','/snapshot','/stream-build','/local-debs'),
                   map(str,(output,snapshot,stream_build,local_debs))))
    for value in roots.values():
        if not re.fullmatch(r'/[A-Za-z0-9_./%+-]+',value) or '..' in PurePosixPath(value).parts:
            raise ValueError('Unsafe native recipe path')
    pattern=r'(?<![A-Za-z0-9_./-])(?:'+ '|'.join(map(re.escape,roots))+r')(?=$|[/\s\'\"])'
    return re.sub(pattern,lambda match:roots[match.group()],script)


def component_script(native, stage):
    """Assemble whole physical shell inputs before path/concurrency rendering."""
    from pathlib import Path
    if stage not in {'native', 'kernel'}:
        raise ValueError('No package recipe for stage')
    native = Path(native)
    script = (native / ('package_'+stage+'.sh')).read_text()
    common = (native/'package_common.sh').read_text()
    early = (native/'package_native_early.sh').read_text() if stage == 'native' else ''
    return script.replace('# @COMMON@', common.replace('# @EARLY@', early)).replace(
        '# @VALIDATE@', (native/'package_validate.sh').read_text())
