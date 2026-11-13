"""Shared source/archive asset naming, size and streaming security contract."""
from pathlib import Path
import re
from .security_contract import SECRET

LIMIT = 2 * 1024**3


def filename(name):
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,199}', name):
        raise ValueError('Unsafe asset name')
    return name


def scan_secrets(path):
    """Streaming PEM/OpenSSH detection; does not claim to find every credential."""
    tail = b''
    with Path(path).open('rb') as source:
        while chunk := source.read(1024**2):
            data = tail + chunk
            if SECRET.search(data):
                raise ValueError('Private key material in release content')
            tail = data[-1024:]
