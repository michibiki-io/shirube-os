"""Small native hosted-runner capability probe, independent of OS acceptance."""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
import uuid

from .architecture import native
from .compiler_artifact import archive_identity
from .sources import sha256


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
    privileged_container = None
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
        # Reuse the successful fixture by immutable ID; never build a compiler.
        records = json.loads(subprocess.check_output(['docker', 'image', 'inspect', tag],
                                                     text=True, timeout=30))
        if (len(records) != 1 or records[0].get('Architecture') != architecture
                or records[0].get('Os') != 'linux'
                or not re.fullmatch('sha256:[0-9a-f]{64}', records[0].get('Id', ''))):
            raise ValueError('Invalid native fixture image identity/platform')
        image = records[0]['Id']
        server = subprocess.check_output(['docker', 'version', '--format', '{{.Server.Version}}'],
                                         text=True, timeout=30).strip()
        with tempfile.TemporaryDirectory(prefix='shirube-runner-probe-') as temporary:
            archive = Path(temporary) / 'fixture.tar'
            subprocess.run(['docker', 'image', 'save', '--output', str(archive), image],
                           check=True, timeout=120, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            config_identity = {}
            with tarfile.open(archive, 'r:') as source:
                counts = Counter(member.type.decode('ascii', errors='backslashreplace')
                                 for member in source.getmembers())
                try:
                    manifest_member = source.getmember('manifest.json')
                    if not manifest_member.isfile() or manifest_member.size > 1024 * 1024:
                        raise ValueError('Invalid fixture manifest')
                    manifest = json.load(source.extractfile(manifest_member))
                    config_name = manifest[0]['Config']
                    config_member = source.getmember(config_name)
                    if not config_member.isfile() or config_member.size > 1024 * 1024:
                        raise ValueError('Invalid fixture config')
                    config = source.extractfile(config_member).read()
                    config_identity = {'config_member': config_name,
                                       'config_sha256': hashlib.sha256(config).hexdigest()}
                except (KeyError, IndexError, TypeError, ValueError) as error:
                    config_identity = {'config_diagnostic_error': str(error)}
            saved = {'docker_server_version': server, 'image_id': image,
                     'architecture': architecture, 'os': 'linux',
                     'archive_bytes': archive.stat().st_size, 'archive_sha256': sha256(archive),
                     'member_type_counts': dict(sorted(counts.items())), **config_identity}
            # Emit raw-format evidence before the strict production validator:
            # a rejected archive must still leave diagnostics in the CI log.
            print(json.dumps({'fixture_docker_save': saved}, sort_keys=True), flush=True)
            archive_identity(archive, image, architecture)
            saved['status'] = 'PASS_CAPABILITY_ONLY'
        # Keep ownership of both containers until finally, including failed starts.
        privileged_container = container
        container = None
        namespace_script = ('set -eu; before=$(readlink /proc/self/ns/net); '
                            'after=$(unshare --net readlink /proc/self/ns/net); '
                            'test "$before" != "$after"')
        container = subprocess.check_output(['docker', 'create', '--network=none', '--log-driver=none',
            '--cap-add=SYS_ADMIN', '--cpus=1', '--memory=256m', '--memory-swap=256m', '--pids-limit=256',
            '--label', 'io.michibiki.shirube.task=runner-probe-'+nonce,
            '--entrypoint', 'sh', image, '-ec', namespace_script], text=True, timeout=30).strip()
        if not re.fullmatch('[0-9a-f]{64}', container):
            raise ValueError('Invalid task-owned namespace probe container identity')
        subprocess.run(['docker', 'start', '-a', container], check=True, timeout=60,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        state = subprocess.check_output(['docker', 'inspect', '--format',
            '{{.State.Status}} {{.State.ExitCode}}', container], text=True, timeout=30).strip()
        if state != 'exited 0':
            raise ValueError('The SYS_ADMIN network namespace probe did not complete successfully')
    finally:
        for owned in (container, privileged_container):
            if owned and re.fullmatch('[0-9a-f]{64}', owned):
                subprocess.run(['docker', 'rm', '-f', owned], check=True, timeout=30,
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
            'fixture_docker_save': saved,
            'sys_admin_network_namespace': 'PASS_CAPABILITY_ONLY',
            'scope': 'Tiny fixed-base fixture only; no compiler, OS image build, disk cleanup or guest boot. '
                     'SYS_ADMIN namespace creation is not NetBird reconstruction evidence. '
                     'Fixture save validation is not compiler export/load evidence. '
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
