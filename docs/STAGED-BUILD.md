# Staged hosted build: implementation and acceptance status

Issue [#3](https://github.com/michibiki-io/shirube-os/issues/3), reviewed
[plan](https://github.com/michibiki-io/shirube-os/issues/3#issuecomment-6796764757).
Reviewed issue-body snapshot SHA256:
`39601e1324b7358228d74be05feae84ece913c4fa52258a36278311adccda68f`.
Implementation starts from `d49274d8ea672b0a4770e9ccac179f48bc42a8ad`.

**Candidate implementation; full hosted acceptance is NOT_RUN.** No new image,
package or disk build was executed to implement this change. There is no measured speedup claim.
Historical Test checks passed at `14aedee` as recorded below. CI for the subsequent
readiness edits, external review and staged cold/warm hosted validation remain
pending. OCI real-machine acceptance remains **NOT_RUN**.

## Evidence used to choose boundaries

[Parsed public-log baseline](evidence/diagnostic-issue3-stage-baseline.json) records
completed run `42994713826`, commit `a207309b6181609ec5ccb76b2527db6a4dafad34`.
Each interval is a log STAGE marker to the next marker, including interstitial
packaging/audit work. It is not isolated compiler CPU time.

| Stage | AMD64 seconds | ARM64 seconds |
| --- | ---: | ---: |
| Kernel | 2156.993 | 1358.325 |
| VPP | 2655.991 | 1205.148 |
| Kea | 1525.537 | 955.872 |
| Salt | incomplete: 496.735 before cancellation | 778.603 |
| Go runtime packages | 405.734 | 266.462 |
| Base extras | 143.085 | 77.288 |
| Podman | not reached | 121.973 |
| External modules | not reached | 48.665 |
| Accel | not reached | 22.888 |
| vyos-1x | 215.649 | 169.912 |
| HTTP API | 71.772 | 61.434 |
| NetBird through ISO assembly | not reached | 755.598 |
| Source collection through restoration marker | not reached | 1147.814 |

Exact job logs: [AMD64](https://github.com/michibiki-io/shirube-os/actions/runs/42994713826/job/130436002866),
[ARM64](https://github.com/michibiki-io/shirube-os/actions/runs/42994713826/job/130436002673).
AMD64 was cancelled at 2h35m16s. ARM64 failed at 2h21m36s (build step 2h14m37s)
with a license mismatch; neither job reached fresh disks/guests. Both producer
logs reported nine library restore hits; this does not establish that outer
preparation or the whole run was warm. AMD64 Kernel + VPP + Kea consumed
6338.521 seconds serially. They and Salt are isolated before splitting cheap jobs.

Earlier completed ISO/source trials, commit
`b30e877a04f47d28aef955407825f48cf21df37e`, run `42758751738`:
[AMD64 receipt](evidence/diagnostic-hosted-amd64-native-iso-source-trial.json),
[ARM64 receipt](evidence/diagnostic-hosted-arm64-native-iso-source-trial.json).
They took 11374.467 / 8719.510 seconds and sampled storage growth of
58938322944 / 56688513024 bytes. These were cold **library stores**, excluded
compiler-image preparation, disks, guests and release clearance, and passed
source restoration while failing license clearance. Storage growth is a sampled
lower bound. Neither complete all-layer cold cache nor peak memory was measured.

## Job graph and resource admission

Both release and intentional pre-merge validation call
[staged-candidate.yml](../.github/workflows/staged-candidate.yml). Every stage has
its own disposable native hosted job/container and output. There is no shared
mutable compiler workspace. The two architecture matrices use fixed
`ubuntu-24.04` (AMD64) and `ubuntu-24.04-arm` (ARM64); runner-variable overrides
and self-hosted runners are excluded from this release path.

This adjacency table specifies the job dependency graph. Every stage also waits
for `compiler`, which prepares and exports the exact public producer image once per architecture.

| Stage | Direct predecessors | Work | Timeout minutes |
| --- | --- | --- | ---: |
| compiler | none | locked native compiler image; no runtime signing keys | 120 |
| native | compiler | 25 non-kernel declared jobs, ordered internally | 180 |
| vpp | compiler | VPP binaries, patched preferred sources, explicit SDK | 180 |
| kea | compiler | Kea with actual prepared Debian/Meson inputs | 180 |
| salt | compiler | Salt plus pinned SDK build, actual Rust compiler receipt | 180 |
| runtime | compiler | base, Go exporters/Telegraf, Podman | 180 |
| kernel | compiler, vpp | Kernel, modules, accel + host-sflow; private signing key stays here | 180 |
| integration | compiler, native, vpp | cloud-init, branded core/integration, NetBird linked-source offline reconstruction | 180 |
| iso | compiler, all six compile tranches, integration | repository/ISO assembly and binary/signature inspection; no package compilation | 180 |
| clearance | compiler, iso | actual ISO factory audit, complete corresponding sources/license clearance and full restoration, once | 180 |
| guests | compiler, clearance | fresh install/distribution disks and complete guest lifecycle; existing manifest validator | 330 |
| release resolve / stage / publish | event / complete candidate / both architectures | existing reservation, validation, draft staging and final promotion | 15 / 30 / 30 |

The six declared native edges are retained within `native`: libtacplus-map →
libpam-tacplus; libtacplus-map and libpam-tacplus → libnss-tacplus; libnftnl →
nftables; libyang → frr; python3-vici → vyos-1x. Kernel is the 26th declared job.
Grouping these small jobs avoids repeated dependency compiler environments.
The VPP → Kernel dependency is deliberate: accel consumes both the matching
kernel signing environment and VPP SDK. VPP and Kernel cannot be independent
without moving the private signing key. This can lengthen their critical path.

The current matrix-level `needs` barriers wait for both architectures of each
prerequisite; queueing and the slower architecture therefore affect elapsed
critical paths. The candidate path is compiler → max(native, Kea, Salt, runtime,
VPP → Kernel, max(native,VPP) → integration) → ISO → clearance → guests.
Artifact transfer/compiler restoration overhead must be measured before quoting
this expression as a duration or a speedup.

GitHub currently documents a six-hour hosted job limit and, for both selected
public Linux labels, 4 CPUs, 16 GB RAM and **14 GB SSD**:
[limits](https://docs.github.com/en/actions/reference/limits),
[public runner resources](https://docs.github.com/en/actions/reference/runners/github-hosted-runners).
These specifications were checked on 2026-11-11. Prior
[capability measurements](evidence/diagnostic-native-hosted-runner-capabilities.json)
observed substantially larger filesystems, not a provider capacity guarantee.
Compiler/runtime containers enforce 4 CPUs / 8 GiB RAM / no swap / 4096 PIDs.
Integration alone adds `--cap-add=SYS_ADMIN` for NetBird offline `unshare --net`;
ISO alone uses `--privileged` for assembly mounts. Other directly launched stages
add no capabilities; the guests stage retains the existing release-container
helper. No Docker socket, host identity, host PID/network namespace or publication
token is mounted. The tiny hosted capability fixture now checks distinct network
namespaces under SYS_ADMIN alone and validates its saved image archive with the
unchanged strict compiler validator. Each run records the results in its
`runner-capability-*` artifact; fixture success cannot establish NetBird
reconstruction or compiler handoff.
Compiler-image BuildKit uses the hosted runner; its peak is not established.
The existing 75 GiB compiler/source/ISO and 56 GiB fresh-disk free-space gates
remain. An insufficient runner fails admission; thresholds are not lowered to
match the advertised 14 GB minimum. Standard-label capacity alone cannot prove
feasibility. Every deadline above is a provisional bound below 360 minutes;
**sufficient measured end-to-end headroom for these new jobs is not established**.

## Transfer, sources and cache contracts

Issue [#13](https://github.com/michibiki-io/shirube-os/issues/13) adds one
`apt-get -o APT::Update::Error-Mode=any update` at the `finish_compile()`
extras boundary for each of `vpp`, `kea`, `salt`, `runtime` and `kernel`, before
the first extra recipe. The producer image removes APT lists, so isolated
extras must refresh the configured authenticated fixed-snapshot indexes before
installing dependencies. Kernel reaches this boundary after its catalog job;
the existing `package.sh` updates remain unchanged and are outside this count.
Any refresh failure, including a partial index failure, stops all extra recipes
and successful export. Catalog-only `native` and verified stage-cache hits gain
no refresh; cache hits continue to bypass compilation. Pinned inputs, compiler
and artifact identities, source/license gates and release cache-write restrictions
remain unchanged.

`python3 -m unittest tests.test_extra_stage_apt -v` passes four synthetic tests
(42 stage/architecture/failure subcases), exercising the real `finish_compile()`
and real stage-cache dispatch with mocked compilation/external effects. No real
APT or package build runs. These regressions do not establish hosted acceptance:
current-code compilation and required release gates on both architectures remain
**NOT_RUN** for this correction; the earlier failed Release run cannot validate it.

`producer.stage_artifacts` binds a reusable content scope (architecture, locked
inputs, recipes/patches, compiler fingerprint and predecessor content hashes) to
current resolved commit/version in a separate transfer receipt. Cached content
also retains its original `content_source_commit`. Unrelated commit attribution
is excluded from the input ID. License-decision JSON and guest files are excluded
from compile-tranche scope, but included in downstream clearance/integration
scopes; a notice decision alone does not invalidate unchanged heavy dependencies.
Public source/notice/build inputs and artifact
bytes have precise file inventories; consumers rederive recipe scope and verify
the archive hash, receipt, every member, sizes, payload identity and provenance
before use. Missing/extra/duplicate/traversal/link/special/mismatched members and
cross-architecture artifacts fail closed. Supplied ancestors must agree with
dependency identities. There is no raw compiler-workspace export.

The source exporter selects authenticated original native preferred sources,
actual HTTP input material, OCaml source archives/notices, patched tracked VPP
and Salt files, exact prepared Kea recipes, and explicit snapshot source members
plus generated dependency/source inventories. `collect(material=...)` consumes
these key-free layouts rather than absolute private compiler paths. Confined
source aliases are materialized as regular files. Existing exact public source
fixture allowances remain hash-bound; generated kernel keys and arbitrary
private keys have no exceptions. No new license or security waiver is introduced.
The existing `source_reuse.py` remains preferred-source metadata supplementation;
it is not this staging engine.

Kernel + modules + accel compile/sign in one private container. It consumes the
verified VPP DEBs and `vpp-sdk/{source,generated,lib}`: explicit upstream/generated
headers and link-library inventories, including filenames used by accel's CPATH
and LIBRARY_PATH. Stage transfers contain only signed DEBs, public certificate
and selected key-free source/build inputs. The SDK includes notices through the retained
VPP corresponding-source/patch material. No signing-key or build-tree cache
exists. The separate optional compiler-result boundary is described under
“Kernel compiler results before signing (Issue #36)” below. Package and ISO
signature audits still use the matching public certificate.

`native`, `vpp`, `kea`, `salt` and `runtime` can reuse verified unsigned payloads.
Each entry is rechecked against the compiler/input scope and payload inventory;
missing, evicted, incompatible or corrupt caches cause a fresh source build.
A fresh build still must pass all source/package/security gates. Kernel, ISO,
clearance, guests and integration are not restored from a stage-output cache.
Compiler images contain only public fixed build inputs and source. Only the
`compiler` jobs invoke BuildKit; downstream jobs never rebuild the image. Each
architecture exports `compiler.tar` and `compiler.json` as a required same-run
artifact. The manifest binds the full source commit, run ID, Linux architecture,
all tracked Dockerfile COPY inputs/recipe/base identity, immutable image ID and
archive size/SHA256. Consumers download only their architecture's named artifact
from the current run. Before Docker load they reject missing/extra/nonregular
files and verify provenance, archive hash/size, the single saved image's config
hash and platform. After load, Docker inspect must confirm the exact image ID,
OS and architecture before any stage executes. Docker load progress goes to stderr;
the CLI writes only one validated `imageid=sha256:...` assignment to stdout after
all checks succeed, so it can be redirected into `GITHUB_OUTPUT`. There is no
registry push or fallback reconstruction. The image contains no runtime signing keys, mounted
host state or publication credentials.

Cold compiler production omits cache-from and sets no-cache; cold stage jobs
skip Actions cache restore and start with empty cache directories.
Eligible, non-no-op `pull_request_target` Releases automatically attempt shared
main cache population after the existing resolution/reservation gates. Release
and validation callers default to literal GitHub `cache-mode: read`. Release's
build caller explicitly uses `write`. Validation uses mutually exclusive callers:
only original-repository main `workflow_dispatch` uses literal `write`; PR,
feature and fork-repository validation use `read`. The reusable workflow has no
top-level cache-mode: compiler/native/vpp/kea/salt/runtime inherit the caller's
effective mode and cap; admission/kernel/integration/iso/clearance/guests explicitly
use `read`. A job overrides its workflow default. A callee requesting beyond an
explicit caller cap is a validation error even if step guards would block saves.
No expressions are used for this keyword. The independent cache-mode validator
checks literals, inheritance and caller caps before the narrow actionlint 1.7.12
unsupported-key suppression; other lint checks remain enabled.

The reusable workflow independently admits the caller with no checkout or project
imports; every build job depends on it. Its `cache-write` output controls every
BuildKit export and stage save. Release writes require `michibiki-io/shirube-os`,
main ref, closed/merged main PR, matching base repository and exact event merge
SHA. A queued merge SHA need not equal `github.sha` or the later main tip.
Optional main dispatch writes require source exactly equal to `github.sha`;
the resolver checks this in inline shell before repository Python. PR validation,
fork repositories and non-main dispatches cannot populate shared main caches.
Unknown events, malformed commits, cache modes other than cold/warm and mismatched
source/event data are rejected. Buildx keeps `cache-binary: false` and cache-v2
input/provenance code is unchanged. The bounded import below adds only its
necessary token read grant.

GitHub `cache-mode` (`read`/`write`) controls service access; `inputs.cache-mode`
(`cold`/`warm`) selects prior-cache use. Merge approval authorizes the merged build
recipes to execute with cache credentials. All merged recipes and all shared-main
cache writers must be trusted: hashes verify integrity/provenance but cannot
protect against a malicious trusted writer. The [2026-11-12 rules snapshot](RELEASE.md)
records ruleset 26762240 approval/Code Owner/resolved-thread requirements and
deletion/non-fast-forward protection, with no returned required status checks or
merge_queue rule. No protections were changed.

既存Releaseは、適格かつno-opでないmainマージで共有キャッシュへの保存を自動的に試みます。
Releaseと検証workflowはGitHub `cache-mode: read`を既定とし、Releaseのbuild呼び出しと
元リポジトリのmain手動検証だけがリテラルの`write`を指定します。
再利用workflowにはトップレベルの指定を置かず、保存する6ジョブが呼び出し元の実効モードと上限を継承します。
admission、kernel、integration、iso、clearance、guestsは明示的にreadを指定します。
ジョブ指定はworkflow既定値を上書きします。呼び出し元の明示的な上限を超える要求は、
保存stepの条件に関係なく検証エラーになります。
inline admissionはcheckout前にイベントと正確なソースを検証し、すべてのexportとsaveを制御します。
Releaseはイベントのmerge SHAを使い、待機中に進んだmain先端との一致は求めません。
手動main検証は`github.sha`との一致が必要で、PR、fork、feature dispatchはreadに制限します。

GitHubのread/writeはアクセス権限で、`inputs.cache-mode`のcold/warmとは別です。
mainへのマージ承認は、キャッシュ認証情報を持つレシピの実行許可になります。
承認済みレシピとmainの全writerを信頼する必要があり、ハッシュでは悪意ある信頼済みwriterを防げません。
2026-11-12のruleset 26762240の記録には承認、Code Owner、スレッド解決、削除禁止、
non-fast-forward禁止があり、required status checkとmerge_queueは返されていません。
保護設定は変更していません。
kernelの署名用秘密鍵と状態、clearance、guest成功結果は共有キャッシュに入れず、毎回新しく生成または検証します。
実runnerでの保存成功と次回warm hitは**NOT_RUN**です。

Successful eligible stages save only after success when there was no exact
outer hit. Compiler BuildKit export is optional (`ignore-error=true`). Restore
attempts and optional exports do not establish verified reuse. Original content
attribution, exact inventory/hash/scope validation and all downstream gates
remain required; kernel signing and key-owning build-tree state are never cached. Hosted warm
acceptance, capacity, warm performance and actual cross-run reuse remain NOT_RUN.
Actions caches are acceleration, while Actions artifacts are required handoffs.
Cache eviction cannot replace an artifact or a failed prerequisite.

`producer.stage_metrics` records stage wall time, enforced resource profile,
cgroup-v2 container-lifetime memory.peak where exposed, 2-second whole-filesystem
samples, cache hit/miss, current commit/version/input identity and incoming/outgoing
transfer hashes/bytes. Filesystem samples are lower bounds, not true disk peaks.
Container memory peak is not host peak. A completed source restoration also emits
a sanitized hash-bound clearance diagnostic, including a FAIL license decision;
that diagnostic cannot clear a candidate. Metrics and diagnostics are separate from cleared release
assets. GitHub download/upload step timings and compiler preparation resource
peaks still require job-log evidence; worker wall time excludes those outer steps.

### Component identities and physical dependency boundaries (issues #17 and #30)

[`producer/component_inputs.py`](../producer/component_inputs.py) defines one
versioned manifest for each existing compile boundary. `build_scope()` hashes
that manifest for both the producer's validation and the Actions outer key.
The manifest records architecture, selected source-lock values, required recipe
files (including executable state), ordered patch inventories and selected
catalog jobs with their dependency graph and flags. Missing declared files,
missing selected lock values, unknown components/architectures and incomplete
selected job graphs fail closed. Repository `containers/native` and `patches/*`
paths normalize to the Dockerfile's `/src/native`, `/src/vyos-patches` and
`/kernel-patches` aliases; host/container equivalence is regression-tested.
Compile identities never hash the entire `source-lock.json` or producer tree.
Noncompile scopes recursively cover native Python/shell/template/JSON inputs and producer Python modules, including the relocated library preparation scripts.

| Component | Additional selected inputs |
| --- | --- |
| native | Nonkernel catalog jobs; package/HTTP/Rust recipes and locks; Cargo/Debian source helpers |
| vpp | VPP and VPP-patches revisions; VPP Python/external locks and input helper |
| kea | Kea packaging revision and pinned Meson build tool |
| salt | Salt tools revision, SDK/source lock, Salt Python/Cargo/source helpers |
| runtime | vyos-utils/i2util revisions; Go SDK, Telegraf dependency and notice overrides; Go module/notice helpers and FPM recipe |
| kernel | Kernel job, ordered kernel patch tree, nat-rtsp/ipt-netflow revisions, embedded notices and module/signing recipes; verified VPP input and payload identity; **uncacheable** |

All compile stages authenticate the Stream source and export full vyos-build
recipe material, prepare branding, Bash archive and network-diagnostic metadata,
and retain source preparation/provenance. Therefore every manifest includes the
full Stream authentication record, selected architecture base, Debian snapshots,
LICENSE/logo, `force_mtu_host.sh`, and explicit shared source/export/security
helpers. Actual toolchain declarations include the Dockerfile toolchain prefix,
architecture base pin, gem installer and gem lock. These declarations also cover
pinned non-dpkg tools; the effective installed compiler fingerprint remains an
independent input. The current `.dockerignore` excludes Git/work/output/Python
cache artifacts and does not exclude declared build inputs. Same-run compiler
artifact validation still checks it and the complete current COPY context.

The physical dependency graph lives in `containers/native/inputs-common.json`
and six independent `inputs-{native,vpp,kea,salt,runtime,kernel}.json` declarations.
Each declaration records roots, direct graph edges, explicit dynamic routes and
normalized dynamic-import expressions,
rendered templates, source-lock selections, external revision selections and
complete patch trees. The common declaration contains no component registry.
`producer/component_graph.py` validates and traverses the graph;
`component_inputs.py` hashes every whole physical recipe/helper/declaration file
and executable bit. The three structured documents (`source-lock.json`,
`packages.json`, `external-sources.lock.json`) retain explicit selected-value
semantics; recipes never use function-text hashing or hash exclusions.

| Boundary | Physical inputs and dispatch |
| --- | --- |
| Shared stage execution/transport | `staged.py` public dispatch, `staged_compile.py` restore/cache/compile/pack/save/cleanup, `stage_transport.py` exact cohort/metadata/ancestry validation, `stage_metrics.py`; all whole-file hashed in every compile graph |
| Shared compiler engine | `build_compile.py`, `native_stages.py`, `native_common.py`, `recipes.py`; authentication, execution, selected package jobs, all-DEB auditing and transfer export |
| Shared security/source contracts | `security_contract.py`, `stage_artifacts.py`, `source_selectors.py`, source fixture and public-test-key contracts; `source_contract.py` for archive-producing native/Salt exporters |
| Per-component export/declarations | `producer/compile_<component>.py`, `inputs-<component>.json`; source roots, extras selection and source/export selectors; VPP SDK export resides in `vpp_sdk.py` (also used by the full-build kernel fallback), kernel-only VPP/signing workspace preparation in `compile_kernel.py` |
| Native packages | `package_native.sh`, `package_native_early.sh`, `security_native.py`, HTTP/Rust inputs, library recipes; FRR/update-check hooks in `compile_native.py` |
| Kernel | `package_kernel.sh`, `extra_kernel.py.in`, `security_kernel.py`, signing template and complete kernel/accel security patch trees |
| VPP / Kea / Salt / runtime extras | Corresponding `extra_<component>.py.in`; `extra_common.py.in` shares execution/packaging helpers; `select_vpp.py`, `select_kea.py`, `select_runtime.py` own export details |
| Native/kernel shared preparation | `package_common.sh`, `package_validate.sh`, `security_common.py`; common changes invalidate both |
| ISO / clearance / guests / full release | `build_release.py`, `image.py`, `image_templates.py`, `collect.py`, `staged_release.py`, pipeline and integration/guest modules; outside every compile closure |

`build.py` routes legacy full builds to `build_release.py`; staged compilation
uses `python -m producer.staged`, whose literal `ENGINES[stage]` route selects
`staged_compile.py`; that engine calls `build_compile.py` directly. The facade,
selected engine, shared restore validator and resource metrics are whole-file
inputs in every compile closure. The graph audit independently resolves this
literal table for the selected component and requires the resulting module edge;
normalized expression declarations still reject new undeclared dynamic calls.
Downstream routes select `staged_release.py`, whose integration/ISO/clearance/guest
bodies remain physically outside compile closures. The existing public scope,
transfer and downstream APIs remain available through the facade. Both
`--stage value` and `--stage=value` select the compile engine in `build.py`. The producer image now has the generic
`python3` entry point; package/ISO/release launchers explicitly pass `build.py`.
Docker COPY layout is unchanged. The whole Dockerfile remains an environment
recipe input, while same-run image admission still inventories the full COPY
context independently of compile scopes. Its COPY destinations are not Python
imports: the static recipe audit below examines Python and shell recipes.

The compile generator renders only the selected declaration's templates. The
wildcard ISO generator resides in `image_templates.py`; compile closures do not
import it. Collection reexports preferred-source selectors for its existing
callers but selectors do not import collection. NetBird/guest/cloud-init/GeoIP,
full-release orchestration and clearance-only edits leave all compile keys
stable. A VPP input change affects VPP directly and kernel through the verified
predecessor input **and payload** identity. Kernel and signing remain fresh.
FPM still inspects every ELF payload for Go dependencies, including kernel
packages; kernel therefore also declares the Go SDK and Go notice overrides.
No non-target binary audit is skipped to achieve isolation.

Security preparation retains the selected native or kernel module as standalone
`recipe.py` plus `security_common.py`. Receipts bind both complete files in an
`inputs` hash inventory, the original `recipe_sha256`, and every patch/provenance
input. Both companions preserve their legal notice. Replay requires only the
original source, the retained companions and patch tree; native replay supports
FRR quilt preparation as well as update-check application. Native changes never
alter a kernel retained recipe, and kernel changes never alter native/library
source identity; a shared security helper change affects both.

The offline audit recognizes Python by suffix or Python shebang (including the extensionless `apt-get` wrapper and its `apt_archive.py` dependency), and derives module-level and delayed Python imports, bare native
imports, and both architecture renderings of templates. It resolves literal
`/src/`, `/opt/stream/` and patch aliases, constant path joins, literal shell paths,
`Path(...).glob/rglob(...)`, and literal `copytree` inputs. Every discovered file
must be an explicit graph edge or a member of a declared complete patch tree.
Missing/unknown nodes, declarations, omitted rendered templates, unresolved local imports, undeclared known
forms of dynamic import, nonregular inputs and symlinks at files or any ancestor
(including mapped parent patch directories) fail closed. Literal selected lock
reads and external `git('name', ...)` calls are also checked against selections.

This is syntax analysis, not an arbitrary Python/shell interpreter. Variable
path construction, computed dictionary keys, imported/aliased callables,
reflection, shell expansion through variables and dependencies discovered in
upstream sources cannot all be inferred. Directory roots alone do not mean all
members are consumed. Declare these routes/trees/selected values explicitly,
review their callers and add a mutation test. Existing dynamic routes include
component module dispatch, selected template/shell rendering, library worker and
prepare-script copying, authenticated source snapshot selectors and external Git
selection. Upstream scripts/patches remain bound by authenticated Stream and
external revisions, locks and complete source inventories. Do not claim this
check proves detection of every possible dynamic dependency.

Focused driver regressions mutate the facade, compile execution engine, shared
restore/transport validator, cache implementation, transfer contract and metrics
files on both architectures; each changes all six compile scopes. A mutation of
`staged_release.py` changes none. They also check every new module in downstream
scopes, VPP-to-kernel propagation, host/container identity, public CLI dispatch,
real scope/pack/unpack/cache-hit wiring and fresh kernel execution. These are
local fixture checks; hosted CI, full builds and hosted cross-run HIT are NOT_RUN.

Maintenance requires updating the owning component declaration and its direct
edges whenever imports/helpers/recipes are added, moved or removed. Shared edges
belong in the common declaration only when consumed by all compile paths. Keep
noncompile-only modules physically separate. Run the component input/graph tests
on both architectures, require exact affected-stage sets, and mutate every new
production location in all four downstream scopes. A new undeclared import must
fail even if a recipe edit already changed today's key.

Manifest schema 2 and `component-manifest-v2` deliberately migrate all component
identities once. Existing Actions key format, exact inner-scope validation,
effective compiler fingerprint, same-run compiler binding, attribution and the
fixed fallback whitelist/expiry remain unchanged. There is no old-key
compatibility assertion, prefix restore, cache deletion/re-save or priming.
To reproduce the base/current comparison without builds or cache access:

```sh
python3 scripts/compare-component-inputs.py \
  --base 6e09074a425c91748e7fc39c12010ce2d8018837 \
  --output /tmp/component-input-comparison
PYTHONPATH=.:tests python3 -m unittest test_component_inputs test_component_graph
```

The comparison extracts an immutable Git archive into a temporary directory
(no branch/worktree), fixes the effective fingerprint to `sha256:` followed by
64 `b` characters and kernel's predecessor to 64 `a` characters, and emits full
base/current manifests/scopes plus added/removed/changed input lists and keys.
Schema migration is separate from postmigration mutation tests: the latter
also derive kernel's predecessor from VPP input identity and a fixed synthetic
payload identity to prove propagation. These offline records do not demonstrate
hosted cache reuse. Full compiler/image/package/guest builds and actual hosted
cross-run HIT remain **NOT_RUN** for issue #30. Required Test CI and independent
PR reviews are publication gates owned by the publishing workflow.

The compiler BuildKit namespace uses `toolchain_identity()` rather than producer
COPY content. BuildKit still receives the complete actual current context and
rebuilds changed producer layers. This namespace is separate from exact same-run
compiler image transfer verification. After verified compiler load, Actions runs
the exact loaded image with `--read-only --network none` to compute the outer
stage key. It calls the same `build_scope()` and `compiler_fingerprint()` as the
actual stage. The immutable key is
`shirube-stage-v2-<stage>-<architecture>-<inner input_id>`; the fingerprint is
part of that input ID, and is **not** the Docker image config ID. A changed real
compiler fingerprint therefore gets a new outer key, which can be saved and
restored on the following run instead of repeatedly missing inside an immutable
outer hit. No fallback prefix keys are used.

### Automatic main cache population and optional validation

The existing eligible merged Release populates missing cache-v2 entries during
its normal build; no separate full-build primer is required. Optional manual
**Validate staged candidate without publishing** validation on main must use the
original repository and the dispatch's exact workflow SHA. Leave `source-ref`
empty for that SHA; an arbitrary source on main is rejected before repository
Python. Keep the Stream-compatible version and intentional cold/warm selection.
No additional full build or workflow dispatch was run for this change, and the
ongoing Release was left untouched.

For actual post-merge evidence, record the run's exact source SHA, ref,
architecture, outer restore/save results, inner `resources.json` cache status,
input ID/fingerprint and transfer receipts. Compare unchanged relevant inputs
on a later warm run. Cold skips restores and attempts saves; warm also saves
successful missing-key stages. Actual outer restores and inner HITs are needed
to establish reuse. Saves can fail and entries can be evicted. Kernel builds/signs
freshly, and source/license, ISO, clearance and guest gates still run.
Actual runner saves and the next warm hit remain **NOT_RUN**.
Hosted acceptance of the changed Release/validation callers is also **NOT_RUN**;
local fixtures and lint do not run GitHub's parser. Inspect startup and the
expected write-capable cache-mode warning on the first ordinary post-merge Release.

既存の適格なマージ後Releaseが通常ビルド中にcache-v2を補充するため、別の全工程ビルドは不要です。
任意の手動main検証では元リポジトリを使い、`source-ref`を空にしてdispatchの正確なSHAを選びます。
Stream互換versionとcold/warmの選択は維持します。
実際の保存と再利用の証拠には、runのSHA、ref、architecture、外側のrestore/save結果、
内側のHITと入力ID、fingerprint、transfer receiptを記録します。
この変更では追加ビルドやdispatchを行わず、進行中のReleaseも操作していません。
変更したRelease・検証callerのGitHub上での受理も**NOT_RUN**です。
ローカルfixtureとlintはGitHub側のパーサーを実行しません。
最初の通常のマージ後Releaseで、起動とwrite指定に伴う警告を確認します。

Evidence for issue #17 is offline and synthetic: the mutation matrix covers
component locks/code, shared contracts, missing/deleted inputs, architecture and
host/container aliases. A real stage-dispatch/transfer fixture mocks compilation
and installed-environment observation, models immutable outer save/restore, and
checks cold MISS, warm HIT, corrupt MISS_INVALID, changed-fingerprint save and
subsequent HIT, current commit rebinding and original content provenance. Workflow
tests execute the trusted admission code and resolver guard, and cover event/ref/
exact-commit/cache-mode/success/hit combinations. Existing archive/security and
downstream-gate tests remain required. These tests establish no actual hosted
cache hit, native speedup or current native acceptance. Actual runner saves and
the next warm hit are explicitly **NOT_RUN**. No workflow was triggered and the
ongoing release was left untouched.

### Bounded original Release artifact fallback

Until **2026-11-19T05:50:46Z**, an admitted normal closed/merged-main Release
may fill a warm outer miss from fixed run `43164071342`, attempt `1`, in
`michibiki-io/shirube-os`. `scripts/release-stage-import.json` pins ten successful
unsigned native/vpp/kea/salt/runtime jobs and artifact IDs for amd64/arm64.
The original source is PR #21's merge `4498c6104b65dfae37be1e5ebe8d78c27100884b`,
version `v2026.03.8`. The API head `42f6706d21f7bf306e9d8bcbf7af968242763cdf`
and `feature/#20` describe the PR head, not the payload source. The importer
checks the merged/closed main PR, repository IDs, Release workflow/event/path,
run attempt, main reusable-workflow SHA, individual successful job and exact
artifact ownership/digest/size/expiry. Overall run success is not required:
a later clearance failure cannot erase a successful unsigned stage, and these
artifacts grant no clearance or guest acceptance.

The verified same-run compiler computes the current exact outer key first.
Before any network request the host recomputes scope using the pinned actual
compiler fingerprint and current checkout and requires both pinned input ID
and current outer-key equality. Host dpkg is never used. PR, fork-repository,
feature/manual validation, cold mode, exact outer hits, incompatible scope and
expired windows never import. No separate validation dispatch or migration job
is needed. Old compiler docker-save artifacts are not BuildKit caches and are
never converted or imported. Kernel, signing, integration, ISO, clearance and
guests always follow the existing fresh path.

During a real import, the host bounds time (600 seconds), download bytes,
metadata, ZIP expansion and scratch storage, requires free disk margin, and
verifies the complete pinned ZIP hash, exactly two regular unique stored files,
original tar hash/size, receipt/metadata equality and full inventory with the
existing unpack validator. Only the verified original pair is atomically
installed at `<input_id>/transfer.tar` and `transfer.json`, without replacement.
Import scratch sits beside the stage-cache on the same filesystem, outside the
cache-save scope, so interrupted cleanup cannot save scratch with the verified pair.
The stage independently validates again, repacks current source/version and
preserves original `content_source_commit`; the missing-key save retains the
original cache bytes. Missing, expired, 403/404, timeout, wrong metadata or
corruption emits a safe skip warning, removes import scratch and falls back to
source compilation. A corrupt exact outer hit is never imported over: existing
`MISS_INVALID` compilation runs, but the unchanged save condition cannot replace
that immutable hosted key.

The ten unsigned transfers total **6,268,180,480 bytes**, before cache compression,
in addition to compiler caches. Quota and eviction can prevent durable reuse.
Per import, free disk must cover the ZIP, tar, unpacked payload, metadata and
512 MiB margin; scratch is cleaned on failure and success. No cache deletion or
capacity purchase is performed. `GH_TOKEN` exists only in the five host import
steps. Release's build caller adds `actions: read`; those jobs inherit caller
permissions, while compiler/admission and other jobs explicitly keep
`contents: read`. PR/manual callers retain their existing contents-only grants.

Small mocked fixtures cover integrity and the real stage cache HIT/repack path.
Historical metric scope equality is a compatibility observation, not full artifact
verification. Actual full artifact verification, hosted import/save/later HIT,
GitHub caller acceptance and next-run compiler fingerprint equality remain
**NOT_RUN**. The source Release is untouched; no extra build is started.

**2026-11-19T05:50:46Z**まで、通常のマージ済みmain Releaseはwarmの外側miss時だけ、
固定run `43164071342`（attempt `1`）の署名前成果物を取り込めます。
元ソースはPR #21のmerge `4498c6104b65dfae37be1e5ebe8d78c27100884b`、versionは`v2026.03.8`です。
APIのhead SHAと`feature/#20`はPR headを表します。
固定manifestで10件の成功jobとartifactを指定し、repository、workflow、event、attempt、
mainのreusable SHA、PRのmerge関係、job成功、artifactの所有者とhash、サイズ、期限を照合します。
後続のclearanceが失敗しても成功した署名前stageは対象ですが、Release合格の根拠にはしません。

同一runの検証済みcompilerが出した外側keyと、現checkoutと固定fingerprintから再計算したscopeを、通信前に照合します。
PR、fork repository、featureや手動検証、cold、外側exact hit、scope不一致、期限後は取得しません。
別の検証dispatchは不要です。
古いcompilerのdocker-saveはBuildKit cacheへ変換せず、kernel、署名、integration、ISO、clearance、guestも取り込みません。

実際の取り込み時に、時間、取得量、metadata、展開量、一時ディスクを制限し、ZIP全体と2つの通常ファイル、
元tarのhashとサイズ、receiptとmetadataの完全一致、全inventoryを既存validatorで検証します。
検証済みの原本2ファイルだけをatomicに配置し、既存entryを上書きしません。
取り込み用の一時領域はstage-cacheと同じfilesystemの親ディレクトリ直下に置き、削除が中断されても検証済みの原本2ファイルと一緒にcacheへ保存されないようにします。
stageが独立して再検証し、現commitとversionへ再梱包しても`content_source_commit`は元のままです。
欠落、失効、403/404、timeout、不正なmetadataや破損では警告を出し、一時領域を片付けて通常コンパイルへ戻ります。
外側exact hitの破損は既存の`MISS_INVALID`で再ビルドしますが、不変keyのsave条件は変更しません。

署名前transferの合計は**6,268,180,480 bytes**で、別にcompiler cacheも必要です。
quotaやevictionで保存と保持が制限される場合があります。
各取り込みではZIP、tar、展開payload、metadata、512 MiBの余裕を確保します。
cache削除や容量購入は行いません。
tokenは5つのhost stepだけに渡し、Release callerの`actions: read`を継承させます。
他のjobとPR、手動callerは既存の`contents: read`を維持します。

小さなmock fixtureで実stageのHITと来歴の再梱包を検証します。
過去10scopeの一致は互換性の観測であり、実artifact全体の検証ではありません。
実artifact検証、hosted import/save/後続HIT、GitHubでのcaller受理、次のcompiler fingerprint一致は**NOT_RUN**です。
元Releaseは操作せず、追加ビルドも開始しません。

## ISO, clearance and guest transfer retirement and builder entrypoint

Issue [#15](https://github.com/michibiki-io/shirube-os/issues/15) adds two explicit
opt-ins for disposable, task-owned downloads. `producer.compiler_artifact load
--discard-archive` removes only `compiler.tar`, after provenance, hash/size and
archive checks, Docker load and final image identity/platform inspection succeed.
`compiler.json` remains; default loading retains the tar. Export rejects this
flag before checking identity or creating outputs. Load progress stays on stderr;
stdout contains only the successful validated `imageid=...` assignment.

`run-native-stage.py --discard-input-archives` and the corresponding staged CLI
flags accept only ISO, clearance and guests (extended by Issue
[#20](https://github.com/michibiki-io/shirube-os/issues/20)). Supplying them asserts
that the input directory is dedicated to this task and its downloaded tars may be
consumed. The launcher rejects broad
host roots, symlinked input paths and overlap with output/cache/build paths before
creating outputs or starting Docker. Only this opt-in mounts `/inputs` writable;
all default input mounts remain read-only. The guest launcher forwards the flag
through `run-release-container.py` to the actual staged command. Before creating
the private Fixture, that wrapper rejects retirement without staged inputs and
rejects broad roots, symlink/parent aliases and overlap in either direction with
output, private fixture state, project source, cached libraries or build paths.
Project source and the fixture certificate remain read-only; fixture credentials
travel only through stdin. Existing fixture ownership, isolation and cleanup are
unchanged. After
`restore_inputs` verifies the entire expected cohort, including dependency
ancestry, metrics copy its hashes/sizes, and the consumer scope is calculated,
ISO retires only the seven expected predecessor `.tar` files; clearance retires
only `iso.tar`; guests retire only `clearance.tar`. ISO/clearance retirement
precedes the unchanged **75 GiB** workspace gate. Guest retirement precedes
clearance proof/kernel verification and the unchanged **56 GiB** gate before
fresh disks. JSON
receipts and unpacked payloads remain, including clearance's ISO bytes,
`assembly.json`, `iso-binding.json`, public signing certificate, DEBs, corresponding
sources and build material. Compiler retirement preserves the loaded image.
Earlier validation failures delete no tars; any retirement error aborts the stage
and export (already deleted tars are not rolled back). The ISO, clearance and guest jobs
enable both flags for AMD64 and ARM64 in their existing two run steps. Shared
caches, Docker objects and durable Actions artifacts are untouched.

A later capacity or clearance failure can still occur after successful retirement.
The **75 GiB** gate remains mandatory, as does the separate **56 GiB** guest gate.
The original same-run durable artifacts retain their provenance and retention and
remain the recovery source; this opt-in consumes only task-local duplicates.
In Release `43136908797` at `208705dabd7569a905a661b063e503429ffcc758`, both ISO
stages passed, but AMD64 clearance stopped at **69.0 GiB** before its body ran.
AMD64 `iso.tar` measured **5,598,208,000 bytes** (about 5.214 GiB), with sampled
free space **74.487 → 69.031 GiB**. That tar alone would not cover the 75 GiB
shortfall. The same run's AMD64 compiler artifact **13908471514** records
`compiler.tar` at **3,136,224,256 bytes** in `compiler.json` (`archive_bytes`);
the ZIP central directory independently reports the same logical file size.
The two transfer files total **8,734,432,256 bytes** (about 8.135 GiB). Adding
that logical size to the sampled free space gives about **77.166 GiB**, above
the gate, but is not a measurement or guarantee of actual filesystem recovery.
Only 66,114 bytes of bounded ZIP metadata ranges were read for this follow-up;
the manifest length and ZIP CRC were verified, not the complete tar hash.
ARM64 clearance succeeded and produced `clearance.tar` of
**8,290,990,080 bytes** (about 7.722 GiB), with sampled free space
**95.345 → 69.617 GiB** (the observed minimum). These are 2-second
whole-filesystem samples, not exact stage peaks or measurements of recovered
capacity. The original Release finished in failure due to AMD64 admission;
guests and publish were skipped. No guest capacity failure was observed.
Source/license success does not establish guest or release acceptance.

Publish previously retained three complete two-architecture asset sets. For a
draft, it now rejects an existing public destination before any download or
remote mutation, and checks again immediately before moving. After
aggregate metadata upload succeeds, it now retires only its internally generated
temporary candidate tree, then freshly downloads and fully validates remote
assets. It explicitly rejects an existing public destination (including a dangling
symlink) and moves the verified tree there. Same-filesystem rename avoids a third
copy; cross-filesystem copy-then-delete can transiently hold two sets. Retirement,
download, validation or move failures block workflow publication; caller-owned
directories are never removed. A failed cross-filesystem move may leave partial
public output and must be resolved by the caller. Staging is unchanged, and
promote still holds public assets plus a freshly downloaded, verified remote set.
Ownership, supersession, published no-op verification, aggregate/hash checks,
artifact ID/digest requirements and artifact-upload-before-promotion ordering
remain mandatory. VM working disks, conversions and overlays are unchanged.
Actual final release size/capacity and hosted guest acceptance are **NOT_RUN**;
the remaining two-set bound is not a capacity guarantee.

Regular-file transfer still materializes confined source aliases. ISO assembly
therefore patches and invokes `scripts/image-build/build-vyos-image`, with the
builder root as cwd, so Python can import its adjacent modules. The same canonical
file receives Shirube branding and ARM64 `ttyAMA0`; AMD64 retains `ttyS0`.
Notices, build environment/arguments and all provenance, source/license,
signature, guest-storage and publication gates remain in force.

Focused synthetic tests cover compiler retention/failures and real CLI output,
both architectures' ISO/clearance/guest cohort retention, metadata/payload preservation,
corrupt inventory/attribution/metadata rejection, metrics/scope-before-retirement,
cleanup and remaining-capacity failures, launcher path/mount boundaries, the exact
ISO+clearance+guest workflow opt-in set, and real
`files`/`copy_selected` → pack/unpack → canonical-script imports. These checks do
not build packages/images or establish release acceptance. The historical ISO
recovery in Issue #15 observed an artifact ZIP total of approximately **7.96 GiB**;
that includes overhead and is not a measurement of reclaimed clearance storage
or proof that its disk gate passes. Actual acceptance on
both architectures remains pending for the next eligible merged revision and its
complete hosted pipeline; rerunning the failed old revision cannot validate this
correction.

## Fixed snapshot validity during ISO assembly

Issue [#19](https://github.com/michibiki-io/shirube-os/issues/19) addresses expired
snapshot metadata independently of download retries. ISO-only preparation in
`containers/native/image.py:assemble` configures `producer/livebuild_sources.py`
alongside debootstrap transport preparation, before the canonical builder runs
for AMD64 and ARM64. It discovers the installed `sourcelist.sh` using
`dpkg-query --listfiles live-build`, verifies the exact official 20230502 file
SHA-256 and unique post-generation anchor, and adds one attributed hook while
preserving upstream notices. Missing/ambiguous paths, changed implementations,
repeated setup and transformation failures stop assembly.

The hook processes `PARENT_LIST_FILE` after each generation and before source
entries are optionally commented. This covers bootstrap, chroot install passes
(including source), and binary/remove regeneration before subsequent APT updates.
Only complete `deb`/`deb-src` URI matches to the lock's `debian_snapshot` and
`debian_security_snapshot`, plus their existing HTTP scheme equivalents, receive
`check-valid-until=no`. Suites, components, comments and nonconflicting options
are preserved. Unrelated mirrors, local repositories, other dates, hosts and URI
prefix matches are unchanged. Correct options are idempotent; malformed or
conflicting targeted entries fail before any rewritten content is saved.

Final-image immutable snapshot entries retain this source-local exception.
There is no global APT validity override or authentication bypass: signed
metadata and package hashes remain verified. Snapshot URLs/dates, canonical
builder and branding/console choices, transfer retirement, same-run provenance,
source/legal material, storage, guest and publication gates remain unchanged.
No workflow or cache policy changes are part of this correction.

Focused synthetic tests in `tests/test_livebuild_sources.py` cover parser and
hook guards, failure propagation and regeneration. A bounded offline smoke check
also passed against the already-downloaded
[official live-build 20230502 source](https://deb.debian.org/debian/pool/main/l/live-build/live-build_20230502.tar.xz),
`functions/sourcelist.sh` SHA-256
`3b22c35af39430f903c6288ef822bd141d6b07accba2ea6413eface6f5cb53da`:
56 regenerations followed by a checking APT stub across both architecture
settings, HTTP/HTTPS and source-archive settings, plus actual-hook failure
propagation. The upstream implementation is not vendored into the test suite.
These checks perform no network access, package/ISO build or software install;
they do not execute real APT or the complete archive-stage scripts. Actual
hosted Release acceptance on both architectures remains **NOT_RUN**, requiring
a future eligible merged revision and the complete existing pipeline.

## One ISO, one clearance, fresh guests

Integration moves the hidden NetBird offline build out of ISO assembly.
`assemble(..., prepared=...)` consumes the prebuilt DEBs and public certificate;
it does not call extras or integration compilation. Clearance checks the actual
complete ISO, retains its hash, generates complete source/license evidence and
physically restores that source set. Unresolved permission is an error before
any guest work. The downstream guest job verifies the clearance transfer and
same ISO hash, commit/version and source assets. It uses the retained task-built
kernel for its libguestfs appliance. It never calls full release assembly again.

`complete_candidate` retains the existing fresh installation, sanitizer,
source-equivalent distribution base, fresh firmware, QCOW2/VMDK conversions,
administrator continuity, per-instance identity and native NetBird
configuration/enrollment/save/reboot/removal tests. It creates the final generation
bindings and validates the complete manifest, including source/license/security
consumption integrity. Existing test disks are not distribution bases. QEMU TCG
is explicit for both hosted architectures; there is no accelerator fallback.
KVM host-device observations are not proof of a usable virtualization mode.

Only release `resolve` reserves a Stream-aligned version/draft. Only the separate
credentialed `stage` and final `publish` jobs can mutate that draft. Compiler jobs
have read-only contents permissions and receive no publication credentials.
Publication requires both architecture candidates and every existing release
check, then verified Actions upload before promotion. Commit-based Stream
version/reservation/supersession decisions remain in `producer.actions/policy`.

## Intentional validation and failure recovery

[validate-staged.yml](../.github/workflows/validate-staged.yml) supports explicit
pre-merge validation through PR label events: adding `validate-staged` selects
`cold`; adding `validate-staged-warm` selects `warm`. Each runs the exact PR head
with read-only contents permissions, no inherited secrets and no publication.
The label being added selects the mode even if both labels are already present.
Before this workflow exists on the default branch, use these labels for cold/warm
validation. Once it exists on the default branch, `workflow_dispatch` also accepts
a public `source-ref`, fixed Stream-compatible `version`, and `cold` or `warm`
(default `cold`). It resolves the ref once to a full commit and does not reserve
a version/draft. Ordinary push/synchronize events and unrelated label events do
not trigger a full build. Ordinary PR unit
and capability checks remain separate. No test path calls release staging,
promotion or OCI deployment.

A failed prerequisite prevents dependent jobs and final staging/publication.
For a same-run failed-job rerun, completed artifacts keep stable stage/architecture
names; every consumption revalidates the exact resolved commit/version and scope.
Successful reruns overwrite only that stage's same-run artifact. Expired/missing
required artifacts fail; rerun the owning prerequisite rather than silently using
an older release. A fresh dispatch resolves its own commit/version; cross-run
candidate substitution is not a recovery mechanism. Existing compiler artifacts
remain bound to their original source commit/run and cannot establish acceptance
of this fix. Rerunning an old run uses its old workflow/code and does not apply
this fix. Do not relabel or substitute compiler artifacts across runs.

The implementation tests use synthetic archives/files and a mocked compiler.
They exercise cache miss/hit/current-attribution reuse/corrupt-cache rebuild,
missing/extra/modified/link members, cross-architecture/commit/input mismatches,
source/SDK selections, dependency barriers and license-failure admission. They do
not prove hosted compilation, runtime capability, cache performance or release
acceptance. Do not dispatch new full builds just to repeat these tests.

## Focused implementation verification

[Exact command and changed-code hashes](evidence/diagnostic-issue3-focused-staging-tests.json):
The initial implementation passed 132 synthetic unit/failure-path tests with zero failures.
Changed Python syntax, both native template variants, relevant shell syntax,
workflow YAML/security/dependency contracts, concurrency/queue validation and
`git diff --check` passed.
The subsequent compiler-handoff/cache refinement passed 34 focused synthetic
tests (zero failures), including both architectures, missing/extra/corrupt
artifacts, manifest/config/platform mismatches, producer-only image builds,
cold restore suppression and cold-to-warm cache seeding. Its exact command and
code hashes are recorded in the same receipt. Python and workflow shell syntax
and diff checks also passed. No image/package build, full test discovery,
installation or additional license scan was run.

The receipt's `final_verification` records **36 final synthetic tests PASS**
(zero failures), **actionlint 1.7.12 PASS** on all workflows with only the
unsupported `concurrency.queue` diagnostic excluded, separate concurrency/queue
validation PASS, and `git diff --check` PASS. The binary was verified against
the repository-pinned official release archive. This supersedes the earlier
actionlint-unavailable status; these remain local synthetic checks.

The subsequent restoration-fixture correction passed **13 focused tests**.
Historical exact-head [Test run 43036745203](https://github.com/michibiki-io/shirube-os/actions/runs/43036745203)
tested revision `14aedee`: `test`, `Native hosted capability (amd64)`, and
`Native hosted capability (arm64)` all concluded **SUCCESS**. That tested revision
predates the readiness edits to the validation triggers and this document;
it does not establish CI success for those edits or any later head. The hosted
[Codex review of `14aedee`](https://github.com/michibiki-io/shirube-os/pull/10#pullrequestreview-5860176259)
completed with one accepted P1 concerning I2util revision evidence across the
Git-free source handoff. Review completion of a later head is not implied.
None of these results establish real hosted staged cold/warm acceptance,
speedup, or source/license clearance. CI for the readiness edits, external
review completion and staged hosted acceptance remain pending.

## All nine acceptance criteria and remaining gaps

| AC | Implementation / focused verification | Remaining acceptance evidence |
| --- | --- | --- |
| 1 | Graph, fixed native labels, explicit sub-360-minute deadlines, historical timings above; workflow contract tests | New-job complete wall/resource/transfer evidence and sufficient measured headroom, compiler peaks, true disk peaks are NOT_RUN. Advertised capacity does not satisfy current storage gates. |
| 2 | Independent heavy tranches; native dependency closure stays ordered in a private workspace; SDK → private Kernel/module signing job; synthetic graph/selector tests | Actual cold compilation, memory/storage feasibility and concurrent execution on both labels are NOT_RUN. |
| 3 | Scope/attribution/inventory/hash verification, regular archives, explicit source/SDK exports, no signing-key transfer/cache; adversarial archive and source-selection tests | Complete real producer inventories, SDK sufficiency and restored corresponding-source proof for the new path on both architectures are NOT_RUN. |
| 4 | Content scope omits commit; verified cache hit/miss and corrupt rebuild tested with mocked compiler; Actions cold/warm switches | Actual all-layer cold and reusable warm durations/reuse records on both architectures are NOT_RUN. |
| 5 | Frozen commit/version through one ISO and one clearance, fresh disk path and retained source/license/checksum generation | Complete current-head ISO + fresh QCOW2 + standalone VMDK/source/notice/checksum assets for both architectures are NOT_RUN; previous license mismatch is not declared resolved. |
| 6 | Existing complete guest lifecycle retained, explicit TCG, fresh firmware/base, source equivalence; failure-path tests | New-path hosted complete guest lifecycle/capability/time acceptance on both architectures is NOT_RUN. |
| 7 | Read-only intentional pre-merge entry point calls the same reusable workflow; permission/event tests | Successful complete hosted cold + warm run IDs, exact commits, hashes and per-job evidence are NOT_RUN. |
| 8 | needs barriers, exact predecessor inventory/commit/version checks, current source/license publication gates; synthetic missing/tampered/cache-rerun tests | Representative hosted failed-job/missing-artifact/rerun cases without publishing are NOT_RUN. |
| 9 | Existing commit-based Stream resolution, reservation/supersession and two-architecture final validators retained; compiler permissions narrowed; actions/workflow tests | Current-head CI and external reviews remain pending; OCI and staged hosted acceptance are NOT_RUN. |

実装と合成 fixture による検証を追加しました。新経路の hosted 完走、実際の cold/warm
比較、容量・時間の余裕、両アーキテクチャのゲスト検証は未実施です。
既存のライセンス不一致を解消したとは扱いません。
受け入れ条件を満たした、または高速化を実証したという判定はできません。

## Kernel compiler results before signing (Issue #36)

Kernel **stage transfers remain uncacheable**. The optional compiler-result
artifact is a separate boundary, not an addition to `CACHEABLE` or the existing
stage cache. `stage_artifacts.inventory` still rejects raw `.o`/`.ko`, private
keys and compiler trees. Every run starts from the authenticated source archive,
runs `mrproper`, removes archived keys and configures/patches a fresh tree. Kbuild
creates a new signing identity. Certificate objects, assembly (including embedded
certificate `.incbin` data), generated module metadata, host tools and all links
bypass the compiler cache. The final kernel link, in-tree module signing, all
external modules/accel-ppp work and current-certificate package/module audits run
normally in the same private key-owning container. No old signed package,
kernel/module, key or complete build tree is saved or restored.

The first candidate was ccache: its narrow C-to-object boundary fits the existing
recipe without restoring Kbuild state. The native compiler image installs Debian
ccache from the existing signed, pinned APT snapshot. A private configuration
forces preprocessor mode, content compiler checks, no direct/depend mode, no
sloppiness, no hardlinks and no remote backend; inherited `CCACHE_*` options are
removed. The source/config namespace hashes the fresh post-clean/post-patch tree,
architecture, installed package fingerprint and compiler/ccache/assembler/linker
and GCC backend contents. Each invocation additionally hashes **all** arguments.
The preprocessor sees current ordinary/generated headers; content changes cannot
be hidden by preserved mtimes. The wrapper excludes certificate compilation,
`.mod.c`, response files, plugins, specs, alternate backends and profiling inputs.
Unsupported ccache commands/directives fall back to the real compiler; in
particular ccache itself rejects `.incbin` reuse. `Module.symvers` and external
ABI processing are not restored: modpost, metadata compilation and external
modules run fresh. The optional fixture's preexisting ABI file also participates
in the source namespace. See the [ccache 4.7.5 manual](https://ccache.dev/manual/4.7.5.html)
for preprocessor hashing and unsupported-directive behavior.

Only the admitted canonical repository's merged-main **Release** path imports or
exports these artifacts. Main dispatch and PR validation remain fresh builds for
this feature. The kernel job inherits the caller's existing permission cap:
Release supplies `actions: read`; PR validation supplies only `contents: read`.
The host-only importer keeps the token out of Docker. It searches at most ten
successful Release runs and accepts an immutable upload only after checking the
canonical repository ID, Release event/path, successful completed run, exact
merged main PR/source commit, reusable workflow SHA/ref, matching successful
kernel job/attempt and upload timing, artifact ownership/expiry and GitHub's
artifact ZIP SHA-256. Then it validates the receipt, archive length/digest and
regular ZIP members. A matching internal digest alone proves neither producer
identity nor compiler correctness. Trust includes GitHub's API/artifact service,
the approved main workflow/source, signed compiler inputs and trusted main
maintainers; a malicious trusted compiler/writer is outside the integrity check's
claim. Changing these trust boundaries requires a separate design review.

The container receives only that admitted seed and checks architecture/toolchain,
every result member, size and digest before writing any cached byte. Foreign,
corrupt, unsafe, missing, expired or unavailable input causes fresh compilation;
there is no fallback to a shared Actions cache or another writer. The host's
`--kernel-compile-cache` option is an explicit trusted-operator boundary for local
use, not an authenticity checker for arbitrary local files. Do not supply an
untrusted local seed. Unexpected local export state fails the build closed.

The dedicated store has ccache eviction targets of 1 GiB and 45,000 files. Export/import enforce a
2 GiB tar limit, 64 MiB per result, 50,000 results and an 8 MiB manifest limit.
Export retains a bounded subset if count, individual-result size, manifest or
tar capacity would be exceeded; metrics report `skipped_capacity`. Capacity alone
cannot fail a correctly signed build. Unknown files, links and malformed state
still fail closed. Optional importer failures (including interrupted HTTP reads)
are redacted and fall back to a fresh build without a seed.
Only regular ccache result files are exported; configuration, logs, temporary
files, statistics, source trees and signing state are excluded. The uncompressed
ZIP must contain exactly `export.tar` and `provenance.json`; the optional artifact
is retained for seven days. Artifact/transfer preparation temporarily needs both
ZIP and tar space (in addition to the private store), and eviction/expiry or no
successful compatible producer means a miss. These optional compiler artifacts
do not replace required same-run stage handoffs. An upload occurs only after
the entire kernel stage, including external modules and package audits, succeeds;
an unsuccessful Release is never imported. No cache deletion or capacity purchase
is part of this change. Adding ccache changes the compiler fingerprint, so the
existing compiler/component caches incur a one-time identity miss.

`kernel-compiler-cache/metrics.json` records per-run ccache counters separately
from stage wall time: `RESTORED` means an admitted store was restored, **not** a
compiler hit. Use `preprocessed_cache_hit` and `cache_miss` counters. The kernel
group includes source/dependency acquisition, tools, external modules,
accel-ppp/sflow, signing, packaging and audits; its whole time is not compiler CPU
time. Fresh generated timestamps/headers may reduce hits, and namespace changes
conservatively invalidate reuse. The small compiler and adversarial fixtures
verify cold/warm reuse, current-certificate signatures after key rotation, source,
header/generated-header/config/option/compiler/architecture/ABI changes,
corruption, foreign/untrusted producers and bounded archives. Both architecture
**configuration paths** are covered; the native fixture is not an ARM64 kernel
build. Full kernel/ISO/guest builds, exact production recipe integration, hosted
artifact import/warm hits, capacity peaks and actual full-kernel speedup are
**NOT_RUN**. No measured speedup percentage is claimed.

### 日本語: 署名前の kernel compiler 結果再利用

kernel の stage cache 除外と `.o`/`.ko` の転送拒否は維持します。
通常の kernel C コンパイルだけを ccache の対象とし、証明書依存 object、assembly、
module metadata、tools、最終 link、全 module の署名・現行証明書による検証は毎回実行します。
鍵、鍵入り build tree、署名済み kernel/module/package は保存しません。
外部 module と accel-ppp は従来どおりビルドします。

共有 cache の digest を信頼性の証明には使いません。
既存 admission を通った canonical repository の merged-main Release だけが入出力し、
host 側で run・PR・workflow・成功 job・artifact の由来と digest を独立に検証します。
token は build container に渡しません。
PR validation と main dispatch はこの機能では常に fresh build です。
不正・異種・非信頼・期限切れ・取得不能の入力を使わず、通常コンパイルへ戻します。
ローカル用の専用 cache 引数は、trusted operator が検証済みの入力を渡す境界です。

content で compiler・設定・source・header・生成 header・引数を識別し、
direct/depend mode、sloppiness、時刻や compiler 名だけの identity は使いません。
保存対象は compiler 結果だけです。ccache の目標上限は 1 GiB と45,000ファイル、tar は 2 GiB、
1結果は 64 MiB、結果数は 50,000、manifest は 8 MiB、artifact 保持は7日です。
一時 ZIP/tar の追加容量が必要で、eviction や期限切れは正常な miss になります。
容量を超える結果は export から除外して統計に記録し、正常に署名した build は継続します。
未知ファイルと link は引き続き拒否します。
HTTP 読み取り中の切断を含む任意 import の障害は、内容を公開せず fresh build に戻します。
compiler fingerprint の変更により既存 cache は一度 miss します。

小さな実 compiler と不正入力 fixture で検証します。
ARM64 は設定経路の検証であり、実 ARM64 kernel の生成ではありません。
full kernel/ISO/guest build、production recipe の実行、hosted warm hit、容量ピーク、
実高速化率は **NOT_RUN** です。group 全時間を compiler 時間と見なさず、
restore 状態と実 compiler hit/miss の統計も区別します。
