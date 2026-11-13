"""Preferred source selection; shared with final source collection."""
import json
import hashlib
import os
from pathlib import Path
import subprocess
from .sources import sha256
from .source_selectors import files, tracked_source_files


def i2util_revision_evidence(source, lock):
    """Observe the locked Git revision before exporting a key-free source tree."""
    source=Path(source)
    if (source/'shirube-stage-source.json').exists():
        raise ValueError('Static I2util export requires original Git source')
    actual=subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()
    if actual!=lock['commit']:raise ValueError('Static I2util source revision differs')
    return {'schema_version':1,'commit':actual,
            'lock_sha256':hashlib.sha256(json.dumps(lock,sort_keys=True).encode()).hexdigest(),
            'files':[{'name':name,'sha256':sha256(path)} for path,name in tracked_source_files(source)]}


def staged_i2util_files(source, lock):
    """Revalidate compiler-observed revision evidence and every retained byte.

    The enclosing stage transfer authenticates this evidence; metadata alone
    never substitutes for checking the current lock and source inventory.
    """
    marker=source/'shirube-stage-source.json'
    if marker.is_symlink() or not marker.is_file():
        raise ValueError('Static I2util staged revision evidence missing/unsafe')
    data=json.loads(marker.read_text())
    if not isinstance(data,dict) or not isinstance(data.get('revision_evidence'),dict):
        raise ValueError('Static I2util staged revision evidence missing/invalid')
    selected=list(tracked_source_files(source))
    expected={'schema_version':1,'commit':lock['commit'],
              'lock_sha256':hashlib.sha256(json.dumps(lock,sort_keys=True).encode()).hexdigest(),
              'files':[{'name':name,'sha256':sha256(path)} for path,name in selected]}
    if (data.get('kind')!='tracked' or not selected
            or data.get('revision_evidence')!=expected
            or not {'LICENSE','debian/copyright'}.issubset({name for _,name in selected})
            or any('.git' in Path(name).parts or name=='shirube-stage-source.json' for _,name in selected)):
        raise ValueError('Static I2util staged revision evidence differs')
    return selected
