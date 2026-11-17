"""Exact-source replay and narrow C stubs, not a kernel/ARM64 race test."""
from collections import Counter
import copy
import difflib
import hashlib
import json
from pathlib import Path
import re
import tempfile
import unittest

import test_security_backports as existing
from component_fixture import component_project
from producer import component_inputs

ROOT = Path(__file__).resolve().parents[1]
PATCHES = ROOT/'patches/kernel'


def hunks(text):
    """Preserve file, hunk order and repeated signed lines, including blanks."""
    result, counts = [], {}
    lines = text.splitlines(True)
    filename, i = None, 0
    while i < len(lines):
        if lines[i].startswith('--- a/'):
            filename = lines[i][6:].strip()
        match = re.match(r'@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@', lines[i])
        if not match:
            i += 1
            continue
        old, new = int(match[2] or 1), int(match[4] or 1)
        body = []
        i += 1
        while old or new:
            line = lines[i]
            assert line[0] in ' +-'
            body.append(line)
            old -= line[0] in ' -'
            new -= line[0] in ' +'
            i += 1
        counts[filename] = counts.get(filename, 0)+1
        result.append((filename, counts[filename], ''.join(body)))
    return result


def changes(body, *, normalize_annotation=False):
    # The only changed +/- spelling allowed for the added helper is the
    # absent 6.6 annotation. Context-only mappings cannot hide a lost change.
    if normalize_annotation:
        body = body.replace(' __signed_wrap', '')
    return Counter(line for line in body.splitlines(True) if line[0] in '+-')


def oracle(source, record):
    """Apply each independently accounted hunk to its exact 6.6 location."""
    raw = {}
    for name in record['original_order']:
        for filename, number, body in hunks((PATCHES/'upstream'/name).read_text()):
            raw[name, filename, number] = body
    keys = [(e['original'], e['file'], e['hunk']) for e in record['entries']+record['excluded']]
    assert len(keys) == len(set(keys)) and set(keys) == set(raw)
    expected = {name: (source/name).read_text() for name in record['expected_before']}
    assert {n: existing.sha(t.encode()) for n, t in expected.items()} == record['expected_before']
    for entry in record['excluded']:
        assert entry['original'] == '99288-refcount-api.patch'
        assert entry['file'] in {'Documentation/RCU/whatisRCU.rst',
                                 'Documentation/core-api/refcount-vs-atomic.rst', 'include/linux/slab.h'}
        body = raw[entry['original'], entry['file'], entry['hunk']]
        assert existing.sha(body.encode()) == entry['hunk_sha256']
        assert body == entry['body']
    for entry in record['entries']:
        body = raw[entry['original'], entry['file'], entry['hunk']]
        assert existing.sha(body.encode()) == entry['hunk_sha256']
        before, after = entry['before'], entry['after']
        delta = ''.join(difflib.unified_diff(before.splitlines(True), after.splitlines(True)))
        # unified_diff without filenames uses ---/+++ framing, never source lines.
        delta = ''.join(s for s in delta.splitlines(True) if not s.startswith(('---', '+++')))
        assert changes(delta) == changes(body, normalize_annotation=entry['annotation_adaptation']), (entry['file'], entry['hunk'])
        text = expected[entry['file']]
        assert text.count(before) == 1
        expected[entry['file']] = text.replace(before, after, 1)
    assert {n: existing.sha(t.encode()) for n, t in expected.items()} == record['expected_after']
    # Mainline and stable are alternative representations, never sequential inputs.
    mainline = [(f, i, changes(b)) for f, i, b in hunks((PATCHES/'upstream'/record['reference_only']).read_text())]
    stable = [(f, i, changes(b)) for f, i, b in hunks((PATCHES/'upstream/99288-stable.patch').read_text())]
    assert mainline == stable
    return expected


class NoisyTCPTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)

    def fixture(self, name='kernel-original', suffix=''):
        return existing.SecurityTests.fixture(self, name, suffix)

    def prepared(self):
        return existing.SecurityTests.kernel(self)

    def patched(self):
        source = self.prepared()
        result = existing.SecurityTests.apply_kernel(self, source, self.work/'receipt')
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        return source

    def c(self, code, libraries=()):
        return existing.SecurityTests.c(self, code, libraries)

    def test_every_original_hunk_and_full_production_afterimage(self):
        source = self.prepared()
        record = json.loads((PATCHES/'upstream/99288-adaptation.json').read_text())
        expected = oracle(source, record)
        self.assertEqual(len(record['entries']), 21)
        self.assertEqual(len(record['excluded']), 8)
        self.assertEqual({f for f, _, _ in hunks((PATCHES/record['production']).read_text())}, set(expected))
        provenance = json.loads((PATCHES/'upstream/provenance.json').read_text())
        bound = provenance['adaptation_oracle']
        self.assertEqual(existing.sha((PATCHES/bound['path']).read_bytes()), bound['sha256'])
        result = existing.SecurityTests.apply_kernel(self, source, self.work/'receipt')
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        for name, text in expected.items():
            self.assertEqual((source/name).read_text(), text, name)
        # Detect missing duplicate puts/setters and dropped entire hunks, not sets.
        for index, entry in enumerate(record['entries']):
            broken = copy.deepcopy(record)
            broken['entries'].pop(index)
            with self.assertRaises(AssertionError):
                oracle(self.fixture(suffix='-missing-hunk-'+str(index)), broken)

    def test_full_sequence_idempotence_receipt_and_partial_incompatible_rejection(self):
        source = self.patched()
        receipt = (self.work/'receipt').read_bytes()
        self.assertEqual(existing.SecurityTests.apply_kernel(self, source, self.work/'receipt').returncode, 0)
        self.assertEqual((self.work/'receipt').read_bytes(), receipt)
        self.assertEqual(receipt.decode().count('0004-inet-lookup-lifetime-ordering.patch'), 1)
        for filename, old, new in [
            ('net/ipv4/inet_connection_sock.c', 'refcount_set_release(&nreq->rsk_refcnt, 1)', 'refcount_set(&nreq->rsk_refcnt, 1)'),
            ('net/ipv4/inet_hashtables.c', 'hlist_nulls_unhashed_lockless', 'missing_lookup_guard'),
            ('include/linux/refcount.h', 'atomic_set_release(&r->refs, n)', 'atomic_set(&r->refs, n)')]:
            path = source/filename
            saved = path.read_text()
            self.assertIn(old, saved)
            path.write_text(saved.replace(old, new, 1))
            result = existing.SecurityTests.apply_kernel(self, source, self.work/'receipt')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Ambiguous repository kernel patch state', result.stderr)
            path.write_text(saved)

    def test_both_arch_new_patch_and_originals_change_only_kernel_scope(self):
        project = component_project(self.work/'project')
        stages = (*component_inputs.CACHEABLE, 'kernel')
        for arch in ('amd64', 'arm64'):
            before = {s: component_inputs.manifest(project, s, arch) for s in stages}
            for name in ['0004-inet-lookup-lifetime-ordering.patch', 'upstream/99288-ordering.patch', 'upstream/99288-adaptation.json']:
                path = project/'patches/kernel'/name
                saved = path.read_bytes()
                path.write_bytes(saved+b'\n# mutation\n')
                after = {s: component_inputs.manifest(project, s, arch) for s in stages}
                self.assertEqual({s for s in stages if before[s] != after[s]}, {'kernel'})
                path.write_bytes(saved)

    def test_lookup_rejects_recycled_unhashed_and_changed_tuples_before_return(self):
        source = self.patched()
        ipv4 = existing.function((source/'net/ipv4/inet_hashtables.c').read_text(), '__inet_lookup_established')
        ipv6 = existing.function((source/'net/ipv6/inet6_hashtables.c').read_text(), '__inet6_lookup_established')
        self.c(r'''#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#define likely(x) (x)
#define unlikely(x) (x)
typedef uint32_t __be32;
typedef uint16_t __be16;
typedef uint16_t u16;
typedef unsigned __portpair;
struct net {int unused;};
struct in6_addr {int unused;};
struct hlist_nulls_node {void *pprev;};
struct sock {unsigned sk_hash; int sk_refcnt; struct hlist_nulls_node sk_nulls_node;};
struct inet_ehash_bucket {int chain;};
struct inet_hashinfo {unsigned ehash_mask; struct inet_ehash_bucket *ehash;};
struct sock good, recycled, *candidate;
struct hlist_nulls_node end;
int mode, checks, acquires, put_count, walks;
#define INET_ADDR_COOKIE(name,a,b) const unsigned long name=0
#define INET_COMBINED_PORTS(a,b) 0
#define inet_ehashfn(...) 0
#define inet6_ehashfn(...) 0
#define sk_nulls_for_each_rcu(sk,node,head) for (int j=(walks++,0); j<1 && ((sk=candidate),(node=&end),1);j++)
int match(void) { checks++; return !(mode==2 && checks==2) && mode!=3; }
#define inet_match(...) match()
#define inet6_match(...) match()
int refcount_inc_not_zero_acquire(int *r) { acquires++; if (!*r) return 0; ++*r; return 1; }
int hlist_nulls_unhashed_lockless(struct hlist_nulls_node *n) {return !n->pprev;}
void sock_gen_put(struct sock *s) {assert(s->sk_refcnt>1); s->sk_refcnt--; put_count++; candidate=&good; mode=0;}
int get_nulls_value(const struct hlist_nulls_node *n) {return 0;}
'''+ipv4+ipv6+r'''
int main(void) {
 struct net net={0}; struct inet_ehash_bucket bucket={0};
 struct inet_hashinfo hash={0,&bucket}; struct in6_addr addr={0};
 for (int family=0;family<2;family++) for(int scenario=0;scenario<5;scenario++) {
  good=(struct sock){0,1,{&good}}; recycled=(struct sock){0,1,{&recycled}};
  candidate=&recycled; mode=scenario; checks=acquires=put_count=walks=0;
  if(scenario==1) recycled.sk_nulls_node.pprev=0;
  if(scenario==4) recycled.sk_refcnt=0;
  struct sock *result=family ? __inet6_lookup_established(&net,&hash,&addr,1,&addr,2,0,0)
                              : __inet_lookup_established(&net,&hash,0,1,0,2,0,0);
  if(scenario==1 || scenario==2) {assert(result==&good);assert(put_count==1);assert(walks==2);}
  else if(scenario==3 || scenario==4) {assert(!result);assert(!put_count);}
  else {assert(result==&recycled);assert(!put_count);}
 }
 return 0;
}
''')

    def test_refcount_helpers_preserve_zero_retry_overflow_and_old_api_semantics(self):
        source = self.patched()
        text = (source/'include/linux/refcount.h').read_text()
        names = ['refcount_set', 'refcount_read', '__refcount_add_not_zero',
                 'refcount_add_not_zero', '__refcount_inc_not_zero', 'refcount_inc_not_zero',
                 'refcount_set_release', '__refcount_add_not_zero_acquire',
                 'refcount_add_not_zero_acquire', '__refcount_inc_not_zero_acquire',
                 'refcount_inc_not_zero_acquire']
        functions = ''.join(existing.function(text, name) for name in names)
        self.assertNotIn('__signed_wrap', text)
        self.assertIn('-fno-strict-overflow', (source/'Makefile').read_text())
        self.c(r'''#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <limits.h>
#define __must_check
#define unlikely(x) (x)
#define REFCOUNT_SATURATED (INT_MIN/2)
#define REFCOUNT_ADD_NOT_ZERO_OVF 1
typedef struct {int counter;} atomic_t;
typedef struct {atomic_t refs;} refcount_t;
int acquire_ops, relaxed_ops, release_ops, warns, retry;
int atomic_read(const atomic_t *a) {return a->counter;}
void atomic_set(atomic_t *a,int n) {a->counter=n;}
void atomic_set_release(atomic_t *a,int n) {release_ops++;a->counter=n;}
bool compare(atomic_t *a,int *old,int n) {
 if (retry) {retry=0;a->counter=7;}
 if(a->counter!=*old){*old=a->counter;return false;}
 a->counter=n;return true;
}
bool atomic_try_cmpxchg_relaxed(atomic_t *a,int *o,int n) {relaxed_ops++;return compare(a,o,n);}
bool atomic_try_cmpxchg_acquire(atomic_t *a,int *o,int n) {acquire_ops++;return compare(a,o,n);}
void refcount_warn_saturate(refcount_t *r,int kind){assert(kind==REFCOUNT_ADD_NOT_ZERO_OVF);warns++;r->refs.counter=REFCOUNT_SATURATED;}
'''+functions+r'''
int main(void){
 refcount_t r={{0}};int old=-1;
 assert(!refcount_inc_not_zero_acquire(&r));assert(!acquire_ops);assert(!r.refs.counter);
 refcount_set_release(&r,1);assert(release_ops==1);
 assert(refcount_inc_not_zero_acquire(&r));assert(r.refs.counter==2 && acquire_ops==1);
 assert(refcount_add_not_zero_acquire(2,&r));assert(r.refs.counter==4);
 retry=1;refcount_set(&r,3);assert(__refcount_inc_not_zero_acquire(&r,&old));assert(old==7 && r.refs.counter==8);
 refcount_set(&r,INT_MAX);assert(refcount_inc_not_zero_acquire(&r));assert(warns==1 && r.refs.counter==REFCOUNT_SATURATED);
 refcount_set(&r,-1);assert(refcount_inc_not_zero_acquire(&r));assert(warns==2 && r.refs.counter==REFCOUNT_SATURATED);
 int acquired=acquire_ops,released=release_ops;
 refcount_set(&r,0);assert(!refcount_inc_not_zero(&r));refcount_set(&r,2);assert(refcount_inc_not_zero(&r));
 assert(relaxed_ops==1 && acquire_ops==acquired && release_ops==released);
 return 0;
}
''', libraries=('-fno-strict-overflow',))

    def test_atomic_contract_and_publication_positions_from_exact_source(self):
        source = self.patched()
        def text(name): return (source/name).read_text()
        instrumented = text('include/linux/atomic/atomic-instrumented.h')
        fallback = text('include/linux/atomic/atomic-arch-fallback.h')
        self.assertIn('raw_atomic_set_release(v, i);', existing.function(instrumented, 'atomic_set_release'))
        self.assertIn('raw_atomic_try_cmpxchg_acquire(v, old, new);', existing.function(instrumented, 'atomic_try_cmpxchg_acquire'))
        self.assertIn('smp_store_release(&(v)->counter, i);', existing.function(fallback, 'raw_atomic_set_release'))
        self.assertIn('raw_atomic_cmpxchg_acquire(v, o, new)', existing.function(fallback, 'raw_atomic_try_cmpxchg_acquire'))
        self.assertIn('raw_cmpxchg_acquire(&v->counter, old, new)', existing.function(fallback, 'raw_atomic_cmpxchg_acquire'))
        self.assertIn('#define raw_cmpxchg_acquire arch_cmpxchg_acquire', fallback)
        arm = text('arch/arm64/include/asm/cmpxchg.h')
        self.assertIn('#define arch_cmpxchg_acquire(...)\t__cmpxchg_wrapper(_acq, __VA_ARGS__)', arm)
        self.assertIn('__CMPXCHG_CASE(w,  , acq_, 32,        , a,  , "memory", K)', text('arch/arm64/include/asm/atomic_ll_sc.h'))
        self.assertIn('"1:\tld" #acq "xr"', text('arch/arm64/include/asm/atomic_ll_sc.h'))
        self.assertIn('asm volatile ("stlr %w1, %0"', text('arch/arm64/include/asm/barrier.h'))
        connection = text('net/ipv4/inet_connection_sock.c')
        timer = existing.function(connection, 'reqsk_timer_handler')
        self.assertLess(timer.index('timer_setup(&nreq->rsk_timer'), timer.index('refcount_set_release(&nreq->rsk_refcnt'))
        normal = existing.function(connection, 'reqsk_queue_hash_req')
        self.assertLess(normal.index('smp_wmb()'), normal.index('refcount_set(&req->rsk_refcnt'))
        timewait = existing.function(text('net/ipv4/inet_timewait_sock.c'), 'inet_twsk_hashdance')
        self.assertLess(timewait.index('inet_twsk_add_node_rcu'), timewait.index('refcount_set_release(&tw->tw_refcnt, 3)'))
        self.assertLess(timewait.rindex('spin_unlock(lock)'), timewait.index('refcount_set_release(&tw->tw_refcnt, 3)'))
        self.assertNotIn('smp_wmb()', timewait)
        purge = existing.function(text('net/ipv4/inet_timewait_sock.c'), 'inet_twsk_purge')
        self.assertEqual(purge.count('refcount_read(&sock_net(sk)->ns.count)'), 2)
        self.assertIn('refcount_inc_not_zero_acquire', purge)
        bpf = text('net/core/filter.c')
        bpf = bpf[bpf.index('BPF_CALL_3(bpf_sk_assign,'):bpf.index('static const struct bpf_func_proto bpf_sk_assign_proto')]
        self.assertEqual(bpf.count('refcount_inc_not_zero_acquire'), 1)
        for name, function, setter in [
            ('net/ipv4/syncookies.c','tcp_get_cookie_sock','refcount_set_release(&req->rsk_refcnt, 1)'),
            ('net/ipv4/tcp_fastopen.c','tcp_fastopen_create_child','refcount_set_release(&req->rsk_refcnt, 2)')]:
            self.assertEqual(existing.function(text(name), function).count(setter), 1)

    def test_migration_failure_drops_owned_refs_not_transient_lookup_refs(self):
        source = self.patched()
        text = (source/'net/ipv4/inet_connection_sock.c').read_text()
        timer = existing.function(text, 'reqsk_timer_handler')
        start = timer.index('\tif (nreq) {', timer.index('Even if we can clone'))
        cleanup = timer[start:timer.index('\ndrop:', start)]
        clones = []
        for name in ('inet_csk_complete_hashdance', 'inet_csk_listen_stop'):
            body = existing.function(text, name)
            self.assertNotIn('__reqsk_free(nreq)', body)
            match = re.search(r'reqsk_migrate_reset\(nreq\);\s*reqsk_put\(nreq\);', body)
            self.assertIsNotNone(match, name)
            clones.append(match[0])
        self.c(r'''#include <assert.h>
#include <stddef.h>
struct request_sock {int refs,resets;};
int calls,frees;
void reqsk_put(struct request_sock *r){assert(r->refs>0);calls++;if(!--r->refs)frees++;}
void reqsk_migrate_reset(struct request_sock *r){r->resets++;}
#define __NET_INC_STATS(n,k) ((void)0)
#define reqsk_queue_removed(q,r) ((void)0)
void timer_failure(int lost_ehash,int lookup){
 struct request_sock r={3-lost_ehash+lookup,0},*nreq=&r;void *queue=NULL;
 calls=frees=0;
 if(lost_ehash)goto no_ownership;
'''+cleanup+r'''
 assert(calls==3-lost_ehash && r.refs==lookup && r.resets==1);
 assert(frees==!lookup);if(lookup){reqsk_put(&r);assert(frees==1);}
}
void hashdance_failure(int lookup){
 struct request_sock r={1+lookup,0},*nreq=&r;calls=frees=0;
'''+clones[0]+r'''
 assert(calls==1 && r.refs==lookup && r.resets==1 && frees==!lookup);
 if(lookup){reqsk_put(&r);assert(frees==1);}
}
void listen_stop_failure(int lookup){
 struct request_sock r={1+lookup,0},*nreq=&r;calls=frees=0;
'''+clones[1]+r'''
 assert(calls==1 && r.refs==lookup && r.resets==1 && frees==!lookup);
 if(lookup){reqsk_put(&r);assert(frees==1);}
}
int main(void){for(int hold=0;hold<2;hold++){timer_failure(0,hold);timer_failure(1,hold);hashdance_failure(hold);listen_stop_failure(hold);}return 0;}
''')

    def test_syncookie_failure_defers_free_until_last_reference(self):
        source = self.patched()
        body = existing.function((source/'net/ipv4/syncookies.c').read_text(), 'tcp_get_cookie_sock')
        self.c(r'''#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
typedef unsigned u32;
struct sock;struct sk_buff{int unused;};struct dst_entry{int unused;};
struct request_sock {int rsk_refcnt;};
struct operations {struct sock *(*syn_recv_sock)(struct sock*,struct sk_buff*,struct request_sock*,struct dst_entry*,void*,bool*);};
struct inet_connection_sock {struct operations *icsk_af_ops;};
struct sock {struct inet_connection_sock icsk;unsigned tsoffset;};
struct sock child;
int has_child,queue_ok,lookup_hold,frees,puts_count,releases,drop;
#define inet_csk(s) (&(s)->icsk)
#define tcp_sk(s) (s)
struct sock *create(struct sock *s,struct sk_buff *b,struct request_sock *r,struct dst_entry *d,void *v,bool *own){return has_child?&child:NULL;}
void refcount_set_release(int *r,int n){releases++;*r=n+lookup_hold;}
void reqsk_put(struct request_sock *r){assert(r->rsk_refcnt>0);puts_count++;if(!--r->rsk_refcnt)frees++;}
void __reqsk_free(struct request_sock *r){assert(!r->rsk_refcnt);frees++;}
int rsk_drop_req(struct request_sock *r){return drop;}
#define sock_rps_save_rxhash(c,b) ((void)0)
#define bh_unlock_sock(c) ((void)0)
#define sock_put(c) ((void)0)
struct sock *inet_csk_reqsk_queue_add(struct sock *s,struct request_sock *r,struct sock *c){return queue_ok?c:NULL;}
'''+body+r'''
int main(void){
 struct operations ops={create};struct sock listener={{&ops},0};
 for(int hold=0;hold<2;hold++)for(int success=0;success<2;success++){
  struct request_sock req={0};has_child=1;lookup_hold=hold;queue_ok=success;frees=puts_count=releases=drop=0;
  struct sock *out=tcp_get_cookie_sock(&listener,NULL,&req,NULL,42);
  assert(releases==1 && child.tsoffset==42);
  if(success){assert(out==&child && !puts_count && !frees && req.rsk_refcnt==1+hold);}
  else {assert(!out && puts_count==1 && frees==!hold && req.rsk_refcnt==hold);if(hold){reqsk_put(&req);assert(frees==1);}}
 }
 struct request_sock req={0};has_child=0;frees=puts_count=releases=0;
 assert(!tcp_get_cookie_sock(&listener,NULL,&req,NULL,0));assert(frees==1 && !puts_count && !releases);
 return 0;
}
''')

    def test_fastopen_failure_consumes_child_and_caller_refs_only(self):
        source = self.patched()
        connection = (source/'net/ipv4/inet_connection_sock.c').read_text()
        forget = existing.function(connection, 'inet_child_forget')
        disconnect = existing.function((source/'net/ipv4/tcp.c').read_text(), 'tcp_disconnect')
        self.assertIn('sk->sk_prot->disconnect(child, O_NONBLOCK)', forget)
        self.assertIn('reqsk_fastopen_remove(sk, req, false)', disconnect)
        remove = existing.function((source/'net/core/request_sock.c').read_text(), 'reqsk_fastopen_remove')
        request = existing.function((source/'net/ipv4/tcp_input.c').read_text(), 'tcp_conn_request')
        start = request.index('if (!inet_csk_reqsk_queue_add(sk, req, fastopen_sk))')
        # This failure block has no nested braces; retain the actual C branch.
        branch = request[start:request.index('}', start)+1]
        self.assertNotIn('goto drop_and_free', branch)
        self.c(r'''#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#define TCP_LISTEN 1
#define HZ 100
unsigned long jiffies;
struct sock;struct request_sock;
struct fastopen_queue {int qlen,lock;struct request_sock *rskq_rst_head,*rskq_rst_tail;};
struct sock {int sk_state;struct fastopen_queue queue;struct request_sock *fastopen_rsk;};
struct request_sock {int refs,tfo_listener;struct sock *rsk_listener,*sk;struct {unsigned long expires;} rsk_timer;struct request_sock *dl_next;};
#define tcp_sk(s) (s)
#define tcp_rsk(r) (r)
#define inet_csk(s) (s)
#define icsk_accept_queue queue_holder
/* Preserve production field expressions with one interface-only wrapper. */
#undef inet_csk
struct queue_holder {struct fastopen_queue fastopenq;};
struct inet_stub {struct queue_holder queue_holder;};
struct inet_stub listener_icsk;
#define inet_csk(s) (&listener_icsk)
#define RCU_INIT_POINTER(p,v) ((p)=(v))
#define spin_lock_bh(x) ((void)0)
#define spin_unlock_bh(x) ((void)0)
#define bh_unlock_sock(c) ((void)0)
#define sock_put(c) ((void)0)
int frees,puts_count,drops;
void reqsk_put(struct request_sock *r){assert(r->refs>0);puts_count++;if(!--r->refs)frees++;}
'''+remove+r'''
struct sock *inet_csk_reqsk_queue_add(struct sock *sk,struct request_sock *r,struct sock *child){reqsk_fastopen_remove(child,r,false);return NULL;}
void fail(int hold){
 struct sock listener={.sk_state=TCP_LISTEN},child={0};
 struct request_sock r={.refs=2+hold,.rsk_listener=&listener,.tfo_listener=1},*req=&r;
 struct sock *sk=&listener,*fastopen_sk=&child;child.fastopen_rsk=req;
 listener_icsk.queue_holder.fastopenq.qlen=1;frees=puts_count=drops=0;
'''+branch+r'''
 assert(0);
 drop:
 drops++;assert(puts_count==2 && drops==1 && frees==!hold && r.refs==hold);
 assert(!child.fastopen_rsk && !r.tfo_listener && listener_icsk.queue_holder.fastopenq.qlen==0);
 if(hold){reqsk_put(req);assert(frees==1);}return;
}
int main(void){fail(0);fail(1);return 0;}
''')
