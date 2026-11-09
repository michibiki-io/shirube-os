"""Identify exact published test fixtures without allowing deployment keys."""
import hashlib
from pathlib import PurePosixPath
import re
import types

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

# Exact public Certbot 2.1.0 test data, compared with the authenticated Debian
# source archive. These are fixtures, never permitted deployment identities.
CERTBOT_SOURCE_SHA256='1f4409739f8454956963b4c78fc1c959db2bef85ea6fc6f08951d7275ec2595d'
CERTBOT_PUBLIC_TEST_FILES={'usr/lib/python3/dist-packages/certbot/tests/testdata/rsa256_key.pem': '46f3e0fcf6488d15ebf1de5f8a64dde7fa8f2a7be15c0ba1b445b96e9902e9be',
 'usr/lib/python3/dist-packages/certbot/tests/testdata/ec_prime256v1_key.pem': '7a0eed03e6d113e022cb8e3a8e4f560be4d67ad674846074f0c081cf51e0a123',
 'usr/lib/python3/dist-packages/certbot/tests/testdata/ec_secp521r1_key.pem': '161ba1691d597ad9d8be97d9434750789a9c6896e76321009c5dd83858a329ab',
 'usr/lib/python3/dist-packages/certbot/tests/testdata/nistp256_key.pem': '28abe3d19cac33314d6ccb3cc1e3c1f77432be4af7f3c309a335c733eab5b93d',
 'usr/lib/python3/dist-packages/certbot/tests/testdata/ec_secp384r1_key.pem': 'e713d97aa1bdb35daf8f5e2031ec4b9ec78ce8665c41a010376cf1f2e73d8cb6',
 'usr/lib/python3/dist-packages/certbot/tests/testdata/rsa512_key.pem': '7b54761d622672c438bdb6876b6f08921e255728e76e490a66e24c52fe085f83',
 'usr/lib/python3/dist-packages/certbot/tests/testdata/rsa2048_key.pem': '69f2a589a1e7f78095eeb2a34e415245f6cfa70354faf7c5d73128aa929436fa',
 'usr/lib/python3/dist-packages/certbot/tests/testdata/sample-archive-ec/privkey1.pem': '4800e846fb91e8708c382446da26f7044373904c9d0e86270926219bb32ee7a3',
 'usr/lib/python3/dist-packages/certbot/tests/testdata/sample-archive/privkey1.pem': 'd906bdbbe56441124cf9a6790c9877bf4dbe901779d84199cab94ae01e8f6f80'}

# GnuTLS 3.7.9 lib/crypto-selftests-pk.c: public cryptographic self-test keys.
GNUTLS_SELFTEST_SOURCE_SHA256='c3d79122e072177f55dc30a98f68b949c0f36f6159a363aae9512a22a9280ed0'
GNUTLS_SELFTEST_KEYS=set(['7c4c63ee462e0e700cd9e29c8e0f730b3f1b484c4abdd83f1e69fcd477c061fa',
 '91ea1699ff6b1a34b4a1d500a9c75a808441e47b9ea68da6fb0195e01ce1dc61',
 'a4d138d7ef9748464117b44fb9c0a4b5b85a1599a127d02690abaa96d03c16e6',
 'd039c8119a029ab9f9c83c04d67002d887b6bc6026c4264402ab27cdf24cf138',
 'ef237ea8db4f2ae9ee100e8ced96d29b5dceb0e6a948443e6b8a00b1791f9ec9',
 'fa0b06a72461ec0a963dcfccb8d5b61bd88a6074fc7271573bff68ab86b8c1af'])


# Original OpenVPN sample keys, independently compared with the authenticated
# Debian 2.6.14-0+deb12u2 source (DSC SHA256 below). Never runtime host keys.
OPENVPN_SAMPLE_SOURCE_DSC='040bba014900f3ff5417c9e049a57b5d36a7e969d2f47933a9b1728e804c76a0'
OPENVPN_PUBLIC_TEST_FILES={
    'usr/share/doc/openvpn/examples/sample-keys/client-pass.key':'ce2e700f032c1b3c8147f4080f134337c4658271985392840a91db8201567945',
    'usr/share/doc/openvpn/examples/sample-keys/ca.key':'0bc5ab537cd0ae0c44bc5a2a985ae9905cb5bcbceb0287e0f7615a84122db547',
    'usr/share/doc/openvpn/examples/sample-keys/server.key':'328355304020c9bd57353eff239b7007c6f2f4e1e34fb17e41959739cdd71835',
    'usr/share/doc/openvpn/examples/sample-keys/client.key':'67a74f6a7c6793ced4fae5e95664b328712ac0f9dbe970edcbce9abe1f9e84b9',
    'usr/share/doc/openvpn/examples/sample-keys/client-ec.key':'aadb33c33085a78d641c80467c67d2dbd584b83b6e4b05c51548c0c5fbd6388c',
    'usr/share/doc/openvpn/examples/sample-keys/server-ec.key':'0c55ca76ff6d790e9d6c2ec14125986397a66ee3b7f6d3e018b0e61d0776f69f',
    'usr/share/doc/openvpn/examples/sample-config-files/loopback-client':'0e87bdaa295d5a84ad2228241c16847656b9877441fdaa92aedb16c36e01a8ec',
}


def allowed_file(package,member):
    prefix='opt/saltstack/salt/lib/python3.11/site-packages/'
    member=member.removeprefix('./')
    if package=='python3-certbot':return CERTBOT_PUBLIC_TEST_FILES.get(member)
    if package=='openvpn':return OPENVPN_PUBLIC_TEST_FILES.get(member)
    if package!='salt-common' or not member.startswith(prefix):return None
    return SALT_PUBLIC_TEST_FILES.get(member.removeprefix(prefix))


def pem_hashes(data, pattern):
    """Fingerprint complete PEM blocks without retaining their key bytes."""
    result=set()
    for match in pattern.finditer(data):
        header=data[match.start():match.end()].split(b'-----',2)[1]
        end=b'-----'+header.replace(b'BEGIN ',b'END ',1)+b'-----'
        finish=data.find(end,match.end(),match.start()+8192)
        if finish>=0:
            result.add(hashlib.sha256(data[match.start():finish+len(end)].replace(b'\r\n',b'\n')).hexdigest())
    return result


def cached_fixture_keys(root, member, package):
    """Authorize only public key constants from a hash-bound source fixture.

    This classifies PEM constants, not bytecode correctness or source rebuilds.
    No candidate bytecode is loaded or executed. Unknown extra keys still fail
    the ordinary streaming scan of the complete cache file.
    """
    from .sanitize import path_in_root
    from .release import SECRET
    member=PurePosixPath(member)
    match=re.fullmatch(r'(.+)\.cpython-311\.pyc',member.name)
    if member.parent.name!='__pycache__' or not match:return set()
    original=str(member.parent.parent/(match[1]+'.py'))
    expected=allowed_file(package,original)
    if expected is None:return set()
    source=path_in_root(root,original)
    if not source.is_file() or not 0<source.stat().st_size<=4*1024**2:
        raise ValueError('Published source fixture is unavailable')
    data=source.read_bytes()
    if hashlib.sha256(data).hexdigest()!=expected:
        raise ValueError('Changed source cannot authorize a compiled fixture')
    pending=[compile(data,'<published fixture>','exec',dont_inherit=True,optimize=0)]
    result=set()
    while pending:
        value=pending.pop()
        if isinstance(value,types.CodeType):pending.extend(value.co_consts)
        elif isinstance(value,(tuple,frozenset)):pending.extend(value)
        elif isinstance(value,(str,bytes)):
            result.update(pem_hashes(value.encode('utf-8','surrogatepass') if isinstance(value,str) else value,SECRET))
    return result


def allowed(package,member,architecture):
    member=member.removeprefix('./')
    triplet={'amd64':'x86_64-linux-gnu','arm64':'aarch64-linux-gnu'}[architecture]
    paths={
        'vpp':{'usr/bin/vcl_test_client','usr/bin/vcl_test_server','usr/bin/vpp_echo'},
        'vpp-plugin-core':{f'usr/lib/{triplet}/vpp_plugins/hs_apps_plugin.so',
                           f'usr/lib/{triplet}/vpp_plugins/http_static_plugin.so'},
    }
    if package=='libgnutls30' and member==f'usr/lib/{triplet}/libgnutls.so.30.34.3':
        return GNUTLS_SELFTEST_KEYS.copy()
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
