# Licenses and corresponding source

`LICENSE` covers original repository-owned code and the supplied Shirube OS SVG.
It does not relicense upstream code, Debian packages, assets or a resulting OS.
`NOTICE` records reused MIT portions and Linux patch attribution. The kernel
patch is GPL-2.0-only; its full license is under `LICENSES/`.

The supplied SVG is the sole product logo input. `producer.branding.install`
keeps it as SVG and converts it with `rsvg-convert` inside the container for GRUB
consumers requiring PNG. No upstream official logo/artwork is checked into this
repository. Boot-template/version/installer rebranding is wired into the producer and
requires final-image/runtime validation; setting os-release alone cannot pass the
branding gate.

Audit the exact pinned components before clearing a binary release. Inputs
include the VyOS legal page (https://vyos.io/legal/eula), exact source copyright
and license files, the Linux GPL, VPP and patches, every shipped Debian binary's
corresponding source/copyright, NetBird client and its linked dependencies.
NetBird v0.82.0's root license is BSD-3-Clause with explicit directory exceptions:
https://github.com/netbirdio/netbird/blob/v0.82.0/LICENSE. Do not describe its entire
repository as uniformly BSD-licensed. These references are audit inputs, not a
completed redistribution clearance or a noncommercial exception.

A component inventory must enumerate every shipped package by name, exact version
and architecture and map it to source, license and build-input archive members
with SHA256 hashes. This includes unmodified GPL/LGPL Debian packages and the
external dependencies/build/install scripts/generated-source inputs required for
reconstruction. A URL/hash list or automatic GitHub source archive is insufficient.
Missing source or uncertain licensing blocks publication.

`producer.bundles.bundle` archives explicit source/license/build input files,
checks for unsafe paths/private keys/Git history, normalizes archive metadata,
then verifies the actual member bytes. Oversized components fail before upload;
partition components into multiple independently verified archives below 2 GiB.
`producer.release.verify_sources` checks inventory member hashes and exact shipped
package coverage. The collector resolves the exact shipped Debian source versions and custom
component sources. Final per-component license audit, complete dependency
closure and reconstruction demonstration remain outstanding. Unknown entries
in `license-audit.json` remain denied.

The first exact-source audit records cover ipaddrcheck 1.4 and hvinfo 1.2.0,
under GPL-2.0-or-later. Each record binds the authenticated Stream archive and
the exact shipped copyright file SHA256, retains its notices, and still requires
complete corresponding source alongside binaries. An empty copyright file is
rejected. These component records do not clear the complete operating system.

Seventeen pure Python HTTP dependencies now have separate exact-source/notice
decisions: annotated-types, anyio, ariadne, click, fastapi, graphql-core, h11,
idna, makefun, pydantic, pyjwt, python-dotenv, python-pam, sgqlc, starlette,
uvicorn and wsproto. Their actual wheel and source notices were
read, their shared architecture-independent wheel inputs checked, and their
MIT, BSD-3-Clause or ISC notice/disclaimer conditions retained. This does not clear
pydantic-core, other native/embedded dependencies or the parent environment.

Upstream display/installer/boot source modifications receive dated notices in
the changed files; upstream headers remain intact. The modified core package
also lists changed paths with its copyright notice. The pinned libvyosconfig
source includes LGPL-2.1 terms and must be audited separately from the core's
GPL terms. The per-component audit must retain such distinctions.

The HTTP API dependency environment is independently packaged under MIT;
the empty-notice upstream wrapper/setup/maintainer code is not copied into it.
Its compatible package name/path and the exact 26 Stream dependency versions
are retained. `http-api.lock.json` pins both architecture wheels and each source
archive. Every installed distribution has its own legal-notice file and inventory
entry. A wrapper approval cannot clear an unreviewed bundled dependency; source,
notice hashes and approval are checked separately for each dependency.

Runtime-extra packaging retains upstream notices and records actual Go module
sources/build inputs where applicable. Module ZIPs are checked against the h1
identity embedded in the binary. This collection path still needs all real
runtime-extra builds and per-component license decisions; its presence is not
redistribution clearance.

Restricted boot PNGs are replaced in the prepared source tree as well as the
binary build. `SHIRUBE-ARTWORK.json` records original/replacement hashes and dates;
its adjacent license covers the replacement artwork only. Upstream code terms
and copyright notices remain applicable to the boot configuration.

Build-internal keys are regenerated by each rebuilder. Only public certificates
and repository fingerprints may appear in manifests/bundles. Kernel/module
signatures differ across rebuilt candidates; bit reproducibility has not been
established. Never publish original source archives containing archived private
signing keys unchanged: remove obsolete key material in the source preparation
stage and include the exact preparation/reconstruction scripts and modification
records instead.

NetBird's linked Go modules are inventoried from the actual pinned binary,
including replacement module identities. Each full source ZIP must match the
embedded h1 checksum; its source and retained notices have separate hashes.
The Go source/SDK and restored offline component rebuild are included alongside
these inputs. The integration package contains the notices. Module/source
retention and reconstruction do not themselves grant redistribution clearance.

When one package/version has architecture-specific source provenance, an audit
entry may use `name@version#source_identity`. Its declared source identity and
actual notice hash must still match exactly; a different architecture/source
variant cannot inherit that decision.

Eleven additional exact source-package notices have been reviewed: vyos-user-utils,
vyatta-biosdevname, vyatta-cfg, libtacplus-map, libpam-tacplus, libnss-tacplus,
vyos-libnss-mapuser, libpam-radius-auth, ndppd, live-boot and bash-completion.
Their source identities, versions and notice hashes were matched to actual
native binary packages. The recorded GPL grants remain conditional on preserving
notices and supplying complete corresponding sources; they do not clear other
linked components or the whole distribution.

The collector additionally retains the prepared upstream Stream builder's
configuration/scripts and the fixed public VPP patches as a shared archive.
All package inventories bind to its exact members. This preserves shared inputs
that are outside an individual component tree; retaining these inputs is not a
claim that the remaining dependency closure or full OS reconstruction has passed.

The native configuration library's OCaml switch is now included separately:
concrete installed package sources, their exact OPAM metadata, original
content-addressed downloads and per-component notices. Virtual capability/system
check packages retain metadata without being misrepresented as additional
runtime libraries. Git download caches/history are excluded; Git-fetched code
is supplied as its prepared source tree with pinned package metadata. The native
retention check covers 44 concrete packages. Their separate license decisions
and an offline reconstruction demonstration are still required. An approval for
the main configuration package cannot hide unresolved OCaml dependencies.

Forty-four actual linked NetBird modules now have exact source/notice decisions
under MIT, ISC, BSD-2-Clause, BSD-3-Clause or Zlib. The inventory preserves each
module's own grant, conditions and disclaimer; BSD non-endorsement and Zlib
origin/modification restrictions remain applicable. Other modules and the Go
standard library remain separate unresolved decisions. These records do not
approve the NetBird parent or an OS release.

VPP and core source exports use patched tracked files instead of generated
build/staging trees. VPP's original downloaded external source/bootstrap inputs
are also retained; its tracked `build-root/Makefile` is preserved. Confined
directory aliases are materialized from tracked source only.

`producer.source_fixtures` identifies exact, already-public upstream
cryptographic test-vector files by public source identity, archive member and
whole-file SHA256. Their source/test code is preserved. VPP bytes were compared
with the pinned public Git revision, and core bytes with the authenticated Stream
source. Both bundle creation and independent inventory/archive validation enforce
those bindings. Any changed file, different member or different source identity
is rejected. This source-only recognition never applies to binaries, build signing
keys, enrollment material or unknown keys. The optional fixed-key core smoketest
binary package remains forbidden. The detector is not a general credential
scanner; complete release source/binary/log/cache audits remain separate gates.

The restored headless-client build additionally records the actual selected
internal source packages/files and their hashes, using the same pinned compiler,
Linux architecture, CGO setting and build tags as the reference client. It checks
the root license's management/signal/relay/combined directory exceptions explicitly;
selecting one stops this BSD-client audit path for additional review. The AMD64
map selects 174 internal packages and no exception directory. This scope check
is not permission for third-party dependencies or the complete distribution.
The installed component inventory must bind to the exact retained package map.

## Published test fixtures

Published upstream test keys/examples are distinguished from build and instance
credentials. VPP hs_apps/http_static, sample tools and development headers are
retained. The exact TLS sample is bound to its public source commit/file hash
and PEM-content hash. Cryptodome test files are bound to the public 3.19.1 source
archive and complete file hashes; Salt documentation is bound to signed Stream
source. The policy is in `producer/public_test_keys.py` and `source-lock.json`.

The package scanner accepts only these identified fixture contents at declared
payload paths. Changed or additional keys undergo ordinary secret inspection.
This classification does not clear licenses or permit build signing keys,
enrollment keys, guest accounts or instance identities in exported images.

## Salt SDK source inputs

`containers/native/salt-sources.lock.json` records the exact installed Salt SDK
Python distributions, their original PyPI source archives, SDK native-library
sources and complete legal-file member hashes. The collector compares the actual
installed distribution names/versions with this inventory before retaining the
source archives and separate full notices. SDK assets for both architectures are
verified by SHA256 and size before the upstream packaging process consumes them;
unverified Relenv host fallback is replaced by this fixed-input acquisition step.
Upstream Salt features, test fixtures and component versions are preserved.

The source lock covers 144 components, including 54 runtime Python dependencies,
CPython, GCC, the declared Relenv native inputs/build recipe, 48 Rust crates from
cryptography's exact Cargo.lock, and the Debian-patched Rust standard-library
source, plus 23 observed isolated Python build-tool source inputs. Those build
inputs are retained and hash-verified; enforcing their consumption and proving
complete SDK reconstruction remain outstanding. Crate versions and checksums
must match the retained parent Cargo.lock;
the compiler and Debian source-package identity must match the actual builder.
The Rust source descriptor and Debian patch archive are retained alongside the
original source and original notices, with a combined source identity.
The timelib archive lacks a standalone complete PHP license, so its original
README notices and a separately hashed full upstream PHP-3.01 license are both
retained. The openssl Rust crate's short Apache header is supplemented with a
separately hashed complete upstream Apache-2.0 license. The Python msgpack Apache
header and certifi MPL-2.0 header are also supplemented with full upstream license
texts, while their original headers remain intact. Original copyright
headers remain in the complete source archive.
Source retention and archive verification do not clear redistribution. The lock
explicitly records the remaining toolchain/source-closure work and reconstruction
as unrun; no corresponding-source or license gate may infer success from this
component inventory.

The audit records redistribution conditions for the 48 exact crate source/notice
pairs. This covers the retained unmodified crate contents only. It does not clear
cryptography as a whole, the native OpenSSL library, Rust standard library,
remaining SDK dependencies, build backends or full OS reconstruction. The BSD
non-endorsement, Apache attribution/change notices and LLVM exception, and Unicode
copyright/non-promotion requirements remain attached to their component records.

Salt SDKの実際のPython依存一覧を固定入力と照合し、対応ソースと完全なライセンス
資料を別アーカイブへ保持する。両アーキテクチャのSDKは利用前にハッシュとサイズを
検証する。cryptography の Cargo.lock にある48個のRust依存は、版とチェックサムを
照合する。Rust標準ライブラリは、実際のコンパイラとDebianソースパッケージの版を
確認し、元ソース・Debianパッチ・ソース記述子・元の著作権表示を一緒に保持する。
上流の機能・公開テスト資材・版は維持する。ソース保持とアーカイブ検証は、
配布許諾や完全な再構築の合格を意味しない。固定ファイルに記載した未解決のソース入力、
再構築試験、個別ライセンス確認が残る間は公開条件を通過できない。

48個のRust依存については、固定したソースと完全なライセンス資料に対する配布条件を
監査記録へ追加した。これは各crateの未変更ソースに限った判断であり、cryptography全体、
OpenSSL本体、Rust標準ライブラリ、SDKの残りの依存、OS全体の配布許諾を意味しない。

msgpackのApacheヘッダーとcertifiのMPL-2.0ヘッダーについても、元の表示を保持し、
ハッシュを固定した上流のライセンス全文を追加で収録する。

The original charset-normalizer 3.2.0 source has a separate `data/NOTICE.md`
covering mixed Wikipedia CC-BY-SA/public-domain test files. It does not identify
per-file origins or the CC-BY-SA version. The complete original source archive
therefore remains uncleared pending fixture attribution or a scoped source export;
the library MIT notice does not establish permission for those separate files.
Runtime functionality is preserved while this source-distribution question is
resolved.

charset-normalizer 3.2.0の元ソースでは、テストデータのNOTICEがライブラリ本体の
MIT表示と分かれている。WikipediaのCC-BY-SAまたはpublic domainと説明されるが、
ファイルごとの由来とCC-BY-SAの版が不明なため、元ソース全体は配布許諾未確認として
記録した。帰属の確認または対象を明示したソース出力を整えるまで公開条件を通さず、
実行時の機能は維持する。

Twenty-one exact Python source/notice pairs have individual MIT, BSD-3-Clause or
Unlicense decisions. Original ApacheBench sample-output attribution in CherryPy
is retained. These entries cover only the declared unmodified distributions;
external programs, native libraries, vendored tools, remaining SDK dependencies
and full OS reconstruction remain separate checks.

Python依存21件は、固定した元ソースとライセンス資料に対するMIT・BSD-3-Clause・
Unlicenseの判断を個別に記録した。CherryPyのApacheBench出力例にある帰属も保持する。
各記録の対象は宣言した未変更の配布物に限り、外部プログラム、ネイティブライブラリ、
同梱ツール、SDKの残る依存、OS全体の再構築は別途確認する。

SDKのビルド時に実際に使われたPythonツール23件も、元ソースとライセンス資料を固定し、
保持対象へ追加した。固定入力だけを実際に使わせる処理とSDK全体の再構築確認は未完了であり、
ソースの保持をビルドの合格として扱わない。
