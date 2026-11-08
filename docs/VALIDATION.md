# Implementation and validation status

This is an incomplete implementation of issue #1, not a release acceptance report.
No Shirube OS binary is cleared for distribution. Static fixtures never substitute
for a source build, disk boot or actual access test.

The original issue body was fetched with all ten comments on 2026-11-08. Its SHA256
was `50285b1247cfd070536260408efc0f6765de0b1afde00604d7db7b4da27d7546`, matching
the historical completed plan review:
https://github.com/michibiki-io/shirube-os/issues/1#issuecomment-6709861384.
The final Claude issue-plan review is historical plan evidence, not a PR review:
https://github.com/michibiki-io/shirube-os/issues/1#issuecomment-6709846136.

The user subsequently selected Stream-aligned versions (`v2026.03.0`,
`v2026.03.1`, then `v2026.04.0`). The updated body was fetched with all eleven
comments and has SHA256
`9e230e81c26e9ccc2500b31f1ad77a408c95009e8820aaa2d0693a582e77bee3`.
The earlier plan approval does not apply to this changed body. The change record is
https://github.com/michibiki-io/shirube-os/issues/1#issuecomment-6715232251;
the changed criteria belong in the latest PR review after release readiness.

## Actual observations

| Stage | Result | Scope |
| --- | --- | --- |
| Source-verifier container build | PASS | Native AMD64, fixed Debian image/snapshot, Python/minisign |
| Official Stream archive acquisition | PASS | Exact public 2026.03 archive, SHA256 and minisign signature |
| Unit/failure-path tests | PASS | 63 tests; metadata/policy/source/NetBird/cleanup primitives, synthetic fixtures |
| VPP Python bootstrap | PASS | Five actual public PyPI inputs pinned by version/file/hash; Meson 0.57.2 and pyelftools 0.33 installed and executed from those inputs in a network-disabled namespace with pip cache disabled. The new pins are not retroactively applied to running older-head builds |
| Patched VPP/core source archives | PASS | 4,690 VPP and 2,533 core source members retained and hash-verified, excluding generated build/staging trees. Full preferred source/test code retained; six exact known public test-vector files independently recognized by origin/member/full-file SHA. Unknown or changed keys and unreviewed licenses still rejected. Full OS reconstruction remains NOT_RUN |
| FPM dependency pinning | PASS | Ten exact gem archives matched RubyGems-published hashes and installed locally without dependency resolution; FPM version checked |
| Private-key scanner regression | PASS | Native compiled ELF containing a key-format delimiter accepted; actual newly generated RSA private key rejected; chunk-boundary cases tested |
| NetBird native CLI schema/templates | PASS | Actual source-built core/integration packages, configuration daemon registration, merged native schema owner/management-url/disable lookups and setup-key node absence checked in a container; guest commit/save/reboot unverified |
| Local NetBird management fixture | PASS | Digest-pinned real server bootstrap and one-use key issuance on an isolated Docker network; no client enrollment or guest lifecycle claim |
| AMD64 container NetBird client | PASS | Real registration, management/signal connectivity, same peer identity after daemon/container restart, setup-key absence in client state/error log; guest configuration save/reboot and ARM64 remain NOT_RUN |
| Fixture connection for guest tests | PASS | Actual default-bridge plus internal-network connection, fixture TLS and fresh one-use key issuance; no guest runtime claim |
| Input-path fixture isolation | PASS | Exact fixture guard exercised in native privileged ARM64 and ordinary AMD64 containers; only a regular dummy file opened after host-device masking. Not a hardware/input-driver test |
| Hidden console input transport | PASS | Actual container PTY prompt/input, secret absence from shell history and nonzero command status propagation; installed-OS lifecycle remains NOT_RUN |
| NetBird installed-guest harness | NOT_RUN | Full release path now creates a real fixture and calls the harness on both disk-format clones; native set/commit/save and operational enrollment, disable/enable, reboot, removal and secret scans implemented; no completed Shirube OS disk is available yet |
| NetBird client directory-license scope | PASS | Actual pinned Go compiler/build tags selected 174 AMD64 internal client packages; source-file hashes mapped and no management/signal/relay/combined AGPL directory selected. Other dependencies and full redistribution clearance remain unresolved |
| NetBird component source rebuild | PASS | Exact source/SDK, h1-verified ZIPs for all 121 actual linked modules, pinned generated vendor declarations and full notices retained; these assets restored and rebuilt with fresh caches/network namespace disabled on AMD64; installed package mappings cover 121 modules plus standard library. Complete OS reconstruction and license clearance remain incomplete |
| HTTP API dependency environment | PASS | Own packaging with the same 26 dependency versions, exact source/wheel hashes, per-dependency notices, native extension imports and actual GraphQL execution; ARM64 and guest API service integration remain unverified |
| Python compilation / shell syntax | PASS | Current Python files, both recipe architecture renderings and producer scripts |
| Workflow validation | PASS | YAML parsing and actionlint v1.7.12; current release/Test workflows |
| Native AMD64 kernel tranche | PASS | Linux 6.6.128 image/headers/libc-dev/perf built; actual module signatures verified against the fresh matching certificate and runtime payloads audited; not an ISO/boot result |
| Module cryptography primitive | PASS | Real CMS signature, tampering/wrong-certificate rejection and private-directory removal in container; not a real kernel build |
| Supplied SVG conversion | PASS | Native-container rsvg conversion to 824×320 PNG and filesystem identity helper; not a guest boot |
| Source-built python3-vici package | PASS | AMD64 container, authenticated Stream sources; 5.9.11-2 all-architecture package; not an ISO |
| Source-built nftables package tranche | PASS | AMD64 libnftnl 1.2.6-2 and nftables 1.0.9-1, including documentation, after adding the missing XML/a2x toolchain; package audits passed |
| Sequential routing package build | PASS | libnftnl, nftables, libyang 3.13.6 and FRR 10.5.2 built/audited in one native runtime after removing stale generated dependency metapackages between jobs |
| Native core package tranche | PASS | Native configuration library, schema generation, upstream build-time tests and all generated runtime payload audits passed; optional fixed-key smoketest toolkit was not generated |
| OCaml switch source retention | PASS | Actual 44 concrete installed components, 27,183 original/prepared source members, exact installed metadata and upstream notices retained; cache content addresses verified and Git histories omitted. Offline OCaml reconstruction and component license audit remain NOT_RUN |
| Static I2util corresponding source | PASS | Actual 102 tracked source members, full upstream license and Debian copyright retained from the fixed public revision; generated binaries and Git history omitted. This closes a static-link source inventory gap, not the remaining license-clearance or full-reconstruction gates |
| Shared reconstruction inputs | PASS | Actual prepared Stream builder configuration/scripts and the fixed public VPP patch repository retained in one hashed archive; every source package receives member bindings. This does not prove full dependency closure or complete OS reconstruction |
| Runtime-extra notice/source helper | PARTIAL | Actual source ZIP/h1 retention and base-component builds passed. Per-module notices and installed binary/source bindings implemented; current complete Go/Kea/Podman probes remain in progress. Complete transitive license clearance and full ISO integration remain incomplete |
| Boot artwork source preparation | PASS | Both restricted boot PNG inputs replaced before compilation/source export; supplied SVG rendered onto a 640x480 canvas, visually inspected, theme contrast and dated provenance recorded; actual firmware UI remains unverified |
| Streaming source extraction | PASS | Authenticated outer archive and selected upstream build component; all 260 regular-file hashes matched the earlier extraction; late unsafe members remove private staging without exposing partial results |
| Native package-toolchain container | PASS | Native AMD64 toolchain image built after correcting package-name/snapshot dependency failures; package/ISO build success is not implied |
| Complete native AMD64 ISO | FAILED | Latest full attempt built core, kernel and VPP, then failed OWAMP linking because the freshly built I2util development package was not installed. Corrected base-component probe built/audited 19 packages and passed all 13 OWAMP tests with non-root execution. No ISO exists; the failed attempt also predates Stream-aligned versioning |
| Complete native ARM64 ISO | BUILDING | First 2 GiB/two-CPU native attempt passed routing/FRR and stopped at core test-fixture creation because privileged device population exposed a real input node. The corrected fixture uses a private nodev tmpfs before any open; exact guard probes on both architectures pass. Second native attempt passed core and is compiling the kernel. No ISO exists; no OOM has been observed so far. Its frozen source predates the subsequent runtime-extra fixes |
| Disk-format conversion primitive | PASS | Actual patterned 50 GiB sparse disk converted to standalone QCOW2 and streamOptimized VMDK; qemu-img checks and full content comparisons passed. Synthetic data only, no operating system or boot/OCI evidence |
| Installed QCOW2/VMDK | NOT_CREATED | Fresh ISO install, conversion and whole-disk sanitization unverified |
| Local unprovisioned/provisioned boots | NOT_RUN | Actual KVM/guest integration and access evidence missing |
| Live OCI, AMD64 and ARM64 | NOT_RUN | No cloud operation performed; outside this task's deployment scope |

The AMD64 environment has no usable KVM device. A native ARM64 environment with
usable KVM is now available; its limited free memory requires measured resource
bounds. Runtime availability is checked at invocation. Full disk/access
validation has not yet run on either environment.

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
| 9 | Tested merged-PR eligibility, Stream-aligned version/revision policy, immutable-set comparison | Real end-to-end build/recovery/publication verification of the wired orchestration |
| 10 | Manifest v1 schema/example, complete-set/evidence/hash/source gates and failure injection | Real coherent assets/evidence and staging/publication verification; OCI remains independent |
| 11 | Task-only mounts/work, runtime-only private signing keys, enrollment erasure and payload key checks | End-to-end source/binary/log/cache/whole-disk audits |
| 12 | Build/release/first-boot/NetBird/license docs and explicit limits | Update runnable reconstruction/recovery instructions after complete integration/validation |

Every AC remains open. The PR must remain a draft while these gaps exist. Green
static CI does not make it merge-ready. Merge and cloud deployment are not part
of this implementation run.

## Release workflow state

The release workflow is implemented and statically validated: merged-only base
commit selection, both native runner variables/defaults, Stream-aligned version selection and individual-commit
release notes, draft reservation, full candidate build invocation, per-architecture
staging, aggregate verification, Actions artifact upload and final promotion.
Published reruns take a verification-only path before any build.

This does **not** make publication ready. `license-audit.json` currently records
only the first individually audited components. Full source reconstruction and actual NetBird enrollment/save/reboot
evidence are still NOT_RUN and block the pipeline. The runtime installation,
repacking/journal/free-space sanitization and both-format boot code are not yet
validated on real native KVM runners. They must not be inferred from static CI.
