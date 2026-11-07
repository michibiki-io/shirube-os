"""Ephemeral build-internal keys and matching Linux module signature checks."""
from contextlib import contextmanager
from pathlib import Path
import os
import subprocess
import tempfile

MAGIC = b'~Module signature appended~\n'


def run(*args, **kwargs):
    return subprocess.run(args, check=True, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, **kwargs)


@contextmanager
def build_keys(private_storage):
    """Use task-private storage, never a Docker layer or shared cache mount."""
    parent = Path(private_storage)
    if parent.is_symlink() or not parent.is_dir() or parent.stat().st_mode & 0o077:
        raise ValueError('Signing storage must be an existing private task directory (0700)')
    with tempfile.TemporaryDirectory(prefix='signing-', dir=parent) as directory:
        directory = Path(directory)
        key = directory / 'kernel.pem'
        cert = directory / 'kernel.der'
        # OpenSSL writes to a 0700 directory. Explicit 0600 also protects later copies.
        run('openssl', 'req', '-new', '-x509', '-newkey', 'rsa:3072', '-nodes',
            '-keyout', str(key), '-outform', 'DER', '-out', str(cert), '-days', '3650',
            '-subj', '/CN=Shirube OS ephemeral build module key/')
        key.chmod(0o600)
        home = directory / 'gnupg'
        home.mkdir(mode=0o700)
        env = dict(os.environ, GNUPGHOME=str(home))
        try:
            run('gpg', '--batch', '--pinentry-mode', 'loopback', '--passphrase', '',
                '--quick-generate-key', 'Shirube OS ephemeral package repository',
                'ed25519', 'sign', '0', env=env)
            public = directory / 'repository.asc'
            with public.open('wb') as output:
                subprocess.run(['gpg', '--batch', '--armor', '--export'], check=True,
                               stdout=output, stderr=subprocess.DEVNULL, env=env)
            listing = subprocess.check_output(['gpg', '--batch', '--with-colons', '--list-keys'],
                                              env=env, stderr=subprocess.DEVNULL, text=True)
            fingerprints = [x.split(':')[9] for x in listing.splitlines() if x.startswith('fpr:')]
            if len(fingerprints) != 1:
                raise ValueError('Exactly one ephemeral repository signing identity required')
            yield {'kernel_key': key, 'kernel_certificate': cert, 'repository_home': home,
                   'repository_public_key': public, 'repository_fingerprint': fingerprints[0]}
        finally:
            run('gpgconf', '--kill', 'all', env=env)


def verify_module(data, certificate):
    if not data.endswith(MAGIC) or len(data) < len(MAGIC) + 12:
        raise ValueError('Missing module signature')
    offset = len(data) - len(MAGIC) - 12
    header = data[offset:offset+12]
    length = int.from_bytes(header[8:12], 'big')
    if (header[2] != 2 or header[3:8] != b'\0'*5 or not 0 < length < offset):
        raise ValueError('Invalid PKCS#7 kernel module signature framing')
    with tempfile.TemporaryDirectory() as temporary:
        temporary = Path(temporary)
        (temporary / 'body').write_bytes(data[:offset-length])
        (temporary / 'signature').write_bytes(data[offset-length:offset])
        run('openssl', 'x509', '-inform', 'DER', '-in', str(certificate),
            '-out', str(temporary / 'certificate.pem'))
        try:
            run('openssl', 'cms', '-verify', '-binary', '-inform', 'DER', '-in',
                str(temporary / 'signature'), '-content', str(temporary / 'body'),
                '-certfile', str(temporary / 'certificate.pem'), '-nointern', '-noverify',
                '-out', '/dev/null')
        except subprocess.CalledProcessError:
            raise ValueError('Module signature does not match the kernel build certificate') from None
