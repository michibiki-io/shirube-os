# Issue 28 security backports

This branch backports source fixes. Existing releases and builds from earlier
commits remain unchanged. Linux stays at 6.6.128; the authenticated Stream archive,
package pins, architecture selection, signing and subscriber configuration stay
on their existing paths. Exposure depends on enabled kernel/network/subscriber
services. Update checking remains disabled by default.

## Provenance and application

`patches/kernel/upstream/provenance.json` records immutable upstream commits and
SHA-256 hashes of original and production patches. `patches/vyos/security/` holds
component provenance manifests, full upstream patches and preparation inputs.
Upstream author, copyright and license notices are retained. Test fixture manifests
identify exact selected original files and archive hashes; these are selected
source excerpts, not a full source or binary audit. GPL/LGPL license texts are in
`LICENSES/`; source-file notices continue to govern the excerpts and patches.

| Component | Complete fixes and adaptation |
| --- | --- |
| Kernel RCU | `742f73df544e03744f2981c467d6542fe8172a2a`: defer both IPv4 option destructor frees. No API adaptation. |
| Kernel checksum | Full prerequisite `33aa3b7f4afbd3da85401c914b5e47ef91a86fb0`, then `a65f69e0f38ffbed1b128029e09097436416dc8b`. Includes SCTP checksum export, NFQUEUE helper, IPv4/bridge/netfilter/xfrm callers. |
| FRR | `b413dbc239841ce4d13824119df4be9c6f06647e`, `693a2e02687cdc9d16501275e05136edea9650d9`, `7676cad65114aa23adde583d91d9d29e2debd045` against `168ade7da94dde087d33ec8de983777249ccac68` plus Stream quilt. |
| update-check | Verbatim `9698284cbe5a8515b453a58859e4c61f1a910337`: pass `wall` an argument list through the real process wrapper. |
| accel-ppp-ng | Pinned `3e30d9b74073bfa7af37434874a30d8c3275b598`; 69 selected complete commits through `9c2c18ec0ae7693a14370c4bb36416c8fc287858`. See the 102-entry `inventory.json` for every disposition, affected path, hash and ordered overlap dependency. |

The existing kernel helper applies top-level patches in sorted order after Stream
preparation on both architectures. Preserve perf patch 0001. The complete NFQUEUE
prerequisite overlaps the checksum fix: their complete before/after diff is one
production patch so reverse detection recognizes the final state. Tests compare
all resulting target bytes to the original sequential upstream patches. No
security hunk is removed. Required RCU/checksum APIs exist in the selected source;
no unrelated stable update is needed.

FRR's Type-3/Type-4 parser contexts use older IPv4-only assignments rather than
upstream macros. The adapted patch inserts all upstream guards verbatim at those
locations, retaining the pinned parser's support policy. The original full patch
is retained separately. FlowSpec and NHC apply without semantic adaptation.
Preparation checks patch states and appends the fixes to the existing quilt
series before native restore and library identity/context capture. Both workers
still reverse archived patches and move `.pc` before `dpkg-source` applies the
whole series. Fixtures use immutable upstream source plus a canned Stream
packaging scaffold containing all ten Stream patches. These fixtures remain
separate from the actual-release source replay described below.

## accel-ppp decisions

Inventory was recorded before production selection. Eighteen merges introduce no
additional source changes; ten CI-only commits are excluded. Five independent
changes are excluded: OpenSSL legacy provider enablement, bonding policy, VPP link
detection, CMake minimum and idle-timeout reporting. No applicability item remains
pending. The 33 T9238 ports include configuration robustness and stability changes;
these are not all separately announced vulnerabilities.

The twelve families in the [VyOS announcement](https://blog.vyos.io/vyos-1.5.1-lts-released-security-fixes-bgp-enhancements-and-automated-upgrades)
map to these selected commits (full SHAs and paths are in the inventory):

| Family | Initial commit and necessary follow-ups |
| --- | --- |
| RADIUS spoofed response | `248e7caa`; T8545 and T9351 chain |
| VRF name overflow | `55a804af`, `177238b1` |
| Host-Uniq overflow | `c93b3675`, `8305387d` |
| PPP control data disclosure | `1a42ab40`, `1957e7d1` |
| PADR Service-Name | `ef7323fd`, `01baa010` |
| IPv6CP stack disclosure | `bbacd993` |
| SSTP length loop | `2fa64759`, `2f18c76d` |
| DHCPv6 status | `24afb859`, `ef78432a` |
| Framed-Route overflow | `7ce6244f` |
| DHCPv6 relay depth | `bd1806e7`, `cc42de13`, `8acbd5d7` |
| DHCPv6 duplicate Rapid-Commit | `371e7f0a`, `8acbd5d7` |
| Accounting authenticator | `be2f54db`, `64311652`, `f52607d4` |

Additional complete families cover hidden L2TP AVPs, AFTR names, RADIUS vendor,
integer/date and truncated attribute parsing, bounded log headers, discovery
allocation/compaction, socket/request/session lifetime, and Message-Authenticator
signing/validation/failover with companion tests. `944d98ec` is essential after
`2fc996b2`: VPP non-device sessions share their channel descriptor. `0436560b`
removes VPP feature arcs before deleting a reachable policer. Hook signatures,
packaging and kernel module build options remain intact; `a9e5f8b4` adds the guarded
kernel >=6.6 packet-handler namespace initialization.

One overlap adaptation is required: the last `64311652` accounting hunk is already
provided by the stronger `fab93be4` lifetime fix. Both authenticator hunks are
retained. Replay verifies the already-present hunk's resulting code, then compares
all 41 final targets with the combined production patch and its before/after
hashes. Production rejects mixed/unknown target states before applying; re-runs
verify the complete final state. All complete originals remain under `upstream/`.
Message-Authenticator controls retain upstream opt-in defaults and response
compatibility; this work does not silently require it for existing RADIUS servers.

## Caches and corresponding source

`security_sources.py` is bound into native/kernel component identities. Every FRR
and update-check patch byte binds native; every accel patch, original, state and
inventory byte binds kernel. Kernel's existing recursive patch identity includes
the complete original/combined patches. FRR library identities bind prepared
source containing the quilt patches, exact preparation recipe and receipts before
lookup/context. Both architectures have mutation coverage. Compiler identities
remain unchanged. Isolated patch mutations leave unrelated component/library
inputs unchanged.

The existing whole-file shared recipe identities mean changes to `build.py`,
`component_inputs.py`, `collect.py` and `extras.py.in` invalidate other component
caches once for this implementation. This conservative shared-recipe invalidation
is unavoidable without changing the cache contract. Subsequent package patch
mutations are scoped as above; no compiler identity is changed.

Retain authenticated original sources and shared project source, not compiler
workspaces. The project archive contains all new patches and ordered recipes.
FRR source retains its quilt additions. Native package receipts are exported as
`security-patches.json` and collected with existing build inputs, including on
FRR library restore. Accel's explicit `shirube-corresponding-inputs` retains the
whole patch set/recipe/receipt even where preferred-path selectors omit the newly
added upstream test. That test is fully reconstructable from the retained patch.
Kernel receipt names and original-source reconstruction remain unchanged.

## Focused validation

From the repository root, offline:

```sh
PYTHONPATH=tests:. python3 -m unittest test_security_backports test_component_inputs test_library_cache test_native_stages test_recipes -v
python3 -m py_compile containers/native/security_sources.py containers/native/build.py containers/native/library_inputs.py scripts/build-library-cache.py producer/component_inputs.py producer/collect.py tests/test_security_backports.py
bash -n containers/native/package.sh
git diff --check
git diff --cached --check
```

Passed: 46 focused tests; source hashes and complete patch equivalence, zero-fuzz Stream/repository
application, repeat receipts, partial-state rejection, both architecture input
mutations, FRR library context binding and normalization, new-file reconstruction,
real update wrapper arguments/shell settings, and small C harnesses with UBSan.
The harnesses extract actual checksum/destructor, FlowSpec, SSTP, AFTR, Host-Uniq
RADIUS authentication code, and VPP/session cleanup functions. NHC/EVPN/VNC checks evaluate extracted production
guards; they do not emulate complete parsers. Upstream RADIUS packet-vector
helpers exercise real OpenSSL verification; daemon-dependent upstream pytest
scenarios are retained but unrun.

Separate actual-release source validation passed using the v2026.03.8 AMD64 FRR
corresponding-source archive:

- Immutable source URL: [shirube-os-v2026.03.8-amd64-source-0071.tar.gz](https://github.com/michibiki-io/shirube-os/releases/download/v2026.03.8/shirube-os-v2026.03.8-amd64-source-0071.tar.gz).
- Size: 13,071,468 bytes; SHA-256:
  `9d2a4447a363107bd4cd9c978caeb67df4815ca2b6a517297bb61b3108f6d00b`,
  verified against the public release manifest.
- Production security preparation and both exact existing ordinary/library
  normalizers passed, with only the output fixture path substituted. The full
  ordered Stream plus security patch series applied at zero fuzz in both paths.
- Preparation, normalization and reapplication reproduced identical hashes for
  all five security-target source files. An inconsistent single-hunk state was
  rejected before cache lookup without changing those source files.
- The production preparation helper SHA-256 remained
  `5cbb8d5af059934586598da99892a9c1103e6c3051874912c7b3bfc52306f01b`
  throughout validation.

This replay validates source and patch state; it does not verify release binaries.
Unrun locally: full kernel/package/image compilation (including native FRR
compilation), boot, native FRR/service/subscriber/VPP runtime integration,
guest/E2E, exploit tests, independent ARM64 artifact validation and full license
suites. The fixture harnesses and extracted guard checks retain the simulation
limits described above.

These are local validation results. CI and PR review evidence must be recorded on
the PR for its exact head commit, including the normal Test workflow, Codex review
and Claude Opus 5.5/medium review. The initial PR is Draft; no merge is performed.
