# Implementation and validation status

This is an incomplete implementation of issue #1, not a release acceptance report.
No Shirube OS binary is cleared for distribution. Static fixtures never substitute
for a source build, disk boot or actual access test.

The latest issue body was fetched with all ten comments on 2026-11-08. Its SHA256
was `50285b1247cfd070536260408efc0f6765de0b1afde00604d7db7b4da27d7546`, matching
the completed plan review:
https://github.com/michibiki-io/shirube-os/issues/1#issuecomment-6709861384.
The final Claude issue-plan review is historical plan evidence, not a PR review:
https://github.com/michibiki-io/shirube-os/issues/1#issuecomment-6709846136.

## Actual observations

| Stage | Result | Scope |
| --- | --- | --- |
| Source-verifier container build | PASS | Native AMD64, fixed Debian image/snapshot, Python/minisign |
| Official Stream archive acquisition | PASS | Exact public 2026.03 archive, SHA256 and minisign signature |
| Unit/failure-path tests | PASS | 41 tests; metadata/policy/source/NetBird/cleanup primitives, synthetic fixtures |
| FPM dependency pinning | PASS | Ten exact gem archives matched RubyGems-published hashes and installed locally without dependency resolution; FPM version checked |
| Private-key scanner regression | PASS | Native compiled ELF containing a key-format delimiter accepted; actual newly generated RSA private key rejected; chunk-boundary cases tested |
| NetBird native CLI schema/templates | PASS | Configuration and operational definitions compiled with authenticated Stream schemas/compilers in a container; guest commit/save/reboot unverified |
| Local NetBird management fixture | PASS | Digest-pinned real server bootstrap and one-use key issuance on an isolated Docker network; no client enrollment or guest lifecycle claim |
| AMD64 container NetBird client | PASS | Real registration, management/signal connectivity, same peer identity after daemon/container restart, setup-key absence in client state/error log; guest configuration save/reboot and ARM64 remain NOT_RUN |
| NetBird component source rebuild | PASS | Exact public source and pinned Go 1.26.10; prepared dependency sources rebuilt again with a fresh cache in a network-disabled AMD64 container; complete OS reconstruction and license clearance remain incomplete |
| HTTP API dependency environment | PASS | Own packaging with the same 26 dependency versions, exact source/wheel hashes, per-dependency notices, native extension imports and actual GraphQL execution; ARM64 and guest API service integration remain unverified |
| Python compilation / shell syntax | PASS | Current Python files, both recipe architecture renderings and producer scripts |
| Workflow validation | PASS | YAML parsing and actionlint v1.7.12; current release/Test workflows |
| Module cryptography primitive | PASS | Real CMS signature, tampering/wrong-certificate rejection and private-directory removal in container; not a real kernel build |
| Supplied SVG conversion | PASS | Native-container rsvg conversion to 824×320 PNG and filesystem identity helper; not a guest boot |
| Source-built python3-vici package | PASS | AMD64 container, authenticated Stream sources; 5.9.11-2 all-architecture package; not an ISO |
| Source-built nftables package tranche | PASS | AMD64 libnftnl 1.2.6-2 and nftables 1.0.9-1, including documentation, after adding the missing XML/a2x toolchain; package audits passed |
| Sequential routing package build | PASS | libnftnl, nftables, libyang 3.13.6 and FRR 10.5.2 built/audited in one native runtime after removing stale generated dependency metapackages between jobs |
| Runtime-extra notice/source helper | PARTIAL | Real FPM notice-retention fixture and actual Go source ZIP/h1 comparison passed; all real runtime-extra recipes still require their integrated build |
| Boot artwork source preparation | PASS | Both restricted boot PNG inputs replaced before compilation/source export; supplied SVG rendered onto a 640x480 canvas, visually inspected, theme contrast and dated provenance recorded; actual firmware UI remains unverified |
| Streaming source extraction | PASS | Authenticated outer archive and selected upstream build component; all 260 regular-file hashes matched the earlier extraction; late unsafe members remove private staging without exposing partial results |
| Native package-toolchain container | PASS | Native AMD64 toolchain image built after correcting package-name/snapshot dependency failures; package/ISO build success is not implied |
| Complete native AMD64 ISO | NOT_BUILT | Latest full attempt stopped at FRR dependency resolution; the corrected sequential routing tranche now passes; a complete ISO is still pending |
| Complete native ARM64 ISO | NOT_BUILT | Native ARM64 runner also unavailable in this execution environment |
| Installed QCOW2/VMDK | NOT_CREATED | Fresh ISO install, conversion and whole-disk sanitization unverified |
| Local unprovisioned/provisioned boots | NOT_RUN | Actual KVM/guest integration and access evidence missing |
| Live OCI, AMD64 and ARM64 | NOT_RUN | No cloud operation performed; outside this task's deployment scope |

No usable KVM device was observed. Runtime resource availability is checked again
at each invocation. These constrain runtime verification; they do
not explain away remaining implementation gaps.

## Acceptance criteria mapping

| AC | Implemented primitives | Remaining acceptance work |
| --- | --- | --- |
| 1 | Public source lock, authenticated acquisition, ephemeral signing-key API, safe source extraction, native checks | Complete fixed transitive dependency closure and integrated fresh-checkout validation |
| 2 | Native package recipes/config selection, ELF/EFI/kernel helpers, module-signature verification | Both actual full ISO builds/audits and verification of all runtime recipes |
| 3 | Supplied SVG, conversion/OS identity helper, attribution/license separation | Final-image branding/license audit and actual UI/installer verification |
| 4 | Non-secret service schema, controller, separate one-shot enrollment, failure-path tests | Actual both-architecture NetBird enrollment/lifecycle/save/reboot tests |
| 5 | Standalone 50 GiB conversion/integrity/equivalence helper | Actual installer, whole-disk cleanup and QCOW2/VMDK results |
| 6 | Offline logical cleanup, console enrollment implementation | Actual proof of guest integration, free-space/journal cleanup and fresh-firmware/access/DHCP/identity tests |
| 7 | Component archive/member integrity and inventory validators | Complete source/dependency coverage, exact component/license audit, real reconstruction |
| 8 | Both runner-variable/default/label policies and native checks | Actual supported runner/resource/virtualization evidence for the wired workflow |
| 9 | Tested merged-PR eligibility, commit-only semantic version policy, immutable-set comparison | Real end-to-end build/recovery/publication verification of the wired orchestration |
| 10 | Manifest v1 schema/example, complete-set/evidence/hash/source gates and failure injection | Real coherent assets/evidence and staging/publication verification; OCI remains independent |
| 11 | Task-only mounts/work, runtime-only private signing keys, enrollment erasure and payload key checks | End-to-end source/binary/log/cache/whole-disk audits |
| 12 | Build/release/first-boot/NetBird/license docs and explicit limits | Update runnable reconstruction/recovery instructions after complete integration/validation |

Every AC remains open. The PR must remain a draft while these gaps exist. Green
static CI does not make it merge-ready. Merge and cloud deployment are not part
of this implementation run.

## Release workflow state

The release workflow is implemented and statically validated: merged-only base
commit selection, both native runner variables/defaults, individual-commit
version/notes, draft reservation, full candidate build invocation, per-architecture
staging, aggregate verification, Actions artifact upload and final promotion.
Published reruns take a verification-only path before any build.

This does **not** make publication ready. `license-audit.json` currently records
only the first individually audited components. Full source reconstruction and actual NetBird enrollment/save/reboot
evidence are still NOT_RUN and block the pipeline. The runtime installation,
repacking/journal/free-space sanitization and both-format boot code are not yet
validated on real native KVM runners. They must not be inferred from static CI.
