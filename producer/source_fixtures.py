"""Exact public test vectors in corresponding source, never runtime credentials.

VPP files were compared with the fixed public Git revision; the two core test
drivers were compared with the authenticated Stream archive. Only full original
file bytes qualify. Telegraf fixtures were compared with both the signed Stream
input and public v1.28.3 commit a72550a02094baf939b1881a225af9763ff2cc3d.
Build signing keys and binary payloads have no exceptions.
"""
KNOWN={
    'vpp:8a3f587695fe6ad4d8297bd4b2f331b7262698d9':{
        'source/src/plugins/ikev2/test/certs/client-key.pem':'88a5250e794f474d3212ae78522cc4a2a31416e85acf9b5b261aff79a105591a',
        'source/src/plugins/ikev2/test/certs/server-key.pem':'0c47c638941f22aa3ba8141041839f9254e6231ae592ad1eb2a4f6438d6283b3',
        'source/test-c/hs-test/resources/cert/localhost.key':'612c09724ed1b9a61d7988a33e3182bdb537f92458ddfc93840bee7a53d51037',
        'source/test-c/kube-test/resources/cert/localhost.key':'612c09724ed1b9a61d7988a33e3182bdb537f92458ddfc93840bee7a53d51037'},
    'stream:e9f5b20261b166b72c60b591d00b173673fe704c5f6aaf90404db457c2804910':{
        'source/smoketest/bin/vyos-configtest-pki':'9cba36b9c814e089e11f1de73f022db072406e433203fcf94dffc15d15e20ae5',
        'source/smoketest/scripts/cli/test_service_ssh.py':'08a768f7711ea7d0ac53616e20b8d63659e26b8d8311f1483d97e2633a80d62e',
        'source/config/testdata/special_types.key':'c110f9866b91917e10fc8811987262792490de38aa74e597a6f80f85c14dd539',
        'source/plugins/outputs/http/http_test.go':'c88d340b7d6fe05d00e435db144822895448ac12bbabfc6df2c78b30ec10a010',
        'source/testutil/pki/cakey.pem':'21c55053ba86f3a22e9c398dcba083f1f80c7403832758419eb24be3904ad188',
        'source/testutil/pki/client.pem':'378a7256a950211513a5f09efa5f4d77cb42adb3dbf236dce4dad21c5c11f207',
        'source/testutil/pki/clientenc.pem':'f3bf89a5834d395d007d177fe5fd588ce46dcd156c2e2068e9405eebab637d73',
        'source/testutil/pki/clientenckey.pem':'8b80086b0b0b9da610c866453fe4c6a48867038e94192416c5a46e18f60e6fb8',
        'source/testutil/pki/clientenckey.pkcs8.pem':'d0a77b0ee13baf0b1ff959f6d2028c44f33863d7b8ce1a3db3d4da011aeabfa9',
        'source/testutil/pki/clientkey.pem':'a0f39ca3e32abde9e59be5f48e5c3c717f9ad24db507f09f6af19a7a6c9ebc2b',
        'source/testutil/pki/clientkey.pkcs8.pem':'cce29cd606462a7388ab84280fba9f0bcca7d13015ed3ae7b6a720e2d5d0f12d',
        'source/testutil/pki/server.pem':'10e5633eafdb6f5921f351ace016d878d059000a774ceb8aef126062fdcc2721',
        'source/testutil/pki/serverkey.pem':'9654446dc1a053996ea5bacd06374ce68c8348495a8784def7258772ec62fb5e'}}


# Unmodified Salt tests/documentation from the authenticated public Stream
# source, compared before any build or test generated local state.
KNOWN['stream:e9f5b20261b166b72c60b591d00b173673fe704c5f6aaf90404db457c2804910'].update({
    "source/t/lib/ddclient/Test/Fake/HTTPD/dummy-server-key.pem": "936df28686ed1e5b798d4c58aa5225ad06e16eca67dfe8da81c8ec9026f2bb99",
    "source/salt/ext/tornado/test/test.key": "29b02b5cee62cb34894524fb02d5648f94d4e735d99499f1bee1bffc153804d5",
    "source/salt/wheel/key.py": "fc48476e5ad95c98537171cf86b709a60772263a9f935ad1809632381f83f78f",
    "source/tests/integration/files/file/base/git_pillar/ssh/server/files/ssh_host_rsa_key": "c2e53fc7e579516b552365b0663b8ae6033a57d134ba216d6afc8bc6b6f2fd27",
    "source/tests/integration/files/file/base/git_pillar/ssh/user/files/id_rsa_nopass": "1f7d4068e283a8249e20fd30ac8dea3d7d87a4be1cab88d6e07bfcb23746c41a",
    "source/tests/integration/files/file/base/git_pillar/ssh/user/files/id_rsa_withpass": "5051ab9a5205238f4e923ad5b0b124e2810c1638aff4c3d7e31a0851921d0e97",
    "source/tests/integration/states/test_x509.py": "e5d20d4b4c6eecdee3a0cf5d28609171e21419de5cbaab8a787cfe31fafdbeb1",
    "source/tests/pytests/functional/modules/test_x509_v2.py": "00c351bdcd06315efcb28f586da7210808036780a24aa068f93100ff4165443a",
    "source/tests/pytests/functional/states/test_x509_v2.py": "91050a0897de7f532345d0e2becb6235a9ddcc2df0b807ad43d216dafc030250",
    "source/tests/pytests/integration/files/https/key.pem": "ff3d4dfe108af661358ccde88d12708f49029d48d1d6894957f39bfbe4f6ec91",
    "source/tests/pytests/integration/modules/test_x509_v2.py": "23d8523b65979fe8ed2e519fac0850865d17c4e2816fb7291a7e4a477bdf93d5",
    "source/tests/pytests/integration/states/test_x509_v2.py": "66cbfbcf3d72f98cbd403c109650157a8da7706d87dcacaae584e7d131bb6d23",
    "source/tests/pytests/unit/crypt/__init__.py": "728070e036b76c959af9418e1644da88822e059fbb0efe7b58ae0b5b2fb0d5f7",
    "source/tests/pytests/unit/crypt/test_crypt.py": "276166b91c70fa4dced8b1a33b47511cb1ca258bfbc34cbad8b5543c1b99ab58",
    "source/tests/pytests/unit/crypt/test_crypt_cryptography.py": "977cb4a8adde85db2e47e7ce55dba36c2ca353bff639b32c72a9423b1cb6f016",
    "source/tests/pytests/unit/modules/test_tls.py": "76d6388be4c2c12bfec2a534b96b222778caca6acd3845c9f631883e97565168",
    "source/tests/pytests/unit/transport/test_zeromq.py": "3e6ac25e0e65a36fc71bfc5992c64b46c8ade7b1b98d85f31ab6f6e94bf9bb1a",
    "source/tests/pytests/unit/utils/test_x509.py": "8d262a98aeee17003a3262aae224d268d3d7c9bc0775d829fe8a7824347e4113",
    "source/tests/unit/modules/test_x509.py": "5fddf532eb7411562a164570c13ef4d3eb2e90710a21d12996a994b778150daa",
    "source/tests/unit/test_crypt.py": "21fab3badc192359a625b3630a41af9eb203bde4218429f3306e8d0aeae73842"
})


# Native preferred-source self-tests compared with the authenticated Stream
# snapshot before compiler/test outputs are considered. Whole-file matches only.
KNOWN['stream:e9f5b20261b166b72c60b591d00b173673fe704c5f6aaf90404db457c2804910'].update({
    'source/src/libcharon/plugins/load_tester/load_tester_creds.c': '7ff94823cbf26c497aa94ea97d5f3ee16b16d379d460d160fac4b4a5b771ff11',
    'source/libtomcrypt/testprof/test.key': '76ec7faebdc42a4de35ca70024c2d273e9f7856ca61612e89f5f66350ba8cf5f',
    'source/libtomcrypt/tests/test.key': '76ec7faebdc42a4de35ca70024c2d273e9f7856ca61612e89f5f66350ba8cf5f',
    'source/libtomcrypt/tests/test_dsa.key': '8a44ced5b373b6124f56bb33577a98585cce3d65671e5303384c4236f9b4d41c',
    'source/tools/testing/selftests/sgx/sign_key.pem': '1f6566d02247dc09d4dcb542ee2399522ceebf9b435ee00fe2053aa5582541e2',
})


# Original Kea TLS test vectors from the authenticated Stream snapshot.
KNOWN['stream:e9f5b20261b166b72c60b591d00b173673fe704c5f6aaf90404db457c2804910'].update({
    "source/src/lib/asiolink/testutils/ca/kea-ca.key": "ca8426dff69faf056c267fb4734c05d8410b9eca7fb329d3e37cb2c224a108c5",
    "source/src/lib/asiolink/testutils/ca/kea-client.key": "30c4a5abe465afd73235bfecf188da58b7a5fafc02bdc6ac9c2fe6ee2b58c061",
    "source/src/lib/asiolink/testutils/ca/kea-other.key": "f40cfdef15d3ec2f336ba91dc9a281e6d30ac99e1ad3f8b9be26f57a9a7f27b2",
    "source/src/lib/asiolink/testutils/ca/kea-self.key": "f40cfdef15d3ec2f336ba91dc9a281e6d30ac99e1ad3f8b9be26f57a9a7f27b2",
    "source/src/lib/asiolink/testutils/ca/kea-server.key": "468a391bc2b1cd23027bfcf1c4fe4f6e4a72293e5c3361f9de6a7702a3e86adf"
})


# Original Podman TLS and vendored verification self-tests, not runtime keys.
KNOWN['stream:e9f5b20261b166b72c60b591d00b173673fe704c5f6aaf90404db457c2804910'].update({
    "source/test/certs/domain.key": "1340383164554a3fe8fa2d11658467323683c0ab227e02ceddfa9b2b42d18e4e",
    "source/vendor/go.mozilla.org/pkcs7/verify_test_dsa.go": "f7bf74cda7cf86c74febd919a6a169fd2062fa17ea0a38f2c303b5491e137872"
})

# The exact preferred-source ZIP contains two identical, public quic-go TLS
# test vectors. Every retained byte was compared with the public module ZIP
# (0890cee71e7bf68691a3cd9072d99f2054419a1b76fa9621eba1626a91339dc1).
# Both test keys have SHA256
# 1a0066fcd8be2b1397002fe040bb28c221c539ef44d165556d56eb107f78c4c2.
# This full-archive exception binds the reviewed deterministic export, not
# arbitrary ZIP contents or any generated signing/instance credential.
KNOWN['netbird:3c00a97878e6af537d28c3bdd4f42cb4286aa883'] = {
    'netbird/linked/inputs/10087b77b6eebf896417f686fe9f7103c345bb45e239c35ce2037e8e4332e515.preferred-source.zip':
        '629bb00d19fc6041a0d0a26a2606ef67723a2058c8d8163ae26dc25dd29c7b15'
}


def allowed(origin,member,digest):
    return origin in KNOWN and KNOWN[origin].get(member)==digest


def allowances(inventory):
    """Bind the independent archive scan to exact inventoried source members."""
    result=set()
    def visit(component):
        provenance=component.get('source_origin',{})
        origins=['stream:'+provenance.get('stream_archive_sha256','')]
        origins.append('netbird:'+provenance.get('netbird_source_commit',''))
        git=provenance.get('external_git',{}).get('vpp',{})
        origins.append('vpp:'+git.get('commit',''))
        for ref in component.get('sources',[]):
            if any(allowed(origin,ref['member'],ref['sha256']) for origin in origins):
                result.add((ref['asset'],ref['member'],ref['sha256']))
        for child in component.get('bundled_components',[]):visit(child)
    for component in inventory.get('components',[]):visit(component)
    return result
