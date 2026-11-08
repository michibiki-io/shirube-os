"""Exact public test vectors in corresponding source, never runtime credentials.

VPP files were compared with the fixed public Git revision; the two core test
drivers were compared with the authenticated Stream archive. Only full original
file bytes qualify. Build signing keys and binary payloads have no exceptions.
"""
KNOWN={
    'vpp:8a3f587695fe6ad4d8297bd4b2f331b7262698d9':{
        'source/src/plugins/ikev2/test/certs/client-key.pem':'88a5250e794f474d3212ae78522cc4a2a31416e85acf9b5b261aff79a105591a',
        'source/src/plugins/ikev2/test/certs/server-key.pem':'0c47c638941f22aa3ba8141041839f9254e6231ae592ad1eb2a4f6438d6283b3',
        'source/test-c/hs-test/resources/cert/localhost.key':'612c09724ed1b9a61d7988a33e3182bdb537f92458ddfc93840bee7a53d51037',
        'source/test-c/kube-test/resources/cert/localhost.key':'612c09724ed1b9a61d7988a33e3182bdb537f92458ddfc93840bee7a53d51037'},
    'stream:e9f5b20261b166b72c60b591d00b173673fe704c5f6aaf90404db457c2804910':{
        'source/smoketest/bin/vyos-configtest-pki':'9cba36b9c814e089e11f1de73f022db072406e433203fcf94dffc15d15e20ae5',
        'source/smoketest/scripts/cli/test_service_ssh.py':'08a768f7711ea7d0ac53616e20b8d63659e26b8d8311f1483d97e2633a80d62e'}}


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
