"""Omit three explicitly restricted fonts unused by Debian's kbd package."""
import json
from pathlib import Path
import shutil
from .debian_sources import descriptor
from .sources import sha256

DSC = '601a34466f78611c6e0bf0ff10941b2b8e9abf93e4c84a5f90327df2ca809966'
NOTICE = '4a8bf215898889ce1f3220174ec99b55ea2e36a847203c9cdeca21cafe066016'
RULES = '96fa1f582c1996248c731599f23213ad4d2b3b0965a411da094d48fcfd166426'
EXCLUDED = {
    'data/consolefonts/Agafari-12.psfu': '3691e6993f62fb014a9cd88ba3155b6b03972cce71cc937e0673dbb0f2512436',
    'data/consolefonts/Agafari-14.psfu': '2c23ee35fb35ac8e0366421d01374428e482ada559572cc2dca14cd72f45cf1c',
    'data/consolefonts/Agafari-16.psfu': '3fe8e9e226fcb873da12de285ef5e352b4c7183bd0dc4e0bb1a22072a849f8f5',
}


def prepare(version, originals, tree, rule):
    expected = [{'member': p, 'sha256': h} for p, h in EXCLUDED.items()]
    if (version != '2.5.1-1' or rule.get('descriptor_sha256') != DSC or
            rule.get('excluded') != expected or
            rule.get('licensing_sha256') != NOTICE or
            rule.get('debian_rules_sha256') != RULES):
        raise ValueError('Unreviewed kbd preferred-source export')
    dsc = descriptor(originals, 'kbd', version)
    if sha256(dsc) != DSC:
        raise ValueError('kbd descriptor differs')
    tree = Path(tree).resolve()

    def regular(member):
        path = tree
        for part in Path(member).parts:
            path = path/part
            if path.is_symlink():
                raise ValueError('Aliased kbd export member')
        if not path.is_file() or not path.resolve().is_relative_to(tree):
            raise ValueError('Missing regular kbd export member')
        return path

    # All checks precede removal. Exact packaging installs executables, locale
    # and manuals, not these optional fonts (install-data is never invoked).
    checks = dict(EXCLUDED, **{'data/consolefonts/README.Ethiopic': NOTICE,
                              'debian/rules': RULES})
    for member, digest in checks.items():
        if sha256(regular(member)) != digest:
            raise ValueError('kbd export source or package scope differs')
    marker = tree/'SHIRUBE-SOURCE-EXPORT.json'
    origin = tree/'SHIRUBE-ORIGINAL-SOURCE.dsc'
    if any(p.exists() or p.is_symlink() for p in (marker, origin)):
        raise ValueError('kbd export destination already exists')
    for member in EXCLUDED:
        regular(member).unlink()
    shutil.copyfile(dsc, origin)
    record = {
        'kind': 'debian-kbd-preferred-source-without-restricted-unused-fonts',
        'source': 'kbd', 'version': version, 'original_descriptor_sha256': DSC,
        'excluded': expected, 'retained_license_notice_sha256': NOTICE,
        'unchanged_debian_rules_sha256': RULES,
        'modified_by': 'michibiki-io', 'modified_on': '2026-11-10',
        'reconstruction': (
            'Applied Debian preferred source, not a new signed Debian source set. '
            'Original descriptor is provenance only; raw original archives are not '
            'delivered. Only three non-commercial Agafari font assets are omitted. '
            'They are not compiled or installed by the retained Debian rules. '
            'Every executable source, header, build/test/install script, keymap, '
            'other font and copyright notice is unchanged. Build the kbd package '
            'using the retained Debian dpkg-buildpackage recipe. This export '
            'does not claim the complete optional upstream font install-data target.')}
    marker.write_text(json.dumps(record, indent=2)+'\n')
    return {'kind': record['kind'], 'record': 'source/SHIRUBE-SOURCE-EXPORT.json',
            'sha256': sha256(marker), 'original_descriptor_sha256': DSC}
