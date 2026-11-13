"""Preferred source selection; shared with final source collection."""
import json
import hashlib
import os
from pathlib import Path
import subprocess
from .sources import sha256
from .source_selectors import files, tracked_source_files


def vpp_source_files(root):
    """Export patched tracked source and original external inputs, not build trees."""
    root=Path(root).resolve()
    if (root/'shirube-stage-source.json').is_file():
        yield from tracked_source_files(root)
        return
    yield from tracked_source_files(root)
    cache=root/'build/external/downloads'
    if not cache.is_dir():raise ValueError('Original VPP external source inputs missing')
    found=False
    for source,relative in files(cache):
        found=True;yield source,'build/external/downloads/'+relative
    if not found:raise ValueError('Empty VPP external source input set')
