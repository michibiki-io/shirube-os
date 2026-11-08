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


def allowed(origin,member,digest):
    return origin in KNOWN and KNOWN[origin].get(member)==digest


def allowances(inventory):
    """Bind the independent archive scan to exact inventoried source members."""
    result=set()
    def visit(component):
        provenance=component.get('source_origin',{})
        origins=['stream:'+provenance.get('stream_archive_sha256','')]
        git=provenance.get('external_git',{}).get('vpp',{})
        origins.append('vpp:'+git.get('commit',''))
        for ref in component.get('sources',[]):
            if any(allowed(origin,ref['member'],ref['sha256']) for origin in origins):
                result.add((ref['asset'],ref['member'],ref['sha256']))
        for child in component.get('bundled_components',[]):visit(child)
    for component in inventory.get('components',[]):visit(component)
    return result
