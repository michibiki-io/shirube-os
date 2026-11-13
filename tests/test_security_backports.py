"""Offline backport replay and behavior on exact selected upstream source files.

C harnesses compile extracted production functions with narrow interface stubs;
these are not daemon/kernel builds or integration/exploit tests.
"""
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import types
import unittest
from unittest.mock import patch

from component_fixture import component_project
from producer import component_inputs, library_cache, native_stages

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT/'tests/fixtures/security'
sys.path.insert(0, str(ROOT/'containers/native'))
import security_sources as security


def sha(data):
    return hashlib.sha256(data).hexdigest()


def function(text, name):
    """Extract complete C function, respecting comments and string literals."""
    masked = re.sub(r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'',
                    lambda m: ''.join('\n' if c == '\n' else ' ' for c in m[0]), text, flags=re.S)
    match = re.search(r'^[^\n;{}]*\b'+name+r'\s*\([^;{}]*?\)\s*\n\{', masked, re.M)
    if not match:
        raise AssertionError('Missing function '+name)
    start = match.start(); index = masked.index('{', start); depth = 1
    for end in range(index+1, len(masked)):
        depth += (masked[end] == '{') - (masked[end] == '}')
        if not depth:
            return text[start:end+1]+'\n'
    raise AssertionError('Unbalanced function '+name)


class SecurityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)

    def fixture(self, name, suffix=''):
        target = self.work/(name+suffix); target.mkdir()
        with tarfile.open(FIXTURES/(name+'.tar.gz')) as archive:
            archive.extractall(target, filter='data')
        record = json.loads((FIXTURES/(name+'.json')).read_text())
        self.assertEqual({str(p.relative_to(target)): sha(p.read_bytes()) for p in target.rglob('*') if p.is_file()}, record['files'])
        return target

    def c(self, code, libraries=()):
        source = self.work/'harness.c'; source.write_text(code)
        binary = self.work/'harness'
        result = subprocess.run(['cc', '-std=gnu11', '-O1', '-g', '-fsanitize=undefined',
                                 '-fno-sanitize-recover=all', str(source), '-o', str(binary), *libraries],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)

    def test_immutable_provenance_hashes(self):
        kernel = ROOT/'patches/kernel'
        record = json.loads((kernel/'upstream/provenance.json').read_text())
        for item in record['originals'] + record['production']:
            self.assertEqual(sha((kernel/item['path']).read_bytes()), item['sha256'])
        for component in ['frr', 'update-check', 'accel-ppp']:
            root = security.patch_root(ROOT)/component
            record = json.loads((root/'provenance.json').read_text())
            self.assertEqual(record['files'], {str(p.relative_to(root)): sha(p.read_bytes())
                             for p in root.rglob('*') if p.is_file() and p.name != 'provenance.json'})
        # The FRR adaptation preserves every nonblank upstream addition.
        from collections import Counter
        root = security.patch_root(ROOT)/'frr'
        original = next((root/'upstream').glob('*.patch')).read_text()
        adapted = next(root.glob('0003-*.patch')).read_text()
        def additions(text):
            return Counter(line[1:] for line in text.splitlines()
                           if line.startswith('+') and not line.startswith('+++') and line[1:].strip())
        self.assertEqual(additions(original), additions(adapted))

    def apply_kernel(self, source, receipt):
        script = (ROOT/'containers/native/package.sh').read_text()
        helper = script[script.index('apply_kernel_tool_fixes() {'):script.index('\npackage_kernel()')]
        helper = helper.replace('/kernel-patches/', str(ROOT/'patches/kernel')+'/')
        return subprocess.run(['bash', '-c', 'set -euo pipefail\n'+helper+'\napply_kernel_tool_fixes'],
                              cwd=source, env={**os.environ, 'PATCH_RECEIPT': str(receipt)}, capture_output=True, text=True)

    def kernel(self):
        source = self.fixture('kernel-original')
        # Existing Stream reverse-first preparation, immediately before the
        # repository helper. No config generation or full source audit implied.
        for p in sorted((source/'stream-patches').glob('*.patch')):
            if security.run_patch(source, p, reverse=True, dry=True).returncode:
                self.assertEqual(security.run_patch(source, p).returncode, 0)
        return source

    def test_kernel_exact_sequence_receipts_repeat_and_partial_rejection(self):
        source = self.kernel()
        original = self.work/'upstream'; shutil.copytree(source, original)
        for p in ['nfqueue-prerequisite.patch', '98432.patch', '99999.patch']:
            result = security.run_patch(original, ROOT/'patches/kernel/upstream'/p)
            self.assertEqual(result.returncode, 0, result.stdout)
        receipt = self.work/'receipt'
        result = self.apply_kernel(source, receipt); self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        first = receipt.read_bytes()
        self.assertEqual(self.apply_kernel(source, receipt).returncode, 0)
        self.assertEqual(first, receipt.read_bytes())
        targets = set()
        for p in (ROOT/'patches/kernel/upstream').glob('*.patch'):
            targets.update(re.findall(r'^--- a/(.+)$', p.read_text(), re.M))
        for name in targets:
            self.assertEqual((source/name).read_bytes(), (original/name).read_bytes(), name)
        file = source/'net/ipv4/af_inet.c'
        file.write_text(file.read_text().replace('kfree_rcu(rcu_dereference_protected(inet->inet_opt, 1), rcu);',
                                                'kfree(rcu_dereference_protected(inet->inet_opt, 1));'))
        result = self.apply_kernel(source, receipt)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Ambiguous repository kernel patch state', result.stderr)

    def test_kernel_helper_boundaries_destructors_and_all_callers(self):
        source = self.kernel()
        self.assertEqual(self.apply_kernel(source, self.work/'receipt').returncode, 0)
        code = function((source/'include/net/ip.h').read_text(), 'ip_check_csum_start')
        self.c('''#include <assert.h>
#include <errno.h>
#define unlikely(x) (x)
struct sk_buff { unsigned csum_start, network_header, ihl; };
unsigned ip_hdrlen(const struct sk_buff *s) { return s->ihl * 4; }
'''+code+'''int main(void) {
for (unsigned ihl=5; ihl<=15; ihl++) {
 struct sk_buff s={0,100,ihl}; unsigned boundary=100+ihl*4;
 s.csum_start=boundary-1; assert(ip_check_csum_start(&s)==-EINVAL);
 s.csum_start=boundary; assert(ip_check_csum_start(&s)==0);
 s.csum_start=boundary+1; assert(ip_check_csum_start(&s)==0);
} return 0; }
''')
        for path, name in [('net/ipv4/af_inet.c','inet_sock_destruct'), ('net/ipv4/tcp_ipv4.c','tcp_v4_reqsk_destructor')]:
            destructor = function((source/path).read_text(), name)
            self.assertEqual(destructor.count('kfree_rcu('), 1)
            self.assertNotRegex(destructor, r'\bkfree\(')
        # All upstream affected callers and prerequisite export are compared
        # byte-for-byte with the full sequential upstream application above.
        destructors = function((source/'net/ipv4/af_inet.c').read_text(), 'inet_sock_destruct')
        destructors += function((source/'net/ipv4/tcp_ipv4.c').read_text(), 'tcp_v4_reqsk_destructor')
        self.c('''#include <assert.h>
#define SOCK_STREAM 1
#define TCP_CLOSE 0
#define SOCK_DEAD 1
#define inet_sk(s) (&(s)->inet)
#define inet_rsk(r) (r)
#define rcu_dereference_protected(x,c) (x)
#define kfree_rcu(p,m) (deferred += ((p)!=0))
#define __skb_queue_purge(x) ((void)0)
#define sk_mem_reclaim_final(x) ((void)0)
#define pr_err(...) ((void)0)
#define WARN_ON_ONCE(x) ((void)(x))
#define atomic_read(x) (*(x))
#define refcount_read(x) (*(x))
#define sk_forward_alloc_get(x) 0
#define dst_release(x) ((void)0)
int deferred;
struct inet_sock {void *inet_opt;};
struct sock {struct inet_sock inet; int sk_receive_queue,sk_error_queue,sk_type,sk_state,sk_rmem_alloc,sk_wmem_alloc,sk_wmem_queued; void *sk_dst_cache,*sk_rx_dst; int dead;};
struct request_sock {void *ireq_opt;};
int sock_flag(struct sock *s,int flag){return s->dead;}
'''+destructors+'''int main(void){
struct sock s={.inet={&s},.dead=1}; struct request_sock r={&r};
inet_sock_destruct(&s); assert(deferred==1);
tcp_v4_reqsk_destructor(&r); assert(deferred==2);
s.dead=0; inet_sock_destruct(&s); assert(deferred==2);
r.ireq_opt=0; tcp_v4_reqsk_destructor(&r); assert(deferred==2);return 0;}
''')
        upstream = (ROOT/'patches/kernel/upstream/99999.patch').read_text()
        for path in re.findall(r'^--- a/(.+)$', upstream, re.M):
            for line in upstream.split('diff --git a/'+path+' b/'+path, 1)[1].split('diff --git',1)[0].splitlines():
                if line.startswith('+') and not line.startswith('+++') and 'ip_check_csum_start(' in line:
                    self.assertIn(line[1:].strip(), (source/path).read_text())
        self.assertIn('EXPORT_SYMBOL(skb_crc32c_csum_help)', (source/'net/core/dev.c').read_text())
        self.assertIn('nf_queue_checksum_help', (source/'net/netfilter/nfnetlink_queue.c').read_text())

    def accel(self):
        source = self.fixture('accel-original')
        security.apply(ROOT, 'accel-ppp', source)
        return source

    def test_accel_complete_inventory_replay_equivalence_repeat_partial_and_retention(self):
        root = security.patch_root(ROOT)/'accel-ppp'
        ledger = json.loads((root/'inventory.json').read_text())['commits']
        self.assertEqual(len(ledger), 102)
        self.assertEqual(len({r['commit'] for r in ledger}), 102)
        source = self.fixture('accel-original')
        for row in ledger:
            self.assertIn(row['disposition'], {'include', 'exclude', 'already present', 'not applicable'})
            if row['disposition'] != 'include': continue
            original = root/'upstream'/(row['commit']+'.patch')
            self.assertEqual(sha(original.read_bytes()), row['sha256'])
            payload = original.read_text()
            if row['order'] == 73:
                # The complete original stays retained. Only this overlapping
                # hunk's result is already supplied by the stronger PR299 fix.
                text = (source/'accel-pppd/radius/serv.c').read_text()
                body = function(text, 'acct_on_recv')
                self.assertIn('struct rad_server_t *s = rad_req_free(req);', body)
                self.assertIn('if (!s)', body)
                self.assertNotIn('req->serv', body)
                payload = payload[:payload.index('@@ -394,9')]
            p = self.work/'replay.patch'; p.write_text(payload)
            result = security.run_patch(source, p)
            self.assertEqual(result.returncode, 0, row['commit']+result.stdout.decode())
        production = self.fixture('accel-original', '-production')
        first = security.apply(ROOT, 'accel-ppp', production)
        self.assertEqual(first, security.apply(ROOT, 'accel-ppp', production))
        for name in json.loads((root/'state.json').read_text()):
            self.assertEqual((production/name).read_bytes(), (source/name).read_bytes(), name)
        new = 'tests/accel-pppd/pppoe/test_pppoe_radius_message_authenticator.py'
        self.assertTrue((production/new).is_file())
        preferred = self.fixture('accel-original', '-preferred')
        retained = dict((name, path) for path, name in native_stages.snapshot_files(production, preferred))
        prefix = 'shirube-corresponding-inputs/security-accel-ppp/'
        self.assertIn(prefix+'patches/security.patch', retained)
        self.assertIn(prefix+'patches/upstream/25e2e10373b307805511ae2662cd5604b1c96547.patch', retained)
        self.assertIn(prefix+'recipe.py', retained)
        # New test is reconstructable from the retained complete patch even
        # though preferred original paths intentionally exclude generated files.
        (production/new).unlink()
        with self.assertRaisesRegex(ValueError, 'partial'):
            security.apply(ROOT, 'accel-ppp', production)

    def test_accel_actual_aftr_sstp_hostuniq_and_message_authenticator_boundaries(self):
        source = self.accel()
        packet = (source/'accel-pppd/radius/packet.c').read_text()
        functions = function((source/'accel-pppd/ipv6/dhcpv6_packet.c').read_text(), 'validate_aftr_name')
        functions += function((source/'accel-pppd/ctrl/sstp/sstp.c').read_text(), 'sstp_handler')
        functions += function((source/'accel-pppd/ctrl/pppoe/pppoe.c').read_text(), 'add_tag2')
        functions += function(packet, 'locate_message_authenticator_attr')
        self.c('''#include <assert.h>
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <arpa/inet.h>
#define log_warn(...) ((void)0)
#define log_error(...) ((void)0)
#define log_ppp_error(...) ((void)0)
#define SSTP_VERSION 0x10
#define SSTP_MAX_PACKET_SIZE 4095
#define ETH_HLEN 14
#define Message_Authenticator 80
#define HMAC_MD5_LEN 16
struct sstp_conn_t {int dummy;};
struct sstp_hdr {uint8_t version,reserved; uint16_t length;};
struct buffer_t {void *head; unsigned len;};
int received;
int sstp_recv_packet(struct sstp_conn_t *c, struct sstp_hdr *h){ received++; return 0; }
void buf_pull(struct buffer_t *b, int n){ b->head=(char*)b->head+n; b->len-=n; }
struct pppoe_hdr {uint8_t type,code; uint16_t sid,length;} __attribute__((packed));
struct pppoe_tag {uint16_t tag_type,tag_len; char data[];} __attribute__((packed));
struct rad_packet_t {uint8_t *buf; int len;};
'''+functions+'''int main(void) {
 uint8_t valid[]={3,'f','o','o',0}, bad[]={0xc0,1,0,0}, shortname[]={4,'f','o','o',0};
 assert(validate_aftr_name(valid,5)==1); assert(!validate_aftr_name(bad,4));
 assert(!validate_aftr_name(shortname,5)); assert(!validate_aftr_name(valid,3));
 struct sstp_hdr h={SSTP_VERSION,0,0}; struct sstp_conn_t c={0};
 for(int n=0;n<4;n++){h.length=htons(n); struct buffer_t b={&h,4}; assert(sstp_handler(&c,&b)==-1);}
 h.length=htons(4); struct buffer_t b={&h,4}; assert(!sstp_handler(&c,&b)); assert(received==1 && b.len==0);
 h.length=htons(5); b=(struct buffer_t){&h,4}; assert(!sstp_handler(&c,&b)); assert(b.len==4);
 uint8_t buf[64]={0}, tagdata[8]={0}; struct pppoe_tag *tag=(void*)tagdata;
 struct pppoe_hdr *ph=(void*)(buf+14); tag->tag_len=htons(4);
 assert(add_tag2(buf,27,tag)==-1); assert(ntohs(ph->length)==0);
 assert(add_tag2(buf,28,tag)==0); assert(ntohs(ph->length)==8);
 ph->length=htons(65535); assert(add_tag2(buf,64,tag)==-1);
 uint8_t raw[64]={0}; struct rad_packet_t rp={raw,20};
 assert(!locate_message_authenticator_attr(&rp));
 raw[20]=1; raw[21]=2; raw[22]=80; raw[23]=18; rp.len=40;
 assert(locate_message_authenticator_attr(&rp)==raw+22);
 for(int n=0;n<18;n++){raw[23]=n; assert(!locate_message_authenticator_attr(&rp));}
 raw[23]=18; raw[40]=80; raw[41]=18; rp.len=58; assert(!locate_message_authenticator_attr(&rp));
 rp.len=21; assert(!locate_message_authenticator_attr(&rp));
 return 0; }
''')

    def test_accel_vpp_hook_lifetime_order_without_changing_api(self):
        source = self.accel()
        before = self.fixture('accel-original', '-api')
        for path, name in [('accel-pppd/ppp/ppp.c', 'destablish_ppp'),
                           ('accel-pppd/session_hooks/vyos_vpp/vpppolicer.c', 'vpppolicer_remove_limiter')]:
            old = function((before/path).read_text(), name).split('{',1)[0]
            new = function((source/path).read_text(), name).split('{',1)[0]
            self.assertEqual(old, new)
        body = function((source/'accel-pppd/ppp/ppp.c').read_text(), 'destablish_ppp')
        self.c('''#include <assert.h>
#include <stdio.h>
#include <string.h>
#include <linux/if.h>
#include <linux/sockios.h>
#define HAVE_SESSION_HOOKS 1
#define log_ppp_warn(...) ((void)0)
#define log_ppp_debug(...) ((void)0)
#define pthread_mutex_lock(x) ((void)0)
#define pthread_mutex_unlock(x) ((void)0)
#define pthread_cond_signal(x) ((void)0)
#define list_add_tail(x,y) ((void)0)
struct ap_net {int (*move_link)(struct ap_net*,int); int (*get_ifindex)(char*); int (*sock_ioctl)(int,void*);};
struct hooks {int is_non_dev_ppp;};
struct ap_session {struct ap_net *net; int ifindex,unit_idx; char ifname[16]; struct hooks *hooks;};
struct ppp_t {int unit_fd,is_unit_read_enabled,unit_hnd; struct ap_session ses;};
struct pppunit_cache {int fd,unit_idx,entry;};
int conf_unit_cache,uc_pool,uc_lock,uc_list,uc_size,uc_cond,finished,closed,destroyed;
struct ap_net *net,*def_net;
struct pppunit_cache cached;
void *mempool_alloc(int pool){return &cached;}
void ap_session_finished(struct ap_session *ses){finished++;}
void triton_md_unregister_handler(int *h,int close_fd){assert(!close_fd);}
int close(int fd){assert(finished);closed++;return 0;}
void destroy_ppp_channel(struct ppp_t *ppp,int nondev){assert(finished);if(nondev)close(50);destroyed++;}
'''+body+'''int main(void){
struct ppp_t p={.unit_fd=50}; destablish_ppp(&p);assert(closed==1&&destroyed==1&&p.unit_fd==-1);
finished=closed=destroyed=0;struct hooks hook={1};p.unit_fd=50;p.ses.hooks=&hook;
destablish_ppp(&p);assert(closed==1&&destroyed==1&&p.unit_fd==-1);
return 0;}
''')
        body = function((source/'accel-pppd/session_hooks/vyos_vpp/vpppolicer.c').read_text(), 'vpppolicer_remove_limiter')
        self.c('''#include <assert.h>
#define POLICER_NAME_MAX_LEN 64
struct ap_session {int dummy;};
struct vpphook_private_data_t {int vpppolicer_down,vpppolicer_down_burst,vpppolicer_up,vpppolicer_up_burst,vpp_sw_if_index;};
struct vpphook_private_data_t pd;
int features,deleted;
struct vpphook_private_data_t *vpphook_get_pd(struct ap_session *s){return &pd;}
void vpppolicer_generate_name(char *n,int len,struct ap_session *s,int rate,int burst,int up){}
int vpppolicer_set_output(int on,int index,char *name){return 0;}
int vpppolicer_set_input(int on,int index,char *name){assert(features==4);return 0;}
void vpppolicer_add_del(int on,char *name,int rate,int burst){assert(features==4);deleted++;}
void vpppoe_set_feature(int index,int on,char *name,char *arc){assert(!deleted);assert(!on);features++;}
'''+body+'''int main(void){struct ap_session s={0};pd.vpppolicer_up=10;pd.vpppolicer_up_burst=20;
assert(!vpppolicer_remove_limiter(&s));assert(features==4&&deleted==1);assert(!pd.vpppolicer_up&&!pd.vpppolicer_up_burst);return 0;}
''')

    def test_accel_authentication_real_crypto_and_upstream_vectors(self):
        source = self.accel()
        upstream = source/'tests/accel-pppd/pppoe/test_pppoe_radius_message_authenticator.py'
        tree = ast.parse(upstream.read_text())
        # Run upstream packet-vector helpers without its daemon/PPP fixtures.
        selected = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.Assign))
                    and (not isinstance(n, ast.FunctionDef) or n.name in {
                        'radius_attr', 'radius_attrs', 'find_message_authenticators',
                        'make_radius_response', 'verify_request_message_authenticator'})]
        env = {'hashlib': hashlib, 'hmac': __import__('hmac'), 'struct': __import__('struct')}
        exec(compile(ast.Module(selected, type_ignores=[]), str(upstream), 'exec'), env)
        request = bytes([1,7,0,20])+bytes(range(16))
        vectors = []
        for mode in ['valid', 'bad', 'none', 'short', 'dup']:
            packet = env['make_radius_response'](2, request, b'', b'secret', ma_mode=mode)
            vectors.append((mode, packet))
        auth = (source/'accel-pppd/radius/auth.c').read_text()
        packet = (source/'accel-pppd/radius/packet.c').read_text()
        code = '''#include <assert.h>
#include <stdint.h>
#include <string.h>
#include <stddef.h>
#include <openssl/crypto.h>
#include <openssl/evp.h>
#include <openssl/core_names.h>
#define HMAC_MD5_LEN 16
#define RADIUS_HEADER_LEN 20
#define RADIUS_AUTHENTICATOR_OFFSET 4
#define CODE_ACCESS_ACCEPT 2
#define CODE_ACCESS_REJECT 3
#define CODE_ACCESS_CHALLENGE 11
#define log_ppp_error(...) ((void)0)
struct rad_dict_attr_t {char *name;};
struct rad_attr_t {void *vendor; struct rad_dict_attr_t *attr; int len; void *raw; struct rad_attr_t *next;};
struct rad_packet_t {uint8_t *buf; int len,code; uint8_t *secret; struct rad_attr_t *attrs;};
struct rad_server_t {char *secret;};
struct rad_req_t {struct rad_packet_t *pack; struct rad_server_t *serv; uint8_t RA[16];};
#define list_for_each_entry(pos, head, entry) for(pos=*(head);pos;pos=pos->next)
int conf_ma_require_access_response;
'''
        code += function(packet, 'rad_hmac_md5')
        for name in ['find_message_authenticator', 'verify_response_authenticator', 'verify_message_authenticator']:
            code += function(auth, name)
        code += '''int main(void) {
struct rad_server_t server={"secret"}; struct rad_req_t req={.serv=&server};
for(int i=0;i<16;i++)req.RA[i]=i;
struct rad_dict_attr_t dictionary={"Message-Authenticator"};
'''
        for index, (mode, data) in enumerate(vectors):
            code += '{ uint8_t bytes[]={' + ','.join(map(str,data)) + '};\n'
            code += 'struct rad_packet_t response={.buf=bytes,.len=sizeof(bytes),.code=2};\n'
            if mode != 'none':
                code += 'struct rad_attr_t a={.attr=&dictionary,.len='+('15' if mode == 'short' else '16')+',.raw=bytes+22}; response.attrs=&a;\n'
                if mode == 'dup':
                    code += 'struct rad_attr_t b={.attr=&dictionary,.len=16,.raw=bytes+40};a.next=&b;\n'
            code += 'assert(!verify_response_authenticator(&req,&response));\n'
            if mode in {'valid','none'}:
                code += 'assert(!verify_message_authenticator(&req,&response));\n'
            else:
                code += 'assert(verify_message_authenticator(&req,&response));\n'
            code += 'bytes[4]^=1;assert(verify_response_authenticator(&req,&response));bytes[4]^=1;\n'
            if mode == 'none':
                code += 'conf_ma_require_access_response=1;assert(verify_message_authenticator(&req,&response));conf_ma_require_access_response=0;\n'
            code += '}\n'
        code += 'return 0;}\n'
        self.c(code, ['-lcrypto'])

    def frr(self):
        source = self.fixture('frr-original')
        security.prepare_frr(ROOT, source)
        recipe = (ROOT/'containers/native/library-prepare/frr.sh').read_text()
        result = subprocess.run(['bash', '-c', 'set -euo pipefail\n'+recipe], cwd=source, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        series = source/'debian/patches/series'
        for name in series.read_text().splitlines():
            result = security.run_patch(source, source/'debian/patches'/name)
            self.assertEqual(result.returncode, 0, result.stdout)
        return source

    def test_frr_full_stream_stack_normalization_repeat_and_both_workers(self):
        source = self.frr()
        security.prepare_frr(ROOT, source)
        self.assertEqual((source/'debian/patches/series').read_text().count('shirube-security-'), 3)
        # Execute both existing normalization recipes on fully applied Stream
        # and security hunks, then replay quilt order to the identical result.
        expected = {str(p.relative_to(source)): p.read_bytes() for p in source.rglob('*.c')}
        ordinary = (ROOT/'containers/native/package.sh').read_text()
        start = ordinary.index('        if [ -e .pc ];')
        end = ordinary.index('\n    fi\n    # Quilt patches', start)
        ordinary = ordinary[start:end]
        library = (ROOT/'containers/native/library-prepare/frr.sh').read_text()
        for i, recipe in enumerate([ordinary, library]):
            (source/'.pc').mkdir(exist_ok=True)
            out = self.work/('output'+str(i)); out.mkdir()
            recipe = recipe.replace('/output/', str(out)+'/')
            result = subprocess.run(['bash','-c','set -euo pipefail\n'+recipe], cwd=source, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
            self.assertTrue((out/'archive-quilt-state').is_dir())
            for name in (source/'debian/patches/series').read_text().splitlines():
                result = security.run_patch(source, source/'debian/patches'/name)
                self.assertEqual(result.returncode, 0, result.stdout)
            for name, data in expected.items(): self.assertEqual((source/name).read_bytes(), data)
        # A deliberately mixed full upstream patch must fail normalization.
        p = source/'bgpd/bgp_flowspec_util.c'
        p.write_text(p.read_text().replace('loop >= BGP_PBR_MATCH_VAL_MAX', 'loop > BGP_PBR_MATCH_VAL_MAX', 1))
        result = subprocess.run(['bash','-c','set -euo pipefail\n'+library], cwd=source, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Ambiguous archived FRR patch state', result.stderr)

    def test_frr_actual_flowspec_bounds_and_all_packet_guards(self):
        source = self.frr()
        text = (source/'bgpd/bgp_flowspec_util.c').read_text()
        code = '''#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#define BGP_PBR_MATCH_VAL_MAX 5
#define BGP_FLOWSPEC_STRING_DISPLAY_MAX 256
#define SET_FLAG(x,y) ((x)|=(y))
#define OPERATOR_COMPARE_LESS_THAN 1
#define OPERATOR_COMPARE_GREATER_THAN 2
#define OPERATOR_COMPARE_EQUAL_TO 4
#define OPERATOR_COMPARE_EXACT_MATCH 8
#define OPERATOR_UNARY_AND 1
#define OPERATOR_UNARY_OR 2
enum bgp_flowspec_util_nlri_t {BGP_FLOWSPEC_RETURN_STRING,BGP_FLOWSPEC_CONVERT_TO_NON_OPAQUE,BGP_FLOWSPEC_VALIDATE_ONLY,BGP_FLOWSPEC_RETURN_JSON};
struct bgp_pbr_match_val {int value,compare_operator,unary_operator;};
'''
        for name in ['hex2bin','hexstr2num','bgp_flowspec_op_decode','bgp_flowspec_bitmask_decode']:
            code += function(text, name)
        code += '''int main(void){
uint8_t input[14]={1,1,1,1,1,1,1,1,1,1,1,1,0x81,1};
struct {struct bgp_pbr_match_val vals[5]; uint64_t canary;} out;
int error;
for(int kind=0;kind<2;kind++)for(int count=0;count<=7;count++){
 memset(&out,0,sizeof(out));out.canary=0x12345678;
 if(count)input[2*(count-1)]|=0x80;
 if(kind)bgp_flowspec_op_decode(BGP_FLOWSPEC_CONVERT_TO_NON_OPAQUE,input,count*2,&out,&error);
 else bgp_flowspec_bitmask_decode(BGP_FLOWSPEC_CONVERT_TO_NON_OPAQUE,input,count*2,&out,&error);
 assert(out.canary==0x12345678);assert(error==(count==0?-1:count>5?-2:count));
 if(count)input[2*(count-1)]&=0x7f;
}return 0;}
'''
        self.c(code)
        # Evaluate exact production guard expressions across boundary values;
        # parser side effects beyond these guards are not simulated.
        cases = [('bgpd/bgp_evpn.c', 'process_type2_route', 'goto fail;'),
                 ('bgpd/bgp_evpn.c', 'process_type3_route', 'return -1;'),
                 ('bgpd/bgp_evpn_mh.c', 'bgp_evpn_type4_route_process', 'return -1;')]
        expressions=[]
        for path,name,_ in cases:
            body = function((source/path).read_text(),name)
            expression = re.search(r'if \((.*psize.*ipaddr_len.*)\) \{',body)[1]
            expressions.append(expression)
        nhc = function((source/'bgpd/bgp_attr.c').read_text(),'bgp_attr_nhc')
        self.assertIn('length < tlv_length + BGP_NHC_TLV_MIN_LEN',nhc)
        rfapi = function((source/'bgpd/rfapi/rfapi_rib.c').read_text(),'rfapiRibBi2Ri')
        start = rfapi.index('case BGP_VNC_SUBTLV_TYPE_RFPOPTION:')
        guards = rfapi[start:rfapi.index('hop = XCALLOC',start)]
        code = '#include <assert.h>\n#define BGP_NHC_TLV_MIN_LEN 4\n'
        for i,expression in enumerate(expressions):
            code += f'int guard{i}(int psize,int ipaddr_len){{return {expression};}}\n'
        code += 'int nhc(int length,int tlv_length){return length < tlv_length + BGP_NHC_TLV_MIN_LEN;}\n'
        code += 'struct encap {int length; unsigned char value[2];};\nint rf(int len,int val){struct encap e={len,{0,val}},*pEncap=&e;switch(1){case 1:\n'
        code += guards.replace('case BGP_VNC_SUBTLV_TYPE_RFPOPTION:', '')+'return 0;}return 1;}\n'
        code += '''int main(void){for(int ip=0;ip<=128;ip+=32)for(int len=0;len<60;len++){
 assert(guard0(len,ip)==!(len==33+ip/8||len==36+ip/8));
 assert(guard1(len,ip)==(len!=13+ip/8)); assert(guard2(len,ip)==(len!=19+ip/8));}
 for(int value=0;value<256;value++)for(int len=0;len<260;len++)assert(nhc(len,value)==(len<value+4));
 assert(rf(2,1));assert(rf(3,0));assert(!rf(3,1));return 0;}
'''
        self.c(code)

    def test_update_remote_text_uses_real_wrapper_without_shell_and_defaults_disabled(self):
        source = self.fixture('update-original')
        security.apply(ROOT, 'update-check', source)
        security.apply(ROOT, 'update-check', source)
        wrapper = source/'python/vyos/utils/process.py'
        spec = importlib.util.spec_from_file_location('real_process',wrapper)
        process = importlib.util.module_from_spec(spec); spec.loader.exec_module(process)
        debug = types.SimpleNamespace(enabled=lambda *a:False,message=lambda *a,**kw:None)
        vyos = types.ModuleType('vyos');vyos.debug=debug;vyos.airbag=types.SimpleNamespace(noteworthy=lambda *a:None)
        version = types.ModuleType('vyos.version');vyos.version=version
        version.get_full_version_data=lambda:{'version':'old'}
        jmes = types.ModuleType('jmespath');jmes.search=lambda key,data:data[0][key[4:]]
        modules={'vyos':vyos,'vyos.version':version,'vyos.utils':types.ModuleType('vyos.utils'),
                 'vyos.utils.process':process,'jmespath':jmes}
        class Done(Exception): pass
        class Child:
            returncode=0
            def communicate(self,*a,**kw):return b'',b''
        script = source/'src/system/vyos-system-update-check.py'
        payloads=[('new','https://example.org/image'),
                  ('$(touch INJECTED);`touch INJECTED`"\n','https://example.org/";touch INJECTED # & | $(id)')]
        for remote,url in payloads:
            calls=[]
            version.get_remote_version=lambda *a:[{'version':remote,'url':url}]
            config=self.work/'config.json';config.write_text('{"url":"https://example.org/version"}')
            motd=self.work/'motd';motd.mkdir(exist_ok=True)
            # Retain actual script and process.call/popen; stub only child
            # creation, version transport, system MOTD path and loop wait.
            text=script.read_text().replace("Path('/run/motd.d/10-vyos-update')",repr(str(motd/'update')))
            text=text.replace('motd_file = '+repr(str(motd/'update')), 'motd_file = Path('+repr(str(motd/'update'))+')')
            def spawn(command,**kwargs):calls.append((command,kwargs));return Child()
            with patch.dict(sys.modules,modules),patch.object(process,'Popen',spawn), \
                 patch('sys.argv',[str(script),'--config',str(config)]), \
                 patch('time.sleep',side_effect=Done):
                with self.assertRaises(Done):exec(compile(text,str(script),'exec'),{'__name__':'__main__'})
            self.assertEqual(len(calls),1)
            command,kwargs=calls[0]
            self.assertEqual(command,['wall','-n',f'Update available: {remote} \nUpdate URL: {url}'])
            self.assertIs(kwargs['shell'],False);self.assertIsNone(kwargs['env'])
            self.assertIn('Current version: old', (motd/'update').read_text())
            self.assertFalse((self.work/'INJECTED').exists())
        default=(source/'data/config.boot.default').read_text()
        self.assertNotIn('update-check',default)

    def calling_source(self):
        source = self.fixture('update-original')
        security.apply(ROOT, 'update-check', source)
        return source

    def test_calling_sid_complete_upstream_replay_repeat_partial_and_reconstruction(self):
        from collections import Counter
        source = self.calling_source()
        root = security.patch_root(ROOT)/'update-check'
        original = (root/'upstream/28ea92afb73a6627c2050474187ace25bb5e3e4e.patch').read_text()
        adapted = next(root.glob('0002-*.patch')).read_text()
        additions = lambda text: Counter(line for line in text.splitlines()
                                        if line.startswith('+') and not line.startswith('+++'))
        self.assertEqual(additions(original), additions(adapted))
        # Replay the complete original with only its incompatible old context
        # mapped to the exact pinned restart list; no added line is rewritten.
        replay = original.replace("        is_node_changed(conf, base + ['authentication', 'radius']),",
                                  "        is_node_changed(conf, base + ['authentication', 'radius', 'dynamic-author']),")
        replay = replay.replace("         is_node_changed(conf, base + ['authentication', 'protocols']),\n", '')
        replay = replay.replace('@@ -127,6 +138,11 @@', '@@ -125,5 +136,10 @@')
        self.assertEqual(replay, adapted)
        other = self.fixture('update-original', '-upstream')
        patchfile = self.work/'replay.patch'; patchfile.write_text(replay)
        result = security.run_patch(other, patchfile)
        self.assertEqual(result.returncode, 0, result.stdout)
        targets = re.findall(r'^--- a/(.+)$', original, re.M)
        for name in targets:
            self.assertEqual((source/name).read_bytes(), (other/name).read_bytes())
        first = security.apply(ROOT, 'update-check', source)
        self.assertEqual(first, security.apply(ROOT, 'update-check', source))
        preferred = self.fixture('update-original', '-preferred')
        retained = dict((name, path) for path, name in native_stages.snapshot_files(source, preferred))
        prefix = 'shirube-corresponding-inputs/security-update-check/'
        for name in ['recipe.py', 'receipt.json', 'patches/'+next(root.glob('0002-*.patch')).name,
                     'patches/upstream/28ea92afb73a6627c2050474187ace25bb5e3e4e.patch']:
            self.assertIn(prefix+name, retained)
        # Reconstruct using only original fixture + collected preparation inputs.
        project = self.work/'reconstruction'; destination = project/'vyos-patches/security/update-check'
        shutil.copytree(source/prefix/'patches', destination)
        recipe = source/prefix/'recipe.py'
        result = subprocess.run([sys.executable, str(recipe), 'update-check', str(preferred),
                                 '--project', str(project)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertEqual(first, json.loads((preferred/prefix/'receipt.json').read_text()))
        for name in targets:
            self.assertEqual((source/name).read_bytes(), (preferred/name).read_bytes())
        # One missing rendering hunk inside an otherwise applied patch is rejected.
        template = source/'data/templates/accel-ppp/ppp-options.j2'
        template.write_bytes((other/template.relative_to(source)).read_bytes().replace(
            b'ipv6-peer-intf-id-secret={{ ppp_options.ipv6_peer_interface_id_secret }}\n', b''))
        with self.assertRaisesRegex(ValueError, 'partial'):
            security.apply(ROOT, 'update-check', source)

    def calling_env(self, source, name):
        """Execute full production functions; stub VyOS system/service dependencies."""
        tree = ast.parse((source/'src/conf_mode'/name).read_text())
        def search(key, data):
            for part in key.split('.'):
                if not isinstance(data, dict): return None
                data = data.get(part)
            return data
        class ConfigError(Exception): pass
        env = {'dict_search': search, 'ConfigError': ConfigError,
               'check_port_availability': lambda *a: True,
               'is_listen_port_bind_service': lambda *a: False}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id.startswith('verify_'):
                env[node.func.id] = lambda *a, **kw: None
        selected = [n for n in tree.body if
                    isinstance(n, ast.FunctionDef) and n.name in {
                        '_is_safe_ascii_secret', '_is_valid_iid_secret', 'verify', 'get_config', 'base_ifname'}
                    or isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in {
                        'iid_secret_min_len', 'iid_secret_max_len', 'pppoe_chap_secrets'} for t in n.targets)]
        exec(compile(ast.Module(selected, type_ignores=[]), name, 'exec'), env)
        return env

    def test_calling_sid_shared_cli_and_four_full_verify_paths(self):
        import xml.etree.ElementTree as ET
        source = self.calling_source()
        xml = source/'interface-definitions/include/accel-ppp/ppp-options-ipv6-interface-id.xml.i'
        tree = ET.fromstring('<root>'+xml.read_text()+'</root>')
        nodes = tree.findall("leafNode[@name='ipv6-peer-interface-id-secret']")
        self.assertEqual(len(nodes), 1)
        regex = nodes[0].findtext('properties/constraint/regex')
        self.assertEqual(regex, '[!-~]{16,128}')
        values = [(None, False), ('', False), ('a'*15, False), ('a'*16, True),
                  ('~'*128, True), ('a'*129, False)]
        values += [('a'*16+c, False) for c in [' ', '\t', '\n', '\r', '\x00', '\x1f', '\x7f', '\u00a0', '\u00e9']]
        values += [(''.join(chr(i) for i in range(33,127)), True)]
        for name in ['service_pppoe-server.py', 'vpn_l2tp.py', 'vpn_pptp.py', 'vpn_sstp.py']:
            env = self.calling_env(source, name)
            for value, valid in values:
                with self.subTest(path=name, length=None if value is None else len(value), valid=valid):
                    config = {'interface': {}, 'port': '443',
                              'ssl': {'certificate': 'cert', 'ca_certificate': 'ca'},
                              'ppp_options': {'ipv6_peer_interface_id': 'calling-sid'}}
                    if value is not None: config['ppp_options']['ipv6_peer_interface_id_secret'] = value
                    self.assertEqual(bool(re.fullmatch(regex, value or '')), valid)
                    if valid:
                        env['verify'](config)
                    else:
                        with self.assertRaises(env['ConfigError']) as caught: env['verify'](config)
                        # Errors name the option/constraint, never the supplied secret.
                        self.assertIn('ipv6-peer-interface-id', str(caught.exception))
                        if value and len(value) >= 15: self.assertNotIn(value, str(caught.exception))
            for mode in [None, 'random', 'ipv4-addr', 'fixed', '::1']:
                config = {'interface': {}, 'port': '443', 'ssl': {'certificate': 'c', 'ca_certificate': 'c'},
                          'ppp_options': {}}
                if mode: config['ppp_options']['ipv6_peer_interface_id'] = mode
                env['verify'](config)  # No secret required in other/default modes.
            env['verify']({'remove': True} if name.startswith('service') else None)

    def test_calling_sid_actual_jinja_option_and_other_modes_unchanged(self):
        # Local Jinja only; no package install or full VyOS renderer/daemon.
        from jinja2 import Environment, ChainableUndefined, Undefined
        source = self.calling_source()
        env = Environment(undefined=ChainableUndefined, trim_blocks=True, lstrip_blocks=True)
        env.tests['vyos_defined'] = lambda value: not isinstance(value, Undefined) and value is not None
        text = (source/'data/templates/accel-ppp/ppp-options.j2').read_text()
        old = self.fixture('update-original', '-render')
        original = env.from_string((old/'data/templates/accel-ppp/ppp-options.j2').read_text())
        template = env.from_string(text)
        for mode in [None, 'random', 'ipv4-addr', 'fixed', '::1', 'calling-sid']:
            options = {'ipv6': 'allow'}
            if mode: options['ipv6_peer_interface_id'] = mode
            config = {'ppp_options': options, 'authentication': {'radius': {}}}
            self.assertEqual(template.render(**config), original.render(**config))
            if mode == 'calling-sid':
                # Synthetic fixture only, never an operator credential.
                options['ipv6_peer_interface_id_secret'] = '!SyntheticTest~16'
                output = template.render(**config).splitlines()
                self.assertEqual(output.count('ipv6-peer-intf-id-secret=!SyntheticTest~16'), 1)
                self.assertIn('ipv6-peer-intf-id=calling-sid', output)
        config = {'ppp_options': {}, 'authentication': {'radius': {}}}
        self.assertEqual(template.render(**config), original.render(**config))
        self.assertNotIn('ipv6-peer-intf-id-secret=', template.render(**config))

    def test_calling_sid_full_pppoe_get_config_restart_conditions(self):
        source = self.calling_source()
        env = self.calling_env(source, 'service_pppoe-server.py')
        class Conf:
            def exists(self, path): return path == ['service', 'pppoe-server']
        env.update(get_accel_dict=lambda *a: {'interface': {}}, node_changed=lambda *a, **kw: [],
                   Diff=types.SimpleNamespace(DELETE=1, ADD=2))
        base = ['service', 'pppoe-server']
        # Exercise full get_config, including the existing restart list.
        for changed, restart in [(None, False), (['ppp-options', 'ipv6-peer-interface-id'], True),
                                 (['ppp-options', 'ipv6-peer-interface-id-secret'], True),
                                 (['ppp-options', 'mru'], False),
                                 (['authentication', 'mode'], True), (['interface'], True)]:
            env['is_node_changed'] = lambda conf, path: changed is not None and path == base+changed
            result = env['get_config'](Conf())
            self.assertEqual('restart_required' in result, restart)
        # Existing apply consumes restart_required to restart sessions.
        tree = ast.parse((source/'src/conf_mode/service_pppoe-server.py').read_text())
        apply = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'apply')
        restart = next(n for n in ast.walk(apply) if isinstance(n, ast.If)
                       and isinstance(n.test, ast.Compare) and ast.unparse(n.test) == "'restart_required' in pppoe")
        calls = []; env.update(call=calls.append, systemd_service='accel-ppp-test.service')
        for required in [False, True]:
            env['pppoe'] = {'restart_required': {}} if required else {}
            exec(compile(ast.Module([restart], type_ignores=[]), '<production restart>', 'exec'), env)
            self.assertEqual(calls[-1], 'systemctl '+('restart' if required else 'reload-or-restart')+' accel-ppp-test.service')

    def test_calling_sid_each_input_bound_to_native_cache_only(self):
        project = component_project(self.work/'project')
        stages = (*component_inputs.CACHEABLE, 'kernel')
        root = security.patch_root(project)/'update-check'
        paths = [next(root.glob('0002-*.patch')), *root.joinpath('upstream').glob('*.patch'), root/'provenance.json']
        for arch in ['amd64', 'arm64']:
            before = {s: component_inputs.manifest(project, s, arch) for s in stages}
            compiler = library_cache.toolchain_identity(project, arch)
            for path in paths:
                saved = path.read_bytes(); path.write_bytes(saved+b'\n')
                after = {s: component_inputs.manifest(project, s, arch) for s in stages}
                self.assertEqual({s for s in stages if before[s] != after[s]}, {'native'})
                self.assertEqual(compiler, library_cache.toolchain_identity(project, arch))
                path.write_bytes(saved)

    def test_both_arch_component_mutation_and_library_context_binding(self):
        project=component_project(self.work/'project')
        fingerprint='sha256:'+'a'*64
        stages=(*component_inputs.CACHEABLE,'kernel')
        def scopes(arch):return {s: component_inputs.manifest(project,s,arch) for s in stages}
        for arch in ['amd64','arm64']:
            before=scopes(arch);compiler=library_cache.toolchain_identity(project,arch)
            kp=project/'patches/kernel/0003-ipv4-checksum-with-nfqueue.patch'
            saved_kernel=kp.read_bytes();kp.write_bytes(saved_kernel+b'\n# mutation\n')
            self.assertEqual({s for s in stages if before[s]!=scopes(arch)[s]}, {'kernel'})
            kp.write_bytes(saved_kernel)
            for component,scope in [('frr','native'),('update-check','native'),('accel-ppp','kernel')]:
                p=next((security.patch_root(project)/component).glob('*.patch'));saved=p.read_bytes()
                p.write_bytes(saved+b'\n# mutation\n')
                after=scopes(arch)
                self.assertEqual({s for s in stages if before[s]!=after[s]},{scope})
                self.assertEqual(library_cache.toolchain_identity(project,arch),compiler)
                p.write_bytes(saved)
            source=self.fixture('frr-original','-'+arch)
            security.prepare_frr(project,source)
            job={'name':'frr','recipe':'frr','source':'frr/frr'}
            unrelated=self.work/('libfixture-'+arch);unrelated.mkdir();(unrelated/'library.c').write_text('int x;\n')
            unrelated_job={'name':'libfixture','source':'libfixture/source'}
            unrelated_id,_=library_cache.identity(project,unrelated_job,unrelated,[],arch,fingerprint)
            before_id,_=library_cache.identity(project,job,source,[],arch,fingerprint)
            context=self.work/('context-'+arch)
            library_cache.context(project,job,source,[],arch,fingerprint,context)
            self.assertEqual(json.loads((context/'context.json').read_text()),before_id)
            p=next((security.patch_root(project)/'frr').glob('*.patch'));saved=p.read_bytes()
            p.write_bytes(saved+b'\n# mutation\n')
            # A new authenticated extraction is prepared before lookup/context.
            changed=self.fixture('frr-original','-mutated-'+arch)
            security.prepare_frr(project,changed)
            after_id,_=library_cache.identity(project,job,changed,[],arch,fingerprint)
            self.assertNotEqual(before_id['input_id'],after_id['input_id'])
            cache=self.work/('cache-'+arch);cache.mkdir()
            (cache/'index.json').write_text(json.dumps({'schema_version':1,'architecture':arch,'jobs':{'frr':{'input_id':before_id['input_id']}}}))
            self.assertIsNone(library_cache.restore(project,job,changed,[],arch,fingerprint,cache,self.work/('unused-output-'+arch)))
            self.assertEqual(unrelated_id,library_cache.identity(project,unrelated_job,unrelated,[],arch,fingerprint)[0])
            with self.assertRaises(ValueError):security.prepare_frr(project,source)
            p.write_bytes(saved)
        # Both native and library entry points prepare before restore/identity.
        for name,prep,lookup in [('containers/native/build.py','prepare_frr(', 'cached=restore('),
                                 ('scripts/build-library-cache.py','prepare_frr(', 'expected,_=identity(')]:
            text=(ROOT/name).read_text();self.assertLess(text.index(prep),text.index(lookup))


if __name__ == '__main__':
    unittest.main()
