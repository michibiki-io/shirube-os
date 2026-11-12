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
and LIBRARY_PATH. Only signed DEBs, public certificate and selected key-free
source/build inputs leave the job. The SDK includes notices through the retained
VPP corresponding-source/patch material. No signing-key or compiler-state cache
exists. Package and ISO signature audits still use the matching public certificate.

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
Release-origin `pull_request_target` executions are restore-only, including
post/failure paths; Buildx uses `cache-binary: false`.
Only a `workflow_dispatch` on `refs/heads/main` in `michibiki-io/shirube-os`,
with resolved source exactly equal to `github.sha`, can write these shared
compiler/stage namespaces. The resolver checks the resolved SHA in inline shell
before repository Python. The reusable workflow independently admits the caller
in a job with no checkout or project imports; all build jobs depend on it.
Its trusted `cache-write` output controls every BuildKit export and stage save.
Merged `pull_request_target` releases must be closed, merged into main in the
caller repository, run on main, and pass the exact event merge SHA. Queued merge
SHAs need not equal the newer main tip. They remain restore-only, as do PR
validation and non-main dispatches. Unknown events, malformed commits, cache
modes other than cold/warm, and mismatched source/event data are rejected.
No `cache-mode: write`, extra credentials, security-setting changes or new
build scheduler are introduced.

Successful eligible stages save only after success when there was no exact
outer hit. Compiler BuildKit export is optional (`ignore-error=true`). Restore
attempts and optional exports do not establish verified reuse. Original content
attribution, exact inventory/hash/scope validation and all downstream gates
remain required; kernel signing and kernel state are never cached. Hosted warm
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

### Component identities and conservative shared inputs (issue #17)

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
Noncompile scope retains the existing broad inputs.

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

The full `build.py`, `producer/native_common.py`, shared contract modules, and
(for stages using it) `extras.py.in` are conservative recipe inputs. Edits to an unrelated function
in those shared files can still miss multiple component caches. This is deliberate;
function-text hashing could omit a transitive dependency. New build dependencies
must be declared and covered by mutation tests. The generic `go_notice()` helper
moved unchanged to `producer/go_notices.py`, shared by FPM and NetBird consumers,
so NetBird-specific code can change without losing runtime notice dependencies.
The `generate()` and `run()` helpers moved unchanged from `image.py` to
`producer/native_common.py`; `image.py` reexports them for existing consumers.
Compile stages import the shared helpers directly and no longer include
`image.py` in their manifests. NetBird lock/code (including guest packaging in
`image.py`), guest, cloud-init and GeoIP-only changes leave these compile
identities stable; integration/downstream identities remain broad and include
`image.py`.

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

### Main cache priming after merge (not run for this change)

The new v2 namespaces require an initial trusted main population. After merge,
a maintainer can use the existing **Validate staged candidate without publishing**
workflow on main, with a Stream-compatible version. Leave `source-ref` empty to
use that dispatch's exact workflow SHA; the resolver and reusable admission both
reject a main dispatch resolving to another commit. For example, these are
**post-merge operator commands, not executions recorded here**:

```sh
gh workflow run validate-staged.yml --ref main -f cache-mode=cold -f version=v2026.03.0
# After the first run completes, inspect compiler/stage cache-save logs and gates.
gh workflow run validate-staged.yml --ref main -f cache-mode=warm -f version=v2026.03.0
```

Record each run's exact `head_sha`, ref, architecture, outer restore/save results,
inner `resources.json` cache status/input ID/fingerprint and transfer receipts.
Use identical relevant component/toolchain inputs for the warm comparison.
The cold run skips restores and attempts saves; saves may fail or entries may
be evicted. The warm run must show actual outer restores **and inner HITs** to
establish reuse. Kernel must build/sign freshly in both runs, and source/license,
ISO, clearance and guest acceptance still run. Subsequent normal merged releases
read these default-branch entries without writing them. PR and non-main
validation cannot populate this shared namespace, and an arbitrary `source-ref`
on a main dispatch is rejected before project execution.

Evidence for issue #17 is offline and synthetic: the mutation matrix covers
component locks/code, shared contracts, missing/deleted inputs, architecture and
host/container aliases. A real stage-dispatch/transfer fixture mocks compilation
and installed-environment observation, models immutable outer save/restore, and
checks cold MISS, warm HIT, corrupt MISS_INVALID, changed-fingerprint save and
subsequent HIT, current commit rebinding and original content provenance. Workflow
tests execute the trusted admission code and resolver guard, and cover event/ref/
exact-commit/cache-mode/success/hit combinations. Existing archive/security and
downstream-gate tests remain required. These tests establish no actual hosted
cache hit, native speedup or current native acceptance. Main cold priming and
native warm verification are explicitly **NOT_RUN premerge**. No workflow was
triggered and the ongoing release was left untouched.

## ISO transfer retirement and builder entrypoint

Issue [#15](https://github.com/michibiki-io/shirube-os/issues/15) adds two explicit
opt-ins for disposable, task-owned downloads. `producer.compiler_artifact load
--discard-archive` removes only `compiler.tar`, after provenance, hash/size and
archive checks, Docker load and final image identity/platform inspection succeed.
`compiler.json` remains; default loading retains the tar. Export rejects this
flag before checking identity or creating outputs. Load progress stays on stderr;
stdout contains only the successful validated `imageid=...` assignment.

`run-native-stage.py --discard-input-archives` and the corresponding staged CLI
flag are ISO-only. Supplying them asserts that the input directory is dedicated
to this task and its downloaded tars may be consumed. The launcher rejects broad
host roots, symlinked input paths and overlap with output/cache/build paths before
creating outputs or starting Docker. Only this opt-in mounts `/inputs` writable;
all default mounts remain read-only and guest handling is unchanged. After
`restore_inputs` verifies the entire expected cohort, including dependency
ancestry, and metrics copy its hashes/sizes, ISO retires only the seven expected
predecessor `.tar` files before the unchanged **75 GiB** workspace gate. JSON
receipts and unpacked payloads remain. Earlier validation failures delete no tars;
any retirement error aborts assembly/export (already deleted tars are not rolled
back). The ISO job enables both flags for AMD64 and ARM64 in its existing two run
steps. Shared caches, Docker objects and durable Actions artifacts are untouched.

Regular-file transfer still materializes confined source aliases. ISO assembly
therefore patches and invokes `scripts/image-build/build-vyos-image`, with the
builder root as cwd, so Python can import its adjacent modules. The same canonical
file receives Shirube branding and ARM64 `ttyAMA0`; AMD64 retains `ttyS0`.
Notices, build environment/arguments and all provenance, source/license,
signature, guest-storage and publication gates remain in force.

Focused synthetic tests cover compiler retention/failures and real CLI output,
complete-cohort retention and metrics, launcher/workflow opt-ins, and real
`files`/`copy_selected` → pack/unpack → canonical-script imports. These checks do
not build packages/images or establish release acceptance. The observed artifact
ZIP total of approximately **7.96 GiB** includes overhead and is not a measurement
of reclaimed storage or proof that the disk gate passes. Actual acceptance on
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
