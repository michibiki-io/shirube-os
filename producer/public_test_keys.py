"""Identify exact published test fixtures without allowing deployment keys."""
import hashlib

# Independently compared with the pinned public FD.io source, not extracted
# from a candidate binary. No private key bytes are stored in this policy.
VPP_TLS_FIXTURE={
    'source_commit':'8a3f587695fe6ad4d8297bd4b2f331b7262698d9',
    'source_path':'src/vnet/tls/tls_test.h',
    'source_sha256':'d8b30ffb02e16b3d530caa4e629de53bb39f96da187bfd1eab54a750e43b318e',
    'pem_sha256':'ccc1b38f71b762cbc276b07134072549643cbc9423142b2ddd347960e2617c78',
}

# These complete Python files were compared byte-for-byte with the published
# pycryptodomex 3.19.1 source archive (SHA256 below). The Salt documentation
# example was compared with the authenticated Stream source, not local state.
SALT_CRYPTO_SOURCE_SHA256='0b7154aff2272962355f8941fd514104a88cb29db2d8f43a29af900d6398eb1c'
SALT_PUBLIC_TEST_FILES={
    'Cryptodome/SelfTest/Cipher/test_pkcs1_15.py':'dc699df4f6e1829bd2bbc73a618cc9f6e911c0b687895aff017fb0b10ab78638',
    'Cryptodome/SelfTest/Protocol/test_ecdh.py':'6305e54259cf60bef2e740b41c8a7b5034a41764b56113a3c16c17aae93f8f87',
    'Cryptodome/SelfTest/PublicKey/test_import_DSA.py':'e9f2e62884d2a0fd7976568a66ad5db77f201e952c8e663a678811d07e417247',
    'Cryptodome/SelfTest/PublicKey/test_import_ECC.py':'c3518afba34480a1cbdb82aecfadd88c0b0adbb6334b48ca3990d9634a58ce0f',
    'Cryptodome/SelfTest/PublicKey/test_import_RSA.py':'5e5c9936dd02cdce24cd28e0082d328caa9978255bec39cf383aaad7ad6f90f2',
    'Cryptodome/SelfTest/Signature/test_pss.py':'536e4123b0b734ece634b4ec241e444a1eda5a13b4ec5e3d5750957daa23951a',
    'salt/wheel/key.py':'fc48476e5ad95c98537171cf86b709a60772263a9f935ad1809632381f83f78f',
}


def allowed_file(package,member):
    prefix='opt/saltstack/salt/lib/python3.11/site-packages/'
    member=member.removeprefix('./')
    if package!='salt-common' or not member.startswith(prefix):return None
    return SALT_PUBLIC_TEST_FILES.get(member.removeprefix(prefix))


def allowed(package,member,architecture):
    member=member.removeprefix('./')
    triplet={'amd64':'x86_64-linux-gnu','arm64':'aarch64-linux-gnu'}[architecture]
    paths={
        'vpp':{'usr/bin/vcl_test_client','usr/bin/vcl_test_server','usr/bin/vpp_echo'},
        'vpp-plugin-core':{f'usr/lib/{triplet}/vpp_plugins/hs_apps_plugin.so',
                           f'usr/lib/{triplet}/vpp_plugins/http_static_plugin.so'},
    }
    return {VPP_TLS_FIXTURE['pem_sha256']} if member in paths.get(package,set()) else set()


def scan(stream,pattern,allowances=()):
    """Retain complete fixture framing across chunks; reject every other key.

    Only CRLF normalization is allowed. A changed/truncated public fixture,
    extra key, or the same fixture in an unauthorized payload remains an error.
    """
    tail=b'';reserve=8192 if allowances else 1024
    def check(data,boundary):
        for match in pattern.finditer(data):
            if match.start()>=boundary:continue
            prefix=data[match.start():match.end()]
            header=prefix.split(b'-----',2)[1]
            end=b'-----'+header.replace(b'BEGIN ',b'END ',1)+b'-----'
            finish=data.find(end,match.end(),match.start()+8192)
            if allowances and finish>=0:
                pem=data[match.start():finish+len(end)].replace(b'\r\n',b'\n')
                if hashlib.sha256(pem).hexdigest() in allowances:continue
            raise ValueError('Private key material in package payload')
    while chunk:=stream.read(1024**2):
        data=tail+chunk;boundary=max(0,len(data)-reserve)
        check(data,boundary);tail=data[boundary:]
    check(tail,len(tail))
