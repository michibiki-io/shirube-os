"""Task-owned, local-only real NetBird management fixture for runtime tests.

No host ports, host credentials or Docker socket are exposed to containers.
Ephemeral setup/admin credentials travel only through private stdin/stdout pipes.
"""
import json
import os
import base64
from pathlib import Path
import secrets
import subprocess
import time

SERVER_IMAGES = {
    'amd64': 'netbirdio/netbird-server@sha256:a3f620738a83bc98a1cd4508178f82d51f18bb6ad85311767546bac0b6a9c808',
    'arm64': 'netbirdio/netbird-server@sha256:bfe87ac71c7a4d60afa05056c5c9849f353a6cc6d5fd63a6482ce3dce586000b',
}
LABEL = 'io.michibiki.shirube.fixture'


class Fixture:
    def __init__(self, architecture, helper_image, work):
        self.image = SERVER_IMAGES[architecture]
        self.helper = helper_image
        self.work = Path(work).resolve()
        self.work.mkdir(mode=0o700, parents=True, exist_ok=False)
        self.nonce = secrets.token_hex(12)
        self.network = 'shirube-netbird-' + self.nonce
        self.hostname = 'netbird-' + self.nonce + '.test'
        self.url = 'https://' + self.hostname
        self.container = None
        self.network_created = False
        self.token = None

    def docker(self, *args, input=None, timeout=120):
        result = subprocess.run(['docker', *args], input=input, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=timeout)
        if result.returncode:
            # Docker/server diagnostics may contain credentials or state. Keep
            # errors generic; never surface captured private response bytes.
            raise RuntimeError('Local NetBird fixture Docker operation failed')
        return result.stdout

    def request(self, path, body=None, authenticated=False):
        program = '''import json, ssl, sys, urllib.request
payload=json.load(sys.stdin)
headers={'Content-Type':'application/json'}
if payload.get('token'):headers['Authorization']='Token '+payload['token']
body=payload.get('body')
request=urllib.request.Request(payload['url'], data=json.dumps(body).encode() if body is not None else None, headers=headers)
try:
 with urllib.request.urlopen(request,context=ssl.create_default_context(cafile='/fixture/cert.pem'),timeout=15) as response:
  sys.stdout.buffer.write(response.read())
except Exception:
 sys.exit(1)
'''
        result = self.docker('run', '--rm', '-i', '--network', self.network,
            '--log-driver', 'none', '--cpus=1', '--memory=128m', '--pids-limit=64',
            '-v', str(self.work/'cert.pem')+':/fixture/cert.pem:ro',
            '--entrypoint', 'python3', self.helper, '-c', program,
            input=json.dumps({'url': self.url+path, 'body': body,
                              'token': self.token if authenticated else None}).encode())
        return json.loads(result)

    def __enter__(self):
        try:
            self.docker('run', '--rm', '--network', 'none', '--log-driver', 'none',
                '--user', str(os.getuid())+':'+str(os.getgid()),
                '--cpus=1', '--memory=128m', '--pids-limit=64',
                '-v', str(self.work)+':/fixture', '--entrypoint', 'openssl', self.helper,
                'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '2',
                '-subj', '/CN='+self.hostname, '-addext', 'subjectAltName=DNS:'+self.hostname,
                '-keyout', '/fixture/key.pem', '-out', '/fixture/cert.pem')
            (self.work/'key.pem').chmod(0o600)
            (self.work/'data').mkdir(mode=0o700)
            config = {'server': {'listenAddress': ':443', 'exposedAddress': self.url,
                'logLevel': 'error', 'logFile': 'console', 'dataDir': '/fixture/data',
                'disableAnonymousMetrics': True, 'disableGeoliteUpdate': True,
                'authSecret': secrets.token_urlsafe(48),
                'tls': {'certFile': '/fixture/cert.pem', 'keyFile': '/fixture/key.pem'},
                'auth': {'issuer': self.url+'/oauth2'},
                'store': {'engine': 'sqlite', 'encryptionKey': base64.b64encode(secrets.token_bytes(32)).decode()}}}
            config_file = self.work/'config.json'
            config_file.write_text(json.dumps(config)); config_file.chmod(0o600)
            self.docker('network', 'create', '--internal', '--label', LABEL+'='+self.nonce, self.network)
            self.network_created = True
            self.container = self.docker('run', '-d', '--network', self.network,
                '--user', str(os.getuid())+':'+str(os.getgid()),
                '--network-alias', self.hostname, '--label', LABEL+'='+self.nonce,
                '-e', 'NB_DISABLE_GEOLOCATION=true',
                '-e', 'NB_PROXY_DISABLE_GEOLOCATION=true',
                '-e', 'NB_SETUP_PAT_ENABLED=true',
                '-e', 'SSL_CERT_FILE=/fixture/cert.pem',
                '--log-driver', 'none', '--cpus=1', '--memory=512m', '--pids-limit=128',
                '-v', str(self.work)+':/fixture', self.image, '--config', '/fixture/config.json').decode().strip()
            deadline = time.monotonic()+120
            while True:
                state = self.docker('inspect', '--format', '{{.State.Running}}', self.container).decode().strip()
                if state != 'true':
                    raise RuntimeError('Local NetBird fixture server exited before readiness')
                try:
                    status = self.request('/api/instance')
                    if status.get('setup_required') is True:
                        break
                except RuntimeError:
                    pass
                if time.monotonic() >= deadline:
                    raise RuntimeError('Local NetBird fixture did not become ready')
                time.sleep(2)
            setup = self.request('/api/setup', {'email': 'fixture@example.test',
                'name': 'Ephemeral test administrator', 'password': secrets.token_urlsafe(32)+'Aa1!',
                'create_pat': True, 'pat_expire_in': 1})
            self.token = setup.get('personal_access_token')
            if not self.token:
                raise RuntimeError('Local NetBird fixture did not create its temporary API token')
            return self
        except BaseException:
            self.close()
            raise

    def setup_key(self):
        result = self.request('/api/setup-keys', {'name': 'Ephemeral one-use acceptance key',
            'type': 'one-off', 'expires_in': 3600, 'auto_groups': [], 'usage_limit': 1,
            'ephemeral': False}, authenticated=True)
        if not result.get('key'):
            raise RuntimeError('Local NetBird fixture did not create a one-use key')
        return result['key']

    def close(self):
        self.token = None
        if self.container:
            label = self.docker('inspect', '--format', '{{index .Config.Labels "'+LABEL+'"}}', self.container).decode().strip()
            if label != self.nonce:
                raise RuntimeError('Refusing to remove an unowned fixture container')
            self.docker('rm', '-f', self.container)
            self.container = None
        if self.network_created:
            label = self.docker('network', 'inspect', '--format', '{{index .Labels "'+LABEL+'"}}', self.network).decode().strip()
            if label != self.nonce:
                raise RuntimeError('Refusing to remove an unowned fixture network')
            self.docker('network', 'rm', self.network)
            self.network_created = False
        # Private state is deliberately not a source/release artifact.
        import shutil
        shutil.rmtree(self.work)

    def __exit__(self, *exception):
        self.close()
