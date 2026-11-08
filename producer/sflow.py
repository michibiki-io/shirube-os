"""Preserve host-sflow features while fixing its bundled JSON build dependency."""
from pathlib import Path


def prepare(source):
    rules = Path(source) / 'src/json/Makefile'
    text = rules.read_text()
    original = '$(UTILS_SHARED_VERSION): $(UTILS_OBJ)\n'
    command = '\t$(CC) -shared -o $@ $< $(CJSON_OBJ) $(UTILS_SO_LDFLAG) $(LDFLAGS)\n'
    if text.count(original + command) != 1:
        raise ValueError('Upstream cJSON utility link recipe differs')
    replacement = ('# Modified by michibiki-io 2026-11-08: declare every parallel link input.\n'
                   '$(UTILS_SHARED_VERSION): $(UTILS_OBJ) $(CJSON_OBJ)\n' + command)
    rules.write_text(text.replace(original + command, replacement))
