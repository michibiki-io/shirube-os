"""native compile source/export recipe. Copyright (c) 2026 michibiki.io; MIT."""
import json
from pathlib import Path
import subprocess
from .source_selectors import files, tracked_source_files
from .native_stages import copy_selected, snapshot_files
EXTRAS = {'native': ()}
ROOTS = {'native': ()}
SNAPSHOTS = {}
GIT_SOURCES = {'native': ()}

def export_material(stage, sources, packages, destination, architecture):
    material = destination / "material"
    from .ocaml_sources import retain
    ocaml = material / 'ocaml'
    ocaml.mkdir()
    records, components = retain(Path('/opt/opam'), ocaml / 'sources.tar.gz', Path('/build-private/ocaml-export'), files)
    copy_selected(files(Path('/build-private/ocaml-export')), ocaml / 'notices')
    (ocaml / 'receipt.json').write_text(json.dumps({'records': records, 'components': components}, default=str) + '\n')


def prepare(jobs, sources, architecture):
    from native.security_native import prepare_frr
    for job in jobs:
        if job.get('recipe') == 'frr':
            prepare_frr(Path('/src'), sources/job['source'])

    default=sources/'vyos-1x/vyos-1x/data/config.boot.default'
    if default.is_file():
        console='ttyAMA0' if architecture=='arm64' else 'ttyS0'
        default.write_text('// Modified by michibiki-io for Shirube OS on 2026-11-08.\ninterfaces {\n ethernet eth0 { address dhcp }\n loopback lo {}\n}\nsystem {\n host-name shirube-os\n console { device '+console+' { speed 115200 } }\n}\n')
    from producer.ocaml_sources import seed_download_cache
    cached=seed_download_cache(Path('/public-inputs/opam-download-cache'),Path('/opt/opam/download-cache'))
    if cached:print('OCAML INPUT CACHE: '+str(cached)+' verified public sources',flush=True)


def receipt(job, output, exported):
    import shutil
    security_component = {'frr': 'frr', 'vyos-1x': 'update-check'}.get(job.get('recipe'))
    if security_component:
        receipt = output/'source/shirube-corresponding-inputs'/('security-'+security_component)/'receipt.json'
        if not receipt.is_file():
            raise ValueError('Missing package security patch receipt')
        shutil.copy2(receipt, exported/'security-patches.json')
