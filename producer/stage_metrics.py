"""Sanitized stage measurements with explicit peak/sampling semantics."""
from contextlib import contextmanager
import json
from pathlib import Path
import shutil
import threading
import time


@contextmanager
def measure(stage, architecture, destination, storage=Path('/build-private'), *, details=None):
    started = time.monotonic()
    stop = threading.Event(); samples = []
    def sample():
        while not stop.is_set():
            usage = shutil.disk_usage(storage)
            samples.append({'elapsed_seconds': round(time.monotonic()-started, 3),
                            'filesystem_used_bytes': usage.used, 'filesystem_free_bytes': usage.free})
            stop.wait(2)
    thread = threading.Thread(target=sample); thread.start()
    status = 'FAIL'
    try:
        yield
        status = 'PASS'
    finally:
        stop.set(); thread.join()
        from .resources import profile
        peak = Path('/sys/fs/cgroup/memory.peak')
        value = peak.read_text().strip() if peak.is_file() else ''
        record = {'schema_version': 1, 'stage': stage, 'architecture': architecture, 'status': status,
                  'wall_seconds': round(time.monotonic()-started, 3), 'resource_limits': profile(),
                  'container_lifetime_memory_peak_bytes': int(value) if value.isdecimal() else None,
                  'memory_scope': 'cgroup v2 memory.peak for this container lifetime; not host peak',
                  'storage_scope': '2 second whole-filesystem samples; sampled lower bound, not true stage peak',
                  'storage_samples': samples, 'cache': 'NOT_RECORDED', 'transfer': 'NOT_RECORDED'}
        record.update(details or {})
        Path(destination).write_text(json.dumps(record, sort_keys=True) + '\n')
