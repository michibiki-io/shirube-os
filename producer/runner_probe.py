"""Small native hosted-runner capability probe, independent of OS acceptance."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import uuid

from .architecture import native


BASES = {
    'amd64': 'debian@sha256:c67e92c8099ba22853ab4cfda7cfb494d253d6012ca82dee98ba8976772af018',
    'arm64': 'debian@sha256:ec8534cda2cb30f29f44f23e135297af7a1496f2990fd8e4e84aa36b081a757a',
}


def probe(architecture, storage):
    # Never operate on a user's workstation or a selected self-hosted runner.
    if (os.environ.get('GITHUB_ACTIONS') != 'true' or
            os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted' or
            os.environ.get('RUNNER_OS') != 'Linux'):
        raise ValueError('This probe is restricted to ephemeral GitHub-hosted Linux runners')
    native(architecture)
    expected = {'amd64': {'x86_64', 'amd64'}, 'arm64': {'aarch64', 'arm64'}}[architecture]
    actual = subprocess.check_output(['docker', 'info', '--format', '{{.Architecture}}'], text=True).strip()
    if actual not in expected:
        raise ValueError('Native Docker daemon architecture differs from the requested runner')
    head = os.environ.get('SHIRUBE_TEST_HEAD', '')
    if not re.fullmatch('[0-9a-f]{40}', head):
        raise ValueError('An exact test head is required')
    checkout = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    if not re.fullmatch('[0-9a-f]{40}', checkout):
        raise ValueError('An exact checkout commit is required')
    usage = shutil.disk_usage(storage)
    available = next(int(line.split()[1])*1024 for line in Path('/proc/meminfo').read_text().splitlines()
                     if line.startswith('MemAvailable:'))
    kvm = {'device_present': Path('/dev/kvm').exists(), 'open_read_write': False}
    if kvm['device_present']:
        try:
            descriptor = os.open('/dev/kvm', os.O_RDWR | os.O_CLOEXEC)
        except OSError:
            pass
        else:
            os.close(descriptor)
            kvm['open_read_write'] = True
    nonce = uuid.uuid4().hex
    tag = 'shirube-runner-probe:'+nonce
    container = None
    built = False
    try:
        # stdin-only context: no repository, credentials or host files enter it.
        recipe = ('FROM '+BASES[architecture]+'\n'
                  'RUN --mount=type=cache,target=/probe-cache '
                  'touch /probe-cache/check && test -f /probe-cache/check && chroot / /bin/true\n')
        subprocess.run(['docker', 'buildx', 'build', '--load', '--network=none',
                        '--platform', 'linux/'+architecture, '--tag', tag, '-'],
                       input=recipe.encode(), check=True, timeout=240,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        built = True
        script = ('set -eu; work=$(mktemp -d); mkdir "$work/mount"; '
                  'trap \'umount "$work/mount" 2>/dev/null || true; rm -rf "$work"\' EXIT; '
                  'mknod "$work/null" c 1 3; test -c "$work/null"; '
                  'mount -t tmpfs -o size=1m tmpfs "$work/mount"; '
                  'touch "$work/mount/check"; test -f "$work/mount/check"; '
                  'chroot / /bin/true; umount "$work/mount"')
        container = subprocess.check_output(['docker', 'create', '--network=none', '--log-driver=none',
            '--privileged', '--cpus=1', '--memory=256m', '--memory-swap=256m', '--pids-limit=256',
            '--label', 'io.michibiki.shirube.task=runner-probe-'+nonce,
            '--entrypoint', 'sh', tag, '-ec', script], text=True, timeout=30).strip()
        if not re.fullmatch('[0-9a-f]{64}', container):
            raise ValueError('Invalid task-owned probe container identity')
        subprocess.run(['docker', 'start', '-a', container], check=True, timeout=60,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        state = subprocess.check_output(['docker', 'inspect', '--format',
            '{{.State.Status}} {{.State.ExitCode}}', container], text=True, timeout=30).strip()
        if state != 'exited 0':
            raise ValueError('The native privileged runtime probe did not complete successfully')
    finally:
        if container and re.fullmatch('[0-9a-f]{64}', container):
            subprocess.run(['docker', 'rm', '-f', container], check=True, timeout=30,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if built:
            subprocess.run(['docker', 'image', 'rm', tag], check=True, timeout=30,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {'kind': 'native-github-hosted-runner-capability-probe',
            'status': 'PASS_CAPABILITY_PROBE_ONLY', 'test_head': head, 'checkout_commit': checkout,
            'architecture': architecture, 'cpu_count': len(os.sched_getaffinity(0)),
            'memory_available_bytes': available,
            'task_storage_total_bytes': usage.total, 'task_storage_free_bytes': usage.free,
            'kvm': kvm, 'native_buildkit_cache_mount_and_chroot': 'PASS',
            'privileged_container_mknod_mount_chroot': 'PASS',
            'scope': 'Tiny fixed-base fixture only; no compiler, OS image build, disk cleanup or guest boot. '
                     'KVM openability and available storage do not prove full build/boot feasibility.',
            'build': 'NOT_RUN', 'local_boot': 'NOT_RUN', 'oci': 'NOT_RUN',
            'release_clearance': 'NOT_CLEARED'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('architecture', choices=BASES)
    parser.add_argument('--storage', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = probe(args.architecture, args.storage)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
