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
| Unit/failure-path tests | PASS | Metadata/policy/source/NetBird/cleanup primitives and failure cases; exact-head Test CI recorded on the PR. Synthetic fixtures do not establish OS acceptance |
| VPP Python bootstrap | PASS | Five actual public PyPI inputs pinned by version/file/hash; Meson 0.57.2 and pyelftools 0.33 installed and executed from those inputs in a network-disabled namespace with pip cache disabled. The new pins are not retroactively applied to running older-head builds |
| Patched VPP/core source archives | PASS | 4,690 VPP and 2,533 core source members retained and hash-verified, excluding generated build/staging trees. Full preferred source/test code retained; six exact known public test-vector files independently recognized by origin/member/full-file SHA. Unknown or changed keys and unreviewed licenses still rejected. Full OS reconstruction remains NOT_RUN |
| VPP Python API offline packaging | PASS | Actual patched VPP API source built and installed in an isolated environment using only the fixed local wheel inputs with Docker networking disabled; full ARM64 VPP package retry is separate |
| Unsigned library artifact reuse | PASS | Nine jobs/45 DEBs reused by the active AMD64 and ARM64 builds. Separate restore checks retained unchanged DEB hashes after version/artwork/guest fixture changes and passed an actual native package consumer. Heavy runtime packages/kernel and cross-run Actions cache verification remain incomplete |
| Salt preferred source retention | PASS | Actual tracked Salt source (6671 members) and pinned public packaging-tool source (26 members) exported with member-hash verification; generated virtualenvs and compiled SDK archives excluded. Nineteen unchanged public test/documentation files recognized against signed Stream input. Complete dependency source closure/reconstruction remains NOT_RUN |
| Salt upstream runtime packages | PASS | The native AMD64 build generated eight packages with the expected Salt 3006.21+0.ge35fd3e and all 55 SDK distributions matched. An earlier copied-source probe produced the wrong Salt version and its original DEBs are not accepted. Exact public test fixtures are classified by source/hash; injected fresh private keys are rejected. Full SDK reconstruction, license clearance and guest services remain incomplete |
| Salt SDK fixed-input Python build | PASS | Native AMD64 component build with 77 fixed Python source inputs plus the architecture-specific PPBT build wheel and 48 Cargo crates; dependency/Salt compilation ran in a network-disabled namespace. All 55 installed versions and 425 pip/Relenv bootstrap Python files matched; cryptography/CFFI/ZeroMQ checks passed. SDK native libraries/compiler and whole OS were not rebuilt |
| Salt SDK source/legal retention | PARTIAL | 144 components/146 inputs and 144 combined legal records passed archive integrity checks against actual SDK inventory; full MPL/Apache/PHP supplements and embedded msgpack notices retained. Individual decisions cover 48 crates and 45 Python runtime/build distributions; 51 SDK components and full release remain uncleared. Previously verified downloads and inherited compiler inventory were used for this retention check; full reconstruction is NOT_RUN |
| Embedded module notices and signatures | PASS | Exact upstream OpenVPN DCO, NAT RTSP and NetFlow headers/README/CREDITS plus full GPL/exception texts retained without removing features or granting release clearance. Native module compilation, matching signatures and package audits passed in the same-cohort diagnostic recovery |
| sFlow parallel cJSON dependency | PASS | Unpatched standalone parallel shared-library build reproduced missing cJSON.o; explicit prerequisite fixed the link and native make/test plus actual JSON pointer access passed. All configured sFlow features remain enabled |
| Transient ISO download retry | PASS | Only wget network exit 4 retried, at most three attempts with unchanged URL/arguments and downstream signature/hash checks; recovery/exhaustion and non-network failure tests passed. Native debootstrap integration checked; this is not completed ISO evidence |
| FPM dependency pinning | PASS | Ten exact gem archives matched RubyGems-published hashes and installed locally without dependency resolution; FPM version checked |
| Private-key scanner regression | PASS | Native compiled ELF containing a key-format delimiter accepted; actual newly generated RSA private key rejected; chunk-boundary cases tested |
| NetBird native CLI schema/templates | PASS | Actual source-built core/integration packages, configuration daemon registration, merged native schema owner/management-url/disable lookups and setup-key node absence checked in a container; diagnostic guests on both architectures subsequently passed commit/save/reboot |
| Local NetBird management fixture | PASS | Digest-pinned real server bootstrap and one-use key issuance on an isolated Docker network; no client enrollment or guest lifecycle claim |
| AMD64 container NetBird client | PASS | Real registration, management/signal connectivity, same peer identity after daemon/container restart, setup-key absence in client state/error log; guest configuration save/reboot and ARM64 remain NOT_RUN |
| Fixture connection for guest tests | PASS | Actual default-bridge plus internal-network connection, fixture TLS and fresh one-use key issuance; no guest runtime claim |
| Input-path fixture isolation | PASS | Exact fixture guard exercised in native privileged ARM64 and ordinary AMD64 containers; only a regular dummy file opened after host-device masking. Not a hardware/input-driver test |
| Hidden console input transport | PASS | Actual container PTY prompt/input, secret absence from shell history and nonzero command status propagation; installed-OS lifecycle remains NOT_RUN |
| NetBird installed-guest harness | INCOMPLETE | ARM64 sanitized QCOW2 reached real native set/commit/save and rejected an unregistered key. The upstream operational shell returns zero for failed internal commands; the harness now additionally requires the controller failure response, then independently checks inactive/no-receipt state and secret erasure. Native ARM64 QCOW2 then passed real registration, management/signal connectivity, disable/enable, saved reboot identity, secret scans and removal/reboot inactivity. Diagnostic acceptance passed on both formats on AMD64/TCG and ARM64/KVM; clean-head acceptance remains incomplete |
| NetBird client directory-license scope | PASS | Actual pinned Go compiler/build tags selected 174 AMD64 internal client packages; source-file hashes mapped and no management/signal/relay/combined AGPL directory selected. Other dependencies and full redistribution clearance remain unresolved |
| NetBird component source rebuild | PASS | Exact source/SDK, h1-verified ZIPs for all 121 actual linked modules, pinned generated vendor declarations and full notices retained; these assets restored and rebuilt with fresh caches/network namespace disabled on AMD64; installed package mappings cover 121 modules plus standard library. Complete OS reconstruction and license clearance remain incomplete |
| HTTP API dependency environment | PASS | Own packaging with the same 26 dependency versions, exact source/wheel hashes, per-dependency notices, native extension imports and actual GraphQL execution; ARM64 and guest API service integration remain unverified |
| HTTP API Rust source retention | PASS, component scope | Actual AMD64 packaging retains pydantic-core/watchfiles parent lock closures: 156 registry and two fixed-Git package bindings, original archives and installed notices. Collector binding, native imports and GraphQL execution passed. Native-extension reconstruction, compiler/standard-library closure and full license clearance remain incomplete |
| HTTP API Rust native reconstruction | PASS, component scope | The repository reconstruction command rebuilt pydantic-core 2.27.1 and watchfiles 1.0.0 on native AMD64 and ARM64 with exact Rust 1.82.0 inputs. Compilation used disabled network namespaces; data validation and actual file-change events passed. Same recipe SHA and source/library hashes are recorded in the component receipt. Full OS reconstruction and release integration remain incomplete |
| HTTP API Rust standard-library source binding | PASS, component scope | Native AMD64/ARM64 packages retain the exact Rust 1.82.0 source archive and 3,583 original legal-file bindings, materializing 32 confined aliases. Installed extension bytes match their fixed wheels and compiler identity; notices regenerated independently match installed bytes. Old packages without the required source inventory are rejected. Full distribution license clearance and OS reconstruction remain incomplete |
| Python compilation / shell syntax | PASS | Current Python files, both recipe architecture renderings and producer scripts |
| Workflow validation | PASS | YAML parsing and actionlint v1.7.12; current release/Test workflows |
| Native ARM64 kernel tranche | PASS | Source-built kernel and matching module-signature audit passed in the earlier frozen full-build attempt; not a complete ISO or current-head acceptance |
| Native ARM64 VPP component | PASS | All ten packages generated/audited with upstream hs_apps/http_static, sample tools and TLS test header retained. Exact published fixture identity checked against pinned source. In a native Docker probe, VPP started, forwarded five ICMP packets between isolated namespaces with zero loss after neighbor discovery, and served Prometheus metrics through http_static. Not a whole guest OS or OCI test |
| Native AMD64 kernel tranche | PASS | Linux 6.6.128 image/headers/libc-dev/perf built; actual module signatures verified against the fresh matching certificate and runtime payloads audited; not an ISO/boot result |
| Generated kernel distributor metadata | IMPLEMENTED, fresh build NOT_RUN | The earlier diagnostic DEB uses a default build-account maintainer. Kernel build user/host and Debian maintainer inputs are now canonical distributor values; the producer rejects a noncanonical generated kernel maintainer without printing its raw value. A new kernel build must verify the change |
| Module cryptography primitive | PASS | Real CMS signature, tampering/wrong-certificate rejection and private-directory removal in container; not a real kernel build |
| Supplied SVG conversion | PASS | Native-container rsvg conversion to 824×320 PNG and filesystem identity helper; not a guest boot |
| Source-built python3-vici package | PASS | AMD64 container, authenticated Stream sources; 5.9.11-2 all-architecture package; not an ISO |
| Source-built nftables package tranche | PASS | AMD64 libnftnl 1.2.6-2 and nftables 1.0.9-1, including documentation, after adding the missing XML/a2x toolchain; package audits passed |
| Sequential routing package build | PASS | libnftnl, nftables, libyang 3.13.6 and FRR 10.5.2 built/audited in one native runtime after removing stale generated dependency metapackages between jobs |
| Native core package tranche | PASS | Native configuration library, schema generation, upstream build-time tests and all generated runtime payload audits passed; optional fixed-key smoketest toolkit was not generated |
| OCaml switch source retention | PASS | Actual 44 concrete installed components, 27,183 original/prepared source members, exact installed metadata and upstream notices retained; cache content addresses verified and Git histories omitted. Offline OCaml reconstruction and component license audit remain NOT_RUN |
| Diagnostic Debian source restoration | PASS | Actual ARM64 diagnostic inventory: 658 Debian binaries mapped to 460 exact source groups/998 original archive members from the fixed signed snapshot. All source trees restored offline and all 460 copyright files matched installed bytes; libxcb/libxcrypt notice generation reproduced from upstream rules. Named original legal-file discovery retained 1,497 file bindings. The 72 custom binary packages, complete OS binary reconstruction and full license decisions are outside this result |
| Static I2util corresponding source | PASS | Actual 102 tracked source members, full upstream license and Debian copyright retained from the fixed public revision; generated binaries and Git history omitted. This closes a static-link source inventory gap, not the remaining license-clearance or full-reconstruction gates |
| Shared reconstruction inputs | PASS | Actual prepared Stream builder configuration/scripts and the fixed public VPP patch repository retained in one hashed archive; every source package receives member bindings. This does not prove full dependency closure or complete OS reconstruction |
| Runtime-extra notice/source helper | PARTIAL | Actual source ZIP/h1 retention, base components, four Go packages, Kea and Podman generation/audits passed. The same-cohort AMD64 diagnostic recovery completed runtime generation and 169 package audits. Complete transitive license clearance and full ISO integration remain incomplete |
| Boot artwork source preparation | PASS | Both restricted boot PNG inputs replaced before compilation/source export; supplied SVG rendered onto a 640x480 canvas, visually inspected, theme contrast and dated provenance recorded; actual firmware UI remains unverified |
| Streaming source extraction | PASS | Authenticated outer archive and selected upstream build component; all 260 regular-file hashes matched the earlier extraction; late unsafe members remove private staging without exposing partial results |
| Native package-toolchain container | PASS | Native AMD64 toolchain image built after correcting package-name/snapshot dependency failures; package/ISO build success is not implied |
| Diagnostic native AMD64 ISO | PASS | Same-cohort recovery completed 169 package audits and ISO assembly. Targeted guest/installer fixes passed actual fresh OVMF/TCG boot, individual enrollment, native installation, retained administrator login without ISO, reboot and clean shutdown. Empty-name/short-password/mismatch retries passed. This is not a clean current-head release candidate |
| Diagnostic native ARM64 ISO | PASS | Same-cohort recovery completed 169 package audits and ISO assembly. Durable serial GRUB selection and targeted guest fixes passed actual fresh AAVMF/KVM boot, enrollment, installation, retained administrator login without ISO, reboot and clean shutdown. Factory SquashFS remained byte-identical to the input ISO after installation. This is not a clean current-head release candidate |
| Disk-format conversion primitive | PASS | Actual patterned 50 GiB sparse disk converted to standalone QCOW2 and streamOptimized VMDK; qemu-img checks and full content comparisons passed. Synthetic data only, no operating system or boot/OCI evidence |
| Sanitized OS QCOW2/VMDK | PASS, diagnostic scope | Fresh installations, clean shutdown, immutable-layer matching, offline cleanup and both 50 GiB format/integrity/content comparisons passed on AMD64 and ARM64. These retained formats are diagnostic; clean current-head candidates remain incomplete |
| Final exported-image boots | PASS, diagnostic scope | AMD64/TCG and ARM64/KVM sanitized QCOW2 and VMDK passed fresh-firmware/no-ISO unprovisioned prompt and individual enrollment/login, architecture/version, sudo, DHCP/default route, virtio NIC, distinct machine/SSH identities, complete NetBird lifecycle and clean shutdown |
| User-reported VMware installation | PASS, reported scope | The user confirmed login to the installed disk using the administrator created before installation. This is independent confirmation of account continuity, not a complete release acceptance or OCI result |
| Live OCI, AMD64 and ARM64 | NOT_RUN | No cloud operation performed; outside this task's deployment scope |

The AMD64 environment has no KVM device. The native ARM64 environment exposes
a KVM device; usable virtualization and sufficient memory still require checks
at the actual invocation. Its limited free memory requires measured resource
bounds. Runtime availability is checked at invocation. Diagnostic ISO/install/access/reboot checks passed with native AMD64 TCG and ARM64 KVM. Both diagnostic sanitized-disk formats passed; clean current-head release acceptance remains incomplete.

The [ARM64 diagnostic receipt](evidence/diagnostic-arm64-v2026.03.0.json)
binds the exact diagnostic ISO/export hashes and runtime helper file hashes.
Both sanitized formats passed fresh-firmware/no-ISO unprovisioned and individual
access, DHCP/default route, virtio NIC, distinct machine/SSH identities, real
NetBird lifecycle/secret-erasure tests and clean shutdown. It explicitly leaves
full source/license/current-head and OCI gates uncleared. Additional systemd
service descriptions are implemented for the next clean image; they are not
retroactively claimed as part of these diagnostic binaries.

ARM64の診断用記録はISO・配布形式・試験コードのハッシュを記録します。両形式で
初回アクセス、DHCP・経路、個別identity、NetBirdの実動作・キー消去と正常停止を確認しました。
最新コミットからの完全な候補、対応ソース再構築、全体の許諾とOCI実機確認は別の未達事項です。

## Acceptance criteria mapping

| AC | Implemented primitives | Remaining acceptance work |
| --- | --- | --- |
| 1 | Public source lock, authenticated acquisition, ephemeral signing-key API, safe source extraction, native checks | Complete fixed transitive dependency closure and integrated fresh-checkout validation |
| 2 | Native package recipes/config selection, ELF/EFI/kernel helpers, module-signature verification | Clean current-head candidates and complete runtime verification; diagnostic ISO builds passed |
| 3 | Supplied SVG, conversion/OS identity helper, attribution/license separation | Final-image branding/license audit and actual UI/installer verification |
| 4 | Non-secret service schema, controller, separate one-shot enrollment; actual diagnostic enrollment/lifecycle/save/reboot passed on both architectures and formats | Repeat integrated acceptance on clean current-head candidates |
| 5 | Standalone 50 GiB conversion/integrity/equivalence, fresh installation, offline cleanup and real diagnostic QCOW2/VMDK boots passed on both architectures | Clean current-head candidates |
| 6 | Offline cleanup, console enrollment, journal/free-space cleanup and fresh-firmware/access/DHCP/identity tests passed for diagnostic exports | Clean current-head integration and identity audit |
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
only the first individually audited components. Full source reconstruction remains NOT_RUN and blocks the pipeline. Actual diagnostic NetBird enrollment/save/reboot, native installations, offline overlay removal, journal/free-space sanitization and both-format disk access/reboot passed on both architectures. Clean current-head integration remains incomplete; these results must not be inferred from static CI.

The [Debian diagnostic source receipt](evidence/diagnostic-debian-source-v2026.03.0-arm64.json)
records source-tree restoration, original archive checks and copyright/notice
correspondence separately from binary reconstruction and distribution clearance.
The source collector now retains original named legal files as explicit legal
members. Missing, modified or undeclared source inputs stop collection.

Debian分の記録は、ソースの復元・原本のハッシュ・表示の一致を、OS全体のバイナリ再構築や
配布許諾の完了と区別します。未確認の独自ビルド分や依存分へ合格を流用しません。

The [AMD64 diagnostic receipt](evidence/diagnostic-amd64-v2026.03.0.json) records
the corresponding TCG tests on both immutable export formats. The later factory
hostname/hosts normalization and additional distributor notices require a new
image; they are not claimed to have been included in either diagnostic receipt.

AMD64も両形式の起動・個別設定・NetBirdの保存と再起動を通過しました。
診断用成果物の後に追加したhostnameの初期化と配布者の許諾表示は、次のビルドで確認します。

The [AMD64 Debian source correspondence receipt](evidence/diagnostic-debian-source-amd64-correspondence.json)
checks the diagnostic AMD64 package inventory independently: all 459 required
source groups occur in the authenticated/restored source set and all 658 installed
copyright files match the corresponding records. The same 72 custom binary
packages remain outside this Debian-only result. Sixty-eight exact Debian source/notice
decisions are recorded; remaining groups and the complete OS remain uncleared.

AMD64のDebian由来658パッケージも、復元済み原本と表示へ対応することを確認しました。
独自ビルド72パッケージと残る許諾判断は、別の未達事項として扱います。

Explicit release accelerator/runtime selection is implemented and focused checks
passed for native mode policy, absent/invalid runtime handling, fixture argument
scope, appliance environment restoration, candidate/evidence mode matching and
workflow/schema structure. Existing AMD64 TCG diagnostic runtime evidence is
separate from the complete clean-head production entry point, which remains
unverified. ARM64 TCG is under additional diagnostic testing and is not accepted
by the release entry point. KVM failure never triggers an automatic switch.

実行方式とruntimeの明示選択を追加しました。設定・失敗時の拒否・環境復元の試験と、
実機の起動試験は区別します。最新コミット全体のリリース実行とARM64のTCGは未合格です。

Native package source collection now selects the authenticated preferred input
snapshot, retains the shared/local patch recipes and records the native job and
original Stream member. Captured kernel configuration and recipe receipts are
retained as build inputs. Generated test keys, compiler output and external
build-tool links are not preferred source. API dependency inputs remain tied to
their fixed acquisition recipe. Complete source-set reconstruction is still a
separate required check.

ネイティブパッケージは認証済みの入力ソースを選び、適用レシピ、元の収録場所、
カーネル設定を合わせて保持します。ビルドで生成された試験鍵やコンパイラ出力を
ソースとして配布しません。完全なソース一式の復元確認は別の必須検証です。
