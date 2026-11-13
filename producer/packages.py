"""Inspect actual Debian payloads, including ELF and module signature identity."""
import gzip
import hashlib
import io
import lzma
from pathlib import Path
import subprocess
import tarfile
from .architecture import check_elf
from .security_contract import SECRET
from .signing import verify_module


def audit(package, architecture, certificate=None):
    modules = 0
    package_name=subprocess.check_output(['dpkg-deb','-f',str(package),'Package'],text=True).strip()
    with subprocess.Popen(['dpkg-deb', '--fsys-tarfile', str(package)], stdout=subprocess.PIPE) as process:
        try:
            with tarfile.open(fileobj=process.stdout, mode='r|') as archive:
                for member in archive:
                    if not member.isfile():
                        continue
                    name = member.name.removeprefix('./')
                    with archive.extractfile(member) as source:
                        if name.endswith(('.ko', '.ko.gz', '.ko.xz', '.ko.zst')):
                            data = source.read()
                            if name.endswith('.gz'):
                                data = gzip.decompress(data)
                            elif name.endswith('.xz'):
                                data = lzma.decompress(data)
                            elif name.endswith('.zst'):
                                data = subprocess.check_output(['zstd', '-dc'], input=data)
                            check_elf(data, architecture)
                            if certificate is None:
                                raise ValueError('Cannot verify a module without the matching kernel certificate')
                            verify_module(data, certificate)
                            modules += 1
                        else:
                            first = source.read(1024**2)
                            # Python/runtime environments may install native
                            # extensions below /usr/share. Audit every ELF
                            # payload, with only firmware's declared exception.
                            if first[:4] == b'\x7fELF' and not name.startswith(('lib/firmware/','usr/lib/firmware/')):
                                check_elf(first, architecture)
                            from .public_test_keys import allowed,allowed_file,scan
                            fixture=allowed_file(package_name,name)
                            if fixture:
                                if member.size>4*1024**2:raise ValueError('Unexpected public test fixture size')
                                data=first+source.read()
                                if hashlib.sha256(data).hexdigest()==fixture:continue
                                # A changed example is ordinary untrusted
                                # content, never a path-wide exception.
                                scan(io.BytesIO(data),SECRET)
                                continue
                            # Prefix already read for ELF inspection; keep the
                            # scanner streaming rather than materializing ELF.
                            class Payload:
                                prefix=first
                                def read(self,size):
                                    if self.prefix:
                                        result=self.prefix;self.prefix=b'';return result
                                    return source.read(size)
                            scan(Payload(),SECRET,allowed(package_name,name,architecture))
        finally:
            process.stdout.close()
        if process.wait() != 0:
            raise ValueError('Failed to inspect Debian payload')
    return {'elf': 'PASS', 'signed_modules_verified': modules}
