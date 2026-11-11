"""Bound transient download failures inside the isolated ISO builder."""
from pathlib import Path


RETRY_FUNCTION = '''
# Modified by michibiki-io: bounded fixed-snapshot object transport recovery.
shirube_wget () {
 PYTHONPATH=/src python3 -m producer.snapshot_fetch "$@"
}
'''



def configure_retry(path=Path('/usr/share/debootstrap/functions')):
    """Retry wget network errors only; retain URL, arguments and verification."""
    path = Path(path)
    text = path.read_text()
    replacements = {
        'wget $@ 2>&1': 'shirube_wget "$@" 2>&1',
        'wget ${NVSWITCH:+"$NVSWITCH"} "$@"':
            'shirube_wget ${NVSWITCH:+"$NVSWITCH"} "$@"',
    }
    if 'shirube_wget' in text or any(text.count(old) != 1 for old in replacements):
        raise ValueError('Unexpected debootstrap download implementation')
    for old, new in replacements.items():
        text = text.replace(old, new)
    path.write_text(RETRY_FUNCTION + text)
