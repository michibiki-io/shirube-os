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
| vyos-1x | Verbatim `9698284cbe5a8515b453a58859e4c61f1a910337`: pass `wall` an argument list through the real process wrapper; complete calling-sid dependency `28ea92afb73a6627c2050474187ace25bb5e3e4e` (backport of `5354959b60c97df699ad14ef561ab9aa92b37922`). |
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

## calling-sid configuration compatibility (AC10)

The selected accel-ppp `bbacd993` fix requires `ipv6-peer-intf-id-secret`.
The actual pinned vyos-1x source exposed calling-sid without a secret CLI node or
rendering. Complete upstream dependency
[28ea92afb73a6627c2050474187ace25bb5e3e4e](https://github.com/vyos/vyos-1x/commit/28ea92afb73a6627c2050474187ace25bb5e3e4e)
is applied after the existing update-check RCE fix through
`patches/vyos/security/update-check/0002-28ea92afb73a6627c2050474187ace25bb5e3e4e.patch`.
Its unmodified original is retained under that directory's `upstream/`; provenance
records hashes for both patches. This is a dependency of the existing security
fix and AC10 compatibility, without a wholesale vyos-1x update or new package.

All 109 upstream additions are preserved: shared CLI XML, exact daemon option
rendering, pure validation helpers and explicit ConfigError guards in PPPoE,
L2TP, PPTP and SSTP, plus PPPoE restart detection for mode and secret changes.
Only the PPPoE restart hunk context differs: the pinned source has
`authentication radius dynamic-author` and `authentication mode`, while upstream
has `authentication radius`, `authentication mode` and `authentication protocols`.
The backport preserves the pinned authentication entries and inserts both IID
conditions unchanged. Other hunks apply unchanged with line offsets and zero fuzz.

Operators of existing calling-sid deployments must configure
`ppp-options ipv6-peer-interface-id-secret` with a 16–128 character printable,
non-whitespace ASCII secret before committing that configuration with this fix.
The template renders the exact `ipv6-peer-intf-id-secret` daemon option.
Missing or invalid secrets in calling-sid mode raise ConfigError instead of
silently negotiating with IID0. No secret is generated or assigned by this work;
diagnostics name the constraint without printing supplied secret values.
The old image has no secret CLI node, so operators cannot preconfigure it there.
On the first boot after upgrading, existing calling-sid configurations without a
secret fail verification and the affected PPPoE/L2TP/PPTP/SSTP service may remain
unconfigured until the operator sets the secret and commits after boot.
Alternatively, switch to another IID mode before upgrading, then configure the
secret and restore calling-sid on the new image; plan for the service interruption.
These upgrade-time effects follow the validation contract; boot and upgrade-time
configuration loading have not been exercised here.
Other IID modes and default configuration retain their existing behavior.
Changing the IID mode or secret restarts PPPoE sessions so they renegotiate,
as upstream intends; operators should account for that interruption.

The ten-file update fixture includes all six exact old dependency targets from
the verified v2026.03.8 AMD64 source-0506 archive (3,603,752 bytes, SHA-256
`e470a740eca8be41399bc1f1826db18293078569214b499f2cbfd2b523a4b64f`).
Offline checks replay the full original with the documented context mapping,
compare all six results, repeat preparation, reject a partial hunk, and reconstruct
from collected inputs. They execute all four full verification functions with
system dependencies stubbed; boundaries 15/16/128/129 and whitespace, control,
non-ASCII and missing values are checked against the shared XML constraint.
Local Jinja2 renders the actual template using a narrow `vyos_defined` test and
chainable undefined handling; this does not exercise the full VyOS renderer.
Full PPPoE get_config runs with configuration/system stubs to check changed and
unchanged restart inputs; the production restart branch is also executed with a
stubbed command runner. These checks do not start services or change credentials.

## Caches and corresponding source

`security_native.py` and `security_kernel.py` are bound to native and kernel identities respectively; `security_common.py` is shared by both. `security_sources.py` is a legacy entry shim outside compile closures. Every FRR
and vyos-1x update-check/calling-sid patch byte binds native; every accel patch,
original, state and inventory byte binds kernel. Kernel's existing recursive patch identity includes
the complete original/combined patches. FRR library identities bind prepared
source containing the quilt patches, exact preparation recipe and receipts before
lookup/context. Both architectures have mutation coverage. Compiler identities
remain unchanged. Isolated patch mutations leave unrelated component/library
inputs unchanged.

Issue #30 separates physical component recipes and declarations from shared
execution/security contracts. See [STAGED-BUILD.md](STAGED-BUILD.md#component-identities-and-physical-dependency-boundaries-issues-17-and-30)
for the current dependency table, one-time schema migration and maintenance rules.
Subsequent component-only recipe/patch changes leave unrelated inputs stable;
shared contract changes still invalidate every affected component.

Retain authenticated original sources and shared project source, not compiler
workspaces. The project archive contains all new patches and ordered recipes.
FRR source retains its quilt additions. Native package receipts are exported as
`security-patches.json` and collected with existing build inputs, including on
FRR library restore. Accel's explicit `shirube-corresponding-inputs` retains the
`recipe.py` and its `security_common.py` companion, both bound in the receipt input hash inventory, plus the whole patch set/receipt even where preferred-path selectors omit the newly
added upstream test. That test is fully reconstructable from the retained patch.
Kernel receipt names and original-source reconstruction remain unchanged.

## Focused validation

From the repository root, offline:

```sh
PYTHONPATH=tests:. python3 -m unittest test_security_backports test_component_inputs test_library_cache test_native_stages test_recipes -v
PYTHONPATH=tests:. python3 -m unittest test_sources test_reconstruction -v
python3 -m py_compile containers/native/security_sources.py containers/native/security_native.py containers/native/security_kernel.py containers/native/security_common.py containers/native/build_compile.py containers/native/build_release.py containers/native/library_inputs.py scripts/build-library-cache.py producer/component_graph.py producer/component_inputs.py producer/collect.py tests/test_security_backports.py
bash -n containers/native/package.sh containers/native/package_common.sh containers/native/package_native_early.sh containers/native/package_native.sh containers/native/package_kernel.sh containers/native/package_validate.sh
git diff --check
git diff --cached --check
```

Historical issue #28 verification: 51 focused security/component tests and 20 source/reconstruction tests; source hashes and complete patch equivalence, zero-fuzz Stream/repository
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

These are local validation results. Each updated PR head requires its exact-head
Test results and independent Claude re-review on the PR. The initial Draft PR
remains unmerged.
