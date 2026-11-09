# Build inputs and native runners

The initial OS baseline is official VyOS Stream 2026.03. `source-lock.json` records
the exact archive, SHA256, minisign key and its official public authority:
https://vyos.net/get/stream/. A checksum mismatch fails before signature validation;
a signature failure produces no successful verification receipt. No replacement
baseline or private package repository fallback is allowed.

Source verification is an independent container stage:

```sh
docker build -f containers/verify/Dockerfile -t shirube-verify .
mkdir -p .work
docker run --rm -v "$PWD/.work:/output" shirube-verify \
  fetch-stream --destination /output/verified-stream
```

For ARM64, pass `--platform linux/arm64` and the ARM64 `BASE` digest recorded in
`source-lock.json`. Use a native Docker daemon. The default Dockerfile base is
AMD64; do not mistake container emulation for a native ARM64 build.

The package-stage entry point is `scripts/build-packages.sh ARCH NEW_OUTPUT`.
Its catalog includes core/network/routing packages and the kernel. It is **not**
the complete ISO runtime package closure. The kernel configuration maps Debian
AMD64 to upstream `config/x86`, and ARM64 to `config/arm64`. Module signing stays
mandatory and archived kernel signing keys are removed before rebuilding.
Foreign runtime ELF files and modules without matching signatures fail inspection.
The Linux stable perf patch retains its original attribution and GPL license.

Native toolchain inputs use architecture-specific Debian image digests and fixed
Debian/security snapshot timestamps. APT authenticates repository signatures and
package hashes; HTTP is used to bootstrap CA certificates. The compiler/toolchain
runs in a container. OPAM uses a pinned public repository revision and OCaml
4.14.2. Python pyhumps uses a fixed wheel hash. No host compiler, libraries or
QEMU installation is required by the package-stage wrapper.

The wrappers default to four CPUs and 8 GiB memory without swap expansion.
`SHIRUBE_BUILD_CPUS` and `SHIRUBE_BUILD_MEMORY_GIB` set explicit task limits.
Compiler concurrency also respects the actual cgroup CPU and memory limits;
C/C++ jobs receive a conservative 1.5 GiB budget per job. Kernel and Go jobs have
separate memory budgets. These are operating policies, not measured peak-memory
guarantees for every upstream compiler. Private build work stays inside its
own disposable container. Task outputs and explicitly selected public-input caches are mounted. It
never mounts host credentials, the Docker socket or sibling checkouts. It never
prunes shared Docker state or stops unrelated workloads.

## Reusing compiler and library builds

The native Dockerfile separates the `toolchain` stage from the final producer
code, guest integration and logo copies. The merged-release workflow exports
public compiler layers to the architecture-specific GitHub Actions BuildKit cache.
Runtime containers, signing keys and guest enrollment state never enter those
layers. Cache eviction can still cause a cold compiler build.

Unsigned library jobs have independent minimal Docker contexts. Their cache keys
bind the authenticated preferred-source tree, patches, compiler-stage inputs,
actual compiler/package fingerprints, architecture, recipe and dependency DEB
hashes. Product version, logo and guest configuration changes do not invalidate
an otherwise identical library. Kernel and core jobs remain outside this cache.
The artifact image contains only library DEBs, preferred sources, notices,
recipes and hash receipts; compiler workspaces are not exported.

Set `SHIRUBE_LIBRARY_CACHE_STORE` to opt into this path for a local ISO build.
The release workflow configures it automatically and restores/saves only curated
artifact image archives. Before import, image configuration and archive members
are checked. Before consumption, the producer recomputes its current source and
dependency identities and audits the actual packages. Changed inputs cause a
fresh build; corrupt matching entries fail rather than weakening validation.

Native AMD64 probes built two libraries, saved their artifact images, removed
their local tags, and restored them with version/artwork/guest-code changes.
Both package sets retained identical hashes without recompilation. A separate
native package producer consumed both validated entries successfully. Full ISO
use and cross-run Actions persistence still require execution; these component
checks do not establish complete image or release readiness.

Runner policy accepts `ACTIONS_RUNNER_AMD64` and `ACTIONS_RUNNER_ARM64`, individually
set or unset. Defaults are `ubuntu-24.04` and `ubuntu-24.04-arm`. A value may be a
single label or JSON label array, for example `["self-hosted","linux","ARM64"]`.
Labels are preserved exactly: an offline selected runner stays queued. There is
no provider/architecture fallback. Native host and actual package/binary
architecture checks are separate from label selection.

A conservative complete-build preflight currently requires 160 GiB free storage
and 8 GiB available memory. This is a planning budget, not an observed minimum.

After complete physical source-set restoration and component license clearance,
the release producer retires its owned compiler objects, private module-signing
workspace and temporary downloaded/extracted sources before creating guest disks.
Exported source archives, kernel DEBs and the inspected factory root remain.
This prevents compiler scratch and disk-test growth from overlapping. The final
evidence records free space before/after retirement; no host Docker cache is pruned.
The complete current-head build still needs measured peak storage and elapsed-time
acceptance before the planning budget can be replaced with a hosted-runner budget.

全対応ソースの実復元と各コンポーネントのライセンス確認が通った後、ディスク試験の
前に、このビルド専用のコンパイル中間物、署名用作業領域、一時ソースを削除します。
配布用ソース、カーネルDEB、検査済みfactory rootは保持し、削除前後の空き容量を
証拠に記録します。ホストのDocker cacheは削除しません。現行headの全工程で容量と
時間を実測してから、hosted runner向けの容量条件を確定します。

The `Test` workflow measures both native GitHub-hosted runner classes with a
small fixed-base fixture. Its `runner-capability-*` artifacts record CPU, available
memory/storage, KVM openability, a real BuildKit cache mount/chroot, and privileged
container mknod/mount/chroot execution. The probe is restricted to ephemeral
GitHub-hosted Linux runners and removes only the container and image tag it creates.
It records the PR head separately from the checked-out merge commit. Complete OS
build, guest boot, elapsed-time feasibility and release clearance remain separate
gates; the capability receipt explicitly records those as unrun.

`Test` workflowでは、両アーキテクチャのGitHub-hosted runnerで小さな固定入力の
試験を実行します。`runner-capability-*` artifactにCPU、空きメモリと容量、
KVMを開けるか、BuildKitのcache mountとchroot、特権コンテナーのmknodとmountの
結果を記録します。実行対象を一時的なGitHub-hosted Linux runnerに限定し、
作成したコンテナーとimage tagだけを削除します。PRのheadとcheckoutしたmerge
commitは別々に記録します。OSビルド、ゲスト起動、所要時間の実用性、公開可否は
それぞれ別に検証し、この資源試験では未実施として記録します。
KVM is a separate boot-stage requirement. Diagnostic AMD64 ISO/install/access/reboot tests passed under explicit TCG, but the complete production TCG disk/lifecycle path and its resource/time budget remain unvalidated; it cannot yet satisfy the release boot gate. The hosted runner defaults are selection defaults, not
claims that their available resources or nested virtualization meet these needs.

An explicit experimental TCG live-ISO probe is available in
`scripts/test-iso-console.py`, executed with the native toolchain container.
It uses two virtual CPUs, 2 GiB guest RAM and a 1200-second boot timeout,
fresh firmware and a new temporary disk. It checks console enrollment/access
and a live default route. The underlying VM path passed diagnostic AMD64 ISO/install/access/reboot tests; the standalone probe command is not claimed as separately exercised. These component results do not relax the production KVM requirement or establish complete final-disk acceptance.

The FPM bootstrap installs all ten gems from
`containers/native/rubygems.lock.json`, verifying their public archive hashes
before installing locally with dependency resolution disabled. The native
toolchain containing that locked bootstrap has been built on AMD64.

The full workflow calls `scripts/build-release.sh` for each selected native runner.
It requires the exact clean merged source commit and runs the ISO, fresh-install,
sanitization, conversion, source and publication gates in containers. Outputs use
fresh run/attempt-specific task directories; incomplete candidates are not staged.
The AMD64 virtual profile omits vendor-only physical NIC/QAT packages; this
difference is explicit in the source lock. Physical hardware parity is not claimed.

Remaining producer validation, complete toolchain/source closure and real
per-architecture verification are listed in `VALIDATION.md`. Toolchain image
creation, package compilation, ISO assembly, installed-disk boot and OCI execution
are different milestones.

## ISO-first execution

`scripts/build-iso.sh` builds an ISO independently of VM validation. It needs
`SHIRUBE_ARCHITECTURE`, `SHIRUBE_VERSION`, `SHIRUBE_SOURCE_COMMIT` and a new
`SHIRUBE_ISO_DIRECTORY`. An optional `SHIRUBE_SOURCE_CACHE` contains only the
public archive/signature and is reverified inside the container. It never accepts
an existing test disk. The ISO-only admission budget is 40 GiB free storage and
8 GiB available memory; the task stops if host headroom falls below 12 GiB disk
or 2 GiB memory. These are conservative operating budgets, not validated minimums.
An ISO build receipt leaves local boot, OCI and distribution clearance unverified.

The ISO wrapper defaults to a 2048 MiB host-memory reserve. For an explicitly
dedicated host, `SHIRUBE_HOST_MEMORY_RESERVE_MIB` may select a reserve from
512 to 65536 MiB; the container budget must still pass available-memory
admission. This adjusts only the owned task's stop threshold, never host kernel
settings or another workload. Choose the task budget from available RAM rather
than total installed RAM.

Set `SHIRUBE_INCLUDE_SOURCES=1` for source/license collection immediately after
ISO assembly, using the same private build tree and exact project archive. This
path requires no KVM and preserves the ISO receipt if collection fails. A
successful collection records inventory/archive names and individual license
clearance separately; complete source reconstruction remains `NOT_RUN` until
demonstrated. The optional audit has not yet run on a completed ISO. It does not
clear binary publication or replace installed-disk tests.

An optional `python-inputs-amd64` or `python-inputs-arm64` subdirectory in that
public-input cache holds the pinned HTTP API wheels/source archives. Each file
is rechecked against the architecture-specific lock before use. It contains no
build-internal keys or guest state and is mounted read-only. The dependency
environment can be packaged offline from this complete verified input set.

Sequential package jobs remove only their generated dependency metapackages
before resolving the next job; libraries are retained unless the next job's
dependency resolver replaces conflicting development headers. This prevents
one job's libedit/readline constraints from blocking a later routing build.

Core build-time lint/unit tests still run. The optional `vyos-1x-smoketest`
on-image toolkit is excluded from Debian package generation because it contains
fixed private-key/VPN test fixtures. Generating that forbidden package or finding
it in the assembled filesystem package list fails the producer. The ordinary
private-key payload audit remains unchanged for every generated runtime package.

After verifying the entire public Stream archive, the producer extracts only
the nested `vyos_tarballs.tar.gz` needed by the declared recipes. Unused outer
source bundles are not written into the build container. The preparation receipt
records both archive hashes, the exact selected member and the generated-output
exclusions applied inside it. This selection was checked against the actual
pinned public input; it does not establish complete corresponding-source closure.

VPP's DPDK Meson bootstrap is preseeded from
`containers/native/vpp-python.lock.json`: exact public Meson, setuptools, wheel,
packaging and pyelftools archives. All hashes are verified before the upstream
recipe runs. pip index access and persistent pip caching are disabled for this
stage, so an unavailable fixed input fails rather than resolving a newer one.
The fixed inputs have passed an actual cache-disabled, network-disabled Meson
bootstrap check on AMD64; the full modified VPP recipe still requires integrated
build validation.

Public VPP patch application preserves upstream authors and fixes the local
preparer identity/timestamp. This avoids introducing host identity or wall-clock
changes into the generated local patch commit identifiers. These local preparation
commits are not described as upstream commits.

The core pathname-discovery test uses a regular filesystem-only dummy at
`/dev/input/event0`. A privileged Docker runtime can populate real device nodes
after its configured tmpfs mounts. Before opening the dummy, the recipe detects
non-regular/link entries and hides them with a fresh nodev tmpfs inside the
container namespace, then verifies that only a regular file is used. Failure
to isolate the path stops the build. No host input device is opened for this test.

## Runtime component build inputs

OWAMP uses the source-built, pinned I2util development package and executes
its daemon tests as an unprivileged build user with normal fakeroot packaging.
Kea uses its own hash-pinned Meson 1.9.2 source, separately from VPP's Meson.
The tool source is retained with Kea's corresponding build inputs.

Node exporter 1.9.1 uses the upstream `nobtrfs` tag on both architectures,
excluding Btrfs metrics because the exact optional ioctl dependency lacks a
permission notice. Other collectors remain available. Telegraf stays at 1.28.3
with all plugins; RCON is fixed to the upstream MIT-license-only commit recorded
in `source-lock.json`, which leaves its two Go files unchanged. Kinesis parent
license and NOTICE files are pinned to the exact Go submodule commit. Embedded
README license notices are retained only through explicit file/hash mappings.

Each packaged Go runtime retains its actual linked module sources, upstream
notices and compiler/standard-library source. The collector checks these against
the installed inventory, executable hash and actual Go build information;
missing dependency clearance blocks release. Source retention does not itself
establish license clearance or a completed whole-OS reconstruction.

For a local ISO investigation, `SHIRUBE_PRESERVE_FAILED_BUILD=1` together with a
new `SHIRUBE_BUILD_STATE_FILE` preserves the stopped, task-owned container and
its private inputs after failure. This avoids discarding diagnostic compiler
outputs. It is opt-in, is not enabled by release automation, and does not turn
failed or older-source output into an accepted candidate. The retained container
may contain build signing keys and must stay private; remove that exact owned
container and its private inputs when diagnosis is complete.

ISO investigations keep their frozen project source and selected public inputs
under `.work/build-state` by default. `SHIRUBE_BUILD_STATE_DIRECTORY` may select
another private task directory. These inputs no longer depend on `/tmp` surviving
a host restart. Keep this directory out of uploaded artifacts/shared caches.
A stopped compiler container is diagnostic state, not a completed release
candidate; recovery must preserve signing-cohort boundaries and re-run every
required candidate gate. Put operator-captured logs in persistent private task
storage as well.

The optional public-input directory additionally accepts `vpp-python`,
`vpp-external`, `salt-sdk` and `opam-download-cache` subdirectories. Their fixed source/asset hashes are
checked before use; corrupt files fail without a network fallback. Missing files
use their fixed public HTTPS URLs. Native VPP inputs are preseeded before its
upstream downloader, so HTTP error pages cannot enter the compiler as archives.
The OPAM cache imports only SHA256/SHA512-addressed public source files. Git
caches and compiler workspaces are excluded. OPAM independently checks selected
archives against the pinned package metadata before compiling the configuration
library.

## Release duration and runner limits

Complete clean-build and repeat-build durations have not yet been established.
AMD64 and ARM64 candidates run in parallel; publication waits for both complete
candidates and their source, disk and runtime gates. Individual package results
and diagnostic retries do not establish a production release duration.

The job deadline is 360 minutes for default or other labels, and 720 minutes
when the selected label array explicitly includes `self-hosted`. GitHub-hosted
runners have a six-hour job execution limit ([GitHub Actions limits](https://docs.github.com/en/actions/reference/limits)).
These deadlines are bounds, not measured build-time estimates. Explicit labels
and preflight checks must still establish sufficient native resources and usable
virtualization; default runner selection does not establish release support.

## VPP component forwarding probe

`scripts/test-vpp-forwarding.sh` runs inside a fresh native compiler container
with the VPP package directory mounted read-only at `/packages`. Supply
`--cap-add NET_ADMIN --cap-add SYS_ADMIN --cap-add IPC_LOCK` and
`--ulimit memlock=-1:-1`; retain ordinary container PID, network and filesystem
isolation. No host networking, credentials or writable host mount is needed.
The packet ring is still bounded by the container memory limit.

It verifies the package cohort hashes, loads the upstream plugins, forwards
packets between two temporary network namespaces, and fetches Prometheus
metrics through http_static. Native ARM64 execution passed. This component
probe does not establish installed Shirube OS boot, OCI or release readiness.

Salt's Rust dependencies are prepared from the 48 exact crate archives in the
source lock. The producer verifies each archive hash, rejects changed caches,
links and unexpected roots, and generates Cargo's per-file/package checksums in
private staging. Only Salt's two SDK installation environments use that vendor
directory with Cargo offline mode; host and unrelated build settings are not
changed. Native AMD64 Cargo dependency resolution matched all 48 registry crates
inside a network namespace without network access. This component check does
not establish a complete SDK rebuild or OS source reconstruction.

SaltのRust依存は、ソース固定ファイルにある48個のcrateから準備する。各アーカイブの
ハッシュを確認し、変更されたキャッシュ、リンク、想定外のルートを拒否して、Cargoの
ファイル・パッケージ別チェックサムを一時作業領域へ生成する。オフライン設定はSaltの
2つのSDKインストール処理だけへ適用する。AMD64上のネットワークを持たない名前空間で、
48個すべての依存解決が一致した。SDK全体の再ビルドやOS全体のソース再構築は別途確認する。

Salt's runtime SDK Python installation now uses a verified local input directory
with 77 fixed Python source archives and one architecture-specific PPBT compiler
wheel. Both SDK installation functions, including their compiler bootstrap calls,
receive local pip index policy and Cargo offline policy. The outer packaging-tool
environment is separate. Source, compiler hash/size or architecture mismatches fail
without an index fallback. The compiler assets for AMD64 and ARM64 are independently
pinned; an AMD64 compiler wheel cannot enter the ARM64 input set.

The native AMD64 component test rebuilt the runtime Python dependencies and exact
Salt wheel without network access after fixed inputs were staged. It removed PPBT
as upstream packaging does, matched all 55 distributions and 425 pip/Relenv Python
source files, then passed EC signature verification, a CFFI native call and ZeroMQ
in-process transfer. Both Python and cryptography used OpenSSL 3.6.1. This test used
the verified prebuilt CPython/native-library SDK; it is not complete SDK native
source reconstruction, DEB packaging, guest OS or ARM64 runtime acceptance.

SaltのSDK内のPython依存は、固定した元ソース77件とアーキテクチャ別のPPBTコンパイラ
wheel 1件を検証してから使う。2つのSDKインストール関数とコンパイラ導入処理に、ローカル
入力だけを使うpip設定とCargoのオフライン設定を渡す。外側の梱包ツール用環境とは分け、
入力のハッシュ・容量・アーキテクチャが不一致なら外部indexへ切り替えずに失敗する。

AMD64の個別試験では、入力の準備後にネットワークを遮断してPython依存と想定版のSalt
wheelを構築した。上流と同じくPPBTを除去した後、55件の配布物と425個のPythonファイルを
照合し、暗号署名・検証、CFFIのネイティブ呼び出し、ZeroMQのプロセス内送受信を確認した。
PythonとcryptographyはいずれもOpenSSL 3.6.1を使用した。この試験では検証済みの
CPython・ネイティブライブラリを含むSDKを使っており、それら自体のソース再構築、DEB梱包、
ゲストOS、ARM64の実行確認を合格とするものではない。

## HTTP Rust component reconstruction

`containers/native/reconstruct_http_rust.py` reconstructs pydantic-core 2.27.1
and watchfiles 1.0.0 in a native producer container. It verifies all original
parent/crate/Git archives, restores the registry sources, compares fetched Git
package bytes with the retained fixed-commit archive and compiles with the network
namespace disabled. Rust 1.82.0 matches the compiler identity inspected in both
architectures' pinned wheels. Its compiler, Cargo, standard library and full
compiler/standard-library source archive are fixed in `http-rust.lock.json`.
This isolated toolchain does not replace Salt's compiler.

Use a prepared output directory from `http_api.py` as the read-only reference.
The work directory passed to the reconstruction command must not exist. For
example, with a native `shirube-producer` image built as described above:

```sh
docker build -f containers/native/Dockerfile -t shirube-producer .
mkdir -p .work/http-reference .work/http-rebuild .work/public-http-inputs
docker run --rm --cpus=4 --memory=3g \
  -v "$PWD/producer:/src/producer:ro" \
  -v "$PWD/containers/native:/src/native:ro" \
  -v "$PWD/.work/http-reference:/output" \
  --entrypoint python3 shirube-producer /src/native/http_api.py amd64 /output
docker run --rm --cap-add SYS_ADMIN --cpus=4 --memory=3g \
  -v "$PWD/producer:/src/producer:ro" \
  -v "$PWD/containers/native:/src/native:ro" \
  -v "$PWD/.work/http-reference:/reference:ro" \
  -v "$PWD/.work/http-rebuild:/work" \
  -v "$PWD/.work/public-http-inputs:/public-inputs:ro" \
  --entrypoint python3 shirube-producer /src/native/reconstruct_http_rust.py \
    amd64 /reference /work/reconstruction --public-input-cache /public-inputs
```

Use `arm64` on a native ARM64 runner. Acquisition needs public network access;
each compilation uses `unshare --net` and offline Cargo. `SYS_ADMIN` is scoped
to this container for that namespace operation. A supplied cache is optional in
content but every present input is rechecked; it must contain only regular public
archives. The reference package is never modified. The work output includes the
fixed compiler source archive, reconstructed libraries and component evidence.
The native probes exercise Pydantic validation and actual file-change notification.
A component PASS is not bit reproducibility, full OS reconstruction or a license
clearance. This command does not open the release gate.

## HTTPのRustコンポーネント再構築

上記コマンドは、公開された固定入力からRust拡張2件を再構築し、データ検証と実際の
ファイル変更通知を確認します。取得後のコンパイルはネットワークを遮断します。
参照パッケージは変更せず、コンパイラーはSalt用とは別の作業領域へ配置します。
ARM64ではネイティブrunner上で引数を`arm64`へ変更します。入力キャッシュの原本も
毎回ハッシュを照合し、作業領域にはコンパイラーと標準ライブラリのソースを保持します。
コンポーネント単位の成功を、OS全体の再構築や配布許諾の完了へ読み替えません。

The [both-architecture component receipt](evidence/diagnostic-http-rust-reconstruction.json)
records actual AMD64 and ARM64 executions of the same recipe hash. Both native
probes passed. Earlier diagnostic failures exposed Cargo source-directory deletion
and an incorrect assumption that Cargo emits an original Git manifest; the current
recipe verifies a read-only registry against freshly restored archives and checks
Git checkout manifests independently. The work contains acquisition caches: retain
only declared original archives and source/notice/build records for a source bundle,
never a wholesale work-directory export. Complete release integration remains open.

両アーキテクチャで同じ試験コードの再構築と動作確認を通過しました。取得用キャッシュを
作業領域ごと公開せず、対応ソースには宣言済みの原本と必要な設定・表示だけを含めます。
OS全体の再構築と配布ゲートへの組み込みは未完了です。

## Explicit release virtualization and container runtime

Release builds default to `SHIRUBE_QEMU_ACCELERATOR=kvm`. Missing KVM fails
without selecting another accelerator. Native AMD64 can explicitly select `tcg`;
diagnostic ISO/install/export/NetBird checks have passed in that mode. Production
configuration now propagates the choice through installation, exported-image
clones and the libguestfs appliance (`force_tcg`). The complete clean-head
release path remains unverified. ARM64 TCG acceptance is still incomplete and
is rejected by the release entry point; native ARM64 KVM remains the supported
validated guest-test mode. No QEMU emulation is used to compile either OS.

`SHIRUBE_DOCKER_RUNTIME=runc` selects an already registered runtime for this
build's containers only. With an empty value the daemon default is preserved.
There is no automatic runtime retry, daemon reconfiguration or provider switch.
The fixture's management/helper/candidate containers receive the same explicit
selection and retain their task ownership/cleanup checks. ISO-only builds support
the same scoped runtime selection without requiring KVM.

The release workflow accepts architecture-specific repository variables
`SHIRUBE_QEMU_ACCELERATOR_AMD64` / `_ARM64`, `SHIRUBE_DOCKER_RUNTIME_AMD64` /
`_ARM64`, `SHIRUBE_BUILD_CPUS_AMD64` / `_ARM64` and
`SHIRUBE_BUILD_MEMORY_GIB_AMD64` / `_ARM64`. Unset virtualization/runtime values
mean KVM and the daemon default; CPU/memory defaults remain four CPUs and 8 GiB.
One architecture's values do not supply the other's defaults. Native resource
preflight uses the configured memory budget. Label selection and queued offline
runner semantics remain unchanged; a selected runner must meet the actual storage,
memory, native Docker and virtualization requirements.

The manifest and matching per-architecture evidence record `qemu_accelerator`.
Missing, unsupported or mismatched values reject publication. Static mode-policy
and environment-restoration tests are separate from runtime boot evidence.

## 仮想化方式とコンテナーruntimeの明示選択

既定はKVMです。AMD64では明示的にTCGを選べますが、最新コミット全体のリリース候補は
まだ未検証です。ARM64のTCGは未合格のためリリース用コマンドで拒否します。
コンパイル自体は、両方ともネイティブのCPU上で行います。
runtime指定は今回のコンテナーだけへ渡し、Docker全体の設定は変更しません。
Actionsの設定はアーキテクチャ別に選択し、片方の値をもう片方へ流用しません。
初期設定値はCPU 4・メモリー8 GiBです。資源不足や選択方式の未対応時は停止し、
別のrunner・方式へ自動で切り替えません。

## Salt source version reconstruction

Salt's prepared source discovers its exact release revision from Git. Omitting
repository history without preserving upstream's supported version stamp changes
this input's reported version to 3007.0. The collector now retains the actual
installed `salt/_version.txt`, requiring an exact match to the pinned installed
Python version. The Debian source-package version (3006.21) is checked separately
against its fixed source baseline; it does not include the Python revision suffix. Restoring that file alongside the unchanged `salt/version.py` recovers
3006.21+0.ge35fd3e without Git history. [Actual component evidence](evidence/diagnostic-salt-version-reconstruction.json)
records both the incorrect unstamped result and the restored result. This closes
a version reconstruction gap, not the remaining SDK/whole-OS reconstruction or
license gates.

TCG guests use two vCPUs, 2 GiB guest RAM and an explicit 128 MiB translation
block cache. [QEMU's accelerator documentation](https://www.qemu.org/docs/master/system/qemu-manpage.html#standard-options)
defines `tb-size` independently of guest RAM. An ARM64 diagnostic using the
host-sized default cache ended during installation with a task-owned container
OOM event under its 3 GiB limit. The bounded-cache retry must pass actual guest
and offline cleanup checks before ARM64 TCG can enter production admission.

Debian corresponding-source acquisition resolves the complete exact-version set
from the installed package inventory and uses one authenticated APT source
invocation. Each resulting component is still independently checked against its
source descriptor, sizes and SHA256 values; hardlinks avoid duplicating shared
original archives in private staging. Source trees and complete legal files are
then restored as before. A differently named or versioned source cannot stand in
for an installed component. [The actual native batch probe](evidence/diagnostic-debian-source-batch.json)
fetched three fixed source versions in one call, including an epoch-bearing
version, verified their known descriptors, restored the trees and matched every
installed copyright. Complete whole-OS reconstruction remains a separate gate.

## Zero writes and temporary disk allocation

The offline appliance retains complete free-space zeroing, trim, journal
recreation and filesystem checks. For its sole input drive, QEMU
`detect-zeroes=unmap` avoids allocating a physical block for an all-zero write;
logical reads still return zero. This changes allocation, not the erasure steps.
The [actual diagnostic receipt](evidence/diagnostic-zero-write-erasure.json)
compares a 2 GiB control with the sparse option and records a 50 GiB real-OS
fixture: a newly injected test private key was absent from the entire logical
export, both conversions matched, and both formats passed individual enrollment,
login, product/architecture checks and clean shutdown. The immutable reference
was unchanged. These are focused AMD64 TCG results, not clean-head release
acceptance, an ARM64 50 GiB result or OCI execution.

## Restoring the complete source set

The release producer physically restores and hash-checks every source member in
task-owned scratch, discarding each archive's restored files only after that
archive is complete. Its proof records total restored bytes and the largest
archive payload. This bounds temporary payload storage to one archive; the
public restoration command retains the entire restored tree by default.

リリース用の検証では、すべてのソースmemberを作業専用領域へ実際に書き出し、
ハッシュを照合します。アーカイブごとの検証を終えてからその復元物を削除し、
一時領域の増大を抑えます。記録には総復元バイト数と最大アーカイブの内容量を
残します。利用者向けの復元コマンドは、既定ですべての復元物を保持します。

After verifying the selected release commit and downloading its complete
manifest, component inventories and source assets, the source consumer restores
every archive into a separate directory under a new destination:

```sh
python3 -m producer.cli restore-sources \
  --release-directory ./downloaded-release \
  --architecture amd64 --commit "$release_commit" \
  --destination ./restored-sources
```

Use `arm64` for that architecture. The command verifies outer asset sizes/hashes,
every component source/license/recipe reference, safe regular-file members and
the bytes written to disk. Executable scripts remain executable; symlinks, Git
history, traversal, duplicates and changed inputs fail. A preexisting destination
is preserved, and a newly created incomplete destination is removed on failure.
It can also inspect a locally generated diagnostic source-set manifest; this
does not publish its contents or approve unresolved redistribution terms.

`restoration.json` records **source-set restoration only**. It does not compile
binaries, approve licenses, or satisfy the complete release reconstruction gate.
The publication validator still rejects every uncleared parent or dependency.
For a native release candidate, the pipeline additionally checks the Git commit
stamp in the actual frozen project archive and requires that exact archive hash
in every component/dependency's build inputs. It runs the complete restorer before
guest admission and records the input manifest and actual restoration proof in
the candidate evidence. The `corresponding_source` check therefore means complete
source-set restoration and matching actual build recipes. It does not assert
bit-identical binaries. Unresolved redistribution permission stops the candidate
before expensive guest tests; the intermediate source manifest cannot publish.
Restore package originals and apply their retained Debian patches with
`dpkg-source --no-check -x` inside the native toolchain container; `--no-check`
does not replace the preceding exact descriptor/archive hash checks. Native
component trees, captured configurations, the shared Stream builder/VPP patches
and the history-free project archive remain separately bound in the inventory.
