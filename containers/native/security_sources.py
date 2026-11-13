"""Component-scoped, offline security backports for authenticated Stream sources.

Copyright (c) 2026 michibiki.io; MIT License (see NOTICE).
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess


try:
    from .security_common import digest, patch_root, inputs, run_patch
    from .security_native import prepare_frr
except ImportError:
    from security_common import digest, patch_root, inputs, run_patch
    from security_native import prepare_frr

def apply(project, component, source):
    if component == 'accel-ppp':
        try:
            from .security_kernel import apply as execute
        except ImportError:
            from security_kernel import apply as execute
    else:
        try:
            from .security_native import apply as execute
        except ImportError:
            from security_native import apply as execute
    return execute(project, component, source)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('component', choices=['accel-ppp', 'update-check'])
    parser.add_argument('source', type=Path)
    parser.add_argument('--project', type=Path, default=Path('/src'))
    args = parser.parse_args()
    apply(args.project, args.component, args.source)

if __name__ == '__main__': main()
