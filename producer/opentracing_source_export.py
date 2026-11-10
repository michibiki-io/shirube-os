"""Replace one saved web page with the license named by unchanged Catch code."""
import json
from pathlib import Path
import shutil
from .debian_sources import descriptor
from .sources import sha256

DSC = '09ae6b43e38df9a6798d721bd76e5c610e20865415e4f6e2efedb7a218f6e9e0'
HTML_MEMBER = '3rd_party/include/opentracing/catch2/LICENSE.txt'
HTML = 'b7d05132c435c3d69f9dead9b5f3f4a9d70b6f8b45dfa887f533b33e54dd128d'
HEADER_MEMBER = '3rd_party/include/opentracing/catch2/catch.hpp'
HEADER = '7c6ac88b2d592d11e889396d9bc5f825e6759e38b6200f339e4b6100fa320205'
LICENSE = 'c9bff75738922193e67fa726fa225535870d2aa1059f91452c411736284ad566'


def prepare(version, originals, tree, rule):
    if (version!='1.6.0-4' or rule.get('descriptor_sha256')!=DSC or
            rule.get('replacement')!={'member':HTML_MEMBER,'original_sha256':HTML,
                'notice':'LICENSES/BSL-1.0.txt','replacement_sha256':LICENSE} or
            rule.get('header')!={'member':HEADER_MEMBER,'sha256':HEADER}):
        raise ValueError('Unreviewed OpenTracing preferred-source export')
    dsc=descriptor(originals,'opentracing-cpp',version)
    if sha256(dsc)!=DSC:raise ValueError('OpenTracing descriptor differs')
    tree=Path(tree).resolve()
    def regular(member):
        path=tree
        for part in Path(member).parts:
            path=path/part
            if path.is_symlink():raise ValueError('Aliased OpenTracing export member')
        if not path.is_file() or not path.resolve().is_relative_to(tree):
            raise ValueError('Missing regular OpenTracing export member')
        return path
    html=regular(HTML_MEMBER);header=regular(HEADER_MEMBER)
    notice=Path(__file__).resolve().parents[1]/'LICENSES/BSL-1.0.txt'
    if (sha256(html)!=HTML or sha256(header)!=HEADER or notice.is_symlink() or
            not notice.is_file() or sha256(notice)!=LICENSE):
        raise ValueError('OpenTracing source or complete Boost license differs')
    marker=tree/'SHIRUBE-SOURCE-EXPORT.json';origin=tree/'SHIRUBE-ORIGINAL-SOURCE.dsc'
    if any(p.exists() or p.is_symlink() for p in (marker,origin)):
        raise ValueError('OpenTracing export destination already exists')
    html.write_bytes(notice.read_bytes())
    shutil.copyfile(dsc,origin)
    record={'kind':'debian-opentracing-preferred-source-license-text','source':'opentracing-cpp',
        'version':version,'original_descriptor_sha256':DSC,
        'replaced':[rule['replacement']], 'unchanged_header':rule['header'],
        'modified_by':'michibiki-io','modified_on':'2026-11-10',
        'reconstruction':'Applied Debian preferred source, not a new signed Debian source set. '
            'The saved GitHub HTML page is replaced only by the complete Boost1.0 license '
            'explicitly named by the unchanged Catch2.0.1 header. Every code, test, build, '
            'packaging and original copyright file remains unchanged. Raw original archives '
            'are not delivered; original descriptor is provenance only. Build with the '
            'retained native CMake/Debian recipe, then test and install normally.'}
    marker.write_text(json.dumps(record,indent=2)+'\n')
    return {'kind':record['kind'],'record':'source/SHIRUBE-SOURCE-EXPORT.json',
        'sha256':sha256(marker),'original_descriptor_sha256':DSC}
