"""Bound transient download failures inside the isolated ISO builder."""
from pathlib import Path


RETRY_FUNCTION = '''
# Modified by michibiki-io 2026-11-08: bounded transient download retries.
shirube_wget () {
 local attempt=1 status=0
 while :; do
  wget "$@" && return 0 || status=$?
  [ "$status" -eq 4 ] && [ "$attempt" -lt 3 ] || return "$status"
  attempt=$((attempt + 1))
  echo "Shirube: retry transient download ($attempt/3)" >&2
  sleep 2
 done
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
