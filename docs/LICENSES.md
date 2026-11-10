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

Shirube OS uses the [Nmap Security Scanner](https://nmap.org/) for port scanning
and IPv4 path-MTU discovery. Nmap 7.93's original NPSL 0.94, including its GPL2
exhibit, is retained in `LICENSES/Nmap-NPSL-0.94.txt` and installed under
`/usr/share/doc/nmap/NPSL-0.94.txt`. The complete original Debian copyright is
retained too; its older Nmap license summary does not replace the actual root
license. Nmap and the derived `shirube-path-mtu.nse` script keep their separate
NPSL terms. The GNU2 launcher constructs arguments and passes scanner output
directly to the console. The original Nmap probe, port selection and packet
handling remain unchanged; the NSE script displays the verified MTU lower bound
and computes conservative MSS suggestions. The IPv6-header calculation is not
an IPv6 path measurement. Sources, modification notices, license text and
acknowledgements accompany the same release. No Windows/Npcap binary is included.

Nmap のポートスキャンと IPv4 経路 MTU 検査を使用します。実際の 7.93 に同梱された
NPSL 0.94 全文と元の Debian 著作権表示を保持し、独自 NSE スクリプトにも同じ NPSL を
適用します。GNU2 の起動ヘルパーは結果を解析せず、そのままコンソールへ渡します。
元のパケット処理と測定方式を保ち、NSE 側で確認済み MTU 下限と保守的な MSS 候補を
表示します。IPv6 ヘッダーでの計算値は、IPv6 経路を測定した結果ではありません。
ソース、変更記録、許諾全文、必要な謝辞を同じリリースに含めます。

A component inventory must enumerate every shipped package by name, exact version
and architecture and map it to source, license and build-input archive members
with SHA256 hashes. This includes unmodified GPL/LGPL Debian packages and the
external dependencies/build/install scripts/generated-source inputs required for
reconstruction. A URL/hash list or automatic GitHub source archive is insufficient.
Missing source or uncertain licensing blocks publication.

The factory GeoIP database uses the unchanged DB-IP Country Lite November 2026
CSV in `third_party/geoip/`, under CC-BY-4.0. The source lock binds its compressed,
uncompressed and logical-row hashes, publisher checksums and full attribution.
The native router importer generates the SQLite template; its exact API source
hash and all imported rows are checked. The integration package retains the CSV,
full license and credit, and the collector inventories this data separately.
On first boot, systemd-tmpfiles restores the cache from the immutable template
after sanitization; existing administrator-updated caches are preserved. Explicit
runtime GeoIP updates retain the native behavior. Final-image boot validation is
required independently of the offline importer test.

VPP's seven fixed native archives and five Python bootstrap inputs have separate
source identities and original-notice inventories. Their 55 original legal files
are retained verbatim in the integration package under `vpp-inputs/`. Collection
checks the actual archive hashes and installed notice bytes; publication rejects
a missing child inventory even when the parent package names an approved license.
Every dependency still needs its own exact-source permission decision. These are
retained build inputs; the inventory does not assert every input is linked into
every VPP binary. Features remain enabled.
The five Python tools also retain pinned original source distributions, including
the separate LGPL-3.0 Autocommand source used by Setuptools. Wheel build inputs
and preferred sources have separate archive references and identities; original
source notices accompany the wheel notices. Verification rejects missing sources
or notice differences. No source reconstruction claim relies only on a wheel.

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

The core's generated distributor file list is not an upstream license change.
When that dated, path-only suffix differs by architecture or enumeration order,
the collector verifies the entire unchanged upstream notice against its reviewed
source/version/hash decision and checks the required retained notices. It records
both notice hashes and the changed paths as `notice_derivation`; it does not need
a new permission decision for every ordering of our own file list. Changed or
missing upstream terms, arbitrary suffix text, unknown sources and unreviewed
dependencies still fail. Unresolved decisions log component/version and source
and notice fingerprints, without raw notice contents or host paths.

コアに自動追記する自社の変更ファイル一覧は、上流の許諾変更として扱いません。
アーキテクチャや列挙順が違っても、元の著作権・許諾全文と固定ソースの承認が一致し、
必要な表示が保持されていれば、同じ許諾を適用します。元の表示と実際の表示のハッシュ、
変更一覧は記録します。上流条項の改変・欠落、任意の追記、未承認ソース・依存関係は
引き続き拒否し、不一致の対象と指紋をログに残します。

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

Binary packages sharing that source identity can retain different original
copyright documents. Such decisions use
`name@version#source_identity#copyright_sha256`; the collector selects them using
the actual document digest. An unknown document or a missing required notice
cannot inherit another variant's clearance.

同じソースから作られたパッケージでも、元の著作権本文が異なる場合があります。
その判断は `name@version#source_identity#copyright_sha256` で記録し、実際の本文の
ハッシュで選択します。未知の本文や必要な通知の欠落を、別の記録で合格にはしません。

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
inputs are retained and hash-verified. Fixed-input Python SDK reconstruction
passed as an isolated native AMD64 component test; complete native SDK and OS
reconstruction remain outstanding. Crate versions and checksums
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
保持対象へ追加した。固定入力だけを使うPython部分の再構築はAMD64の個別試験で通過したが、
SDKのネイティブライブラリやOS全体の再構築は未完了である。ソースの保持だけで
ビルドの合格と判断しない。

The SDK collector also compares the installed pip and Relenv Python files against
their retained exact source archives, rejecting changed, added or linked files
even when distribution versions still match. Empty Python package initializers
are valid source files. The native AMD64 component check matched all 425 files.
This binds those asset-provided bootstrap sources; it does not clear licenses or
prove native-library reconstruction.

SDKに含まれるpipとRelenvは、版だけでなく、保持した元ソースと実際のPythonファイルを
照合する。同じ版でも変更・追加・リンクがあれば拒否し、空の初期化ファイルは有効な
ソースとして扱う。AMD64の個別試験では425ファイルが一致した。これは起動用ツールの
ソース照合であり、配布許諾やネイティブライブラリの再構築の合格とは別である。

Salt SDK source inspection also identified embedded PLY notices inside pycparser
2.21. Their full original file hashes and complete notice-prefix hashes are
pinned; these notices are retained separately from pycparser's root BSD notice.
This closes a notice-retention gap without clearing that dependency or the SDK.
The exact looseversion 1.0.2 PSF-2.0 and packaging 24.0 BSD-2-Clause/Apache-2.0
source/notice pairs have individual decisions with their redistribution conditions.

Exact Apache-2.0 decisions also cover requests 2.32.5 (including its NOTICE),
distro 1.5.0 and importlib-metadata 8.7.0. Their license terms, archive legal
members and source-level license/attribution markers were inspected; these
records do not clear dependencies or the complete distribution. Contextvars' PSF-licensed test header and the complete matching public CPython license are now separately pinned for retention; that component remains outside these approvals.

Eight exact Debian source/notice pairs have initial individual decisions: acpid,
aptitude, bmon, configobj, conntrack-tools, cwidget, debconf and conserver. Their complete
notices were read and their restored-source notices matched installed notices
byte-for-byte, including epoch-bearing source versions. The source descriptor
and copyright hashes bind each decision. GPL source obligations, permissive
notice conditions and non-endorsement conditions remain mandatory; this is not
a blanket approval of Debian packages or the complete OS.

Conserver's root BSD notice does not replace its original licensing chain.
`LICENSES/Conserver-8.2.7.txt` retains the exact authenticated source `LICENSES`
bytes, including the Purdue and Ohio State terms (identified as the
LicenseRef-Conserver-Purdue and LicenseRef-Conserver-Ohio-State terms in the
inventory). The required Ohio State acknowledgement and Purdue credit appear
in `NOTICE`. The integration package carries these full distributor notices,
and every component source archive includes explicit legal-file bindings.
Required supplemental notice hashes are checked before a decision can clear.
The decision covers the unchanged Debian binary; it is not a blanket BSD
relicensing of contributed code or a noncommercial-use exception.

A second exact Debian batch covers dh-runit, dmidecode, gawk, grep, hostname,
htop, iotop and ipcalc under their stated GNU GPL versions. The declared
excluded GFDL documentation was absent from the restored gawk tree. Iotop's
upstream COPYING confirmed GNU GPL version 2 alongside its GPL-2+ grant. Each
decision remains bound to its source descriptor and complete copyright hash.

A further batch covers ipmitool, iproute2, iw, jansson, json-c, libcbor,
libconfig-model-perl, libedit, libestr, liblinear, libndp, libnetfilter-conntrack,
libnetfilter-queue, libnginx-mod-http-echo, libtry-tiny-perl and live-config.
Complete Debian notices and primary source legal files were inspected; retained
GNU texts were compared in full, and LGPL source/relinking conditions remain
mandatory. Liblinear's excluded Windows/BLAS trees were absent. These decisions
do not approve their dependencies or the whole image. The collector now
reconstructs each exact Debian source tree and explicitly includes named
original legal files in its source/legal archive, alongside installed notices.

The HTTP API source collector now retains the Cargo.lock closure for pydantic-core
2.27.1 and watchfiles 1.0.0: 156 registry bindings plus two Git package bindings
from the exact notify commit declared by watchfiles. All registry checksums,
parent archive/lock hashes, fixed Git archive hash and original legal files are
checked. Each dependency has an installed notice and a separately denied-by-default
license inventory entry. See the [diagnostic component receipt](evidence/diagnostic-http-api-source-retention.json).
Actual AMD64 packaging, native imports and GraphQL execution passed. This closes
an original-source inventory gap; it does not establish native-extension or full
OS reconstruction, compiler/standard-library closure, or distribution clearance.
The Git workspace is preserved intact; the returned registry Cargo home alone
is not a complete offline Git build environment.

HTTP APIのRust依存も上流の固定lockと照合し、原本と許諾表示を保持します。
AMD64でパッケージ生成と実インポートを確認しました。ソースの保持をバイナリの再構築や
全体の配布許諾へ読み替えず、未確認のコンパイラー・標準ライブラリ等は公開ゲートで止めます。

Exact source/combined-notice decisions now also cover MarkupSafe 2.1.2
(BSD-3-Clause), zc.lockfile 1.4 (ZPL-2.1) and the retained pathspec 1.1.1
build-tool source (MPL-2.0). Original notices, trademark/non-endorsement limits,
ZPL modification dates and MPL covered-source availability remain conditions.
A missing Lars Gustaebel copyright/permission header in backports.tarfile 1.2.0
is separately pinned for the next collection; that component remains uncleared.
Earlier diagnostic source receipts do not include this new notice binding.

上記3件は原本と表示のハッシュへ限定した個別判断です。backports.tarfileの
埋め込み著作権表示は保持対象へ追加しましたが、コンポーネント全体の許諾判断は保留します。

Eight additional exact Debian source/copyright decisions cover localepurge,
lsb-release-minimal, lsscsi, Lua 5.3, LZO2, mdadm, Minisign and MTR. GPL source
availability and full notices remain mandatory. Minisign's full original LICENSE,
including its newer upstream copyright range, is separately retained under
`LICENSES/Minisign-0.11.txt` and required by its exact-source decision. These
68 Debian decisions do not clear the remaining source groups or the complete OS.

Debian分の追加8件も原本と表示へ限定した判断です。Minisignの上流表示は配布用の
追加表示として保持し、不足・変更時は許諾判断を無効にします。全体の合格とは別です。

The HTTP package now includes the full Rust 1.82.0 compiler/standard-library
preferred-source archive in its corresponding-source inputs. Both native package
builds checked actual extension bytes against their fixed wheels and embedded
compiler identity. Original source legal files and confined aliases are retained
as installed notices, regenerated independently by the collector and bound to a
separate standard-library component. See the [source-binding receipt](evidence/diagnostic-http-rust-standard-library.json).
Missing or changed source/notice/compiler bindings reject collection. This closes
a source inventory gap; it does not approve the complete Rust legal set or the OS.

HTTP拡張へ取り込まれた標準ライブラリも原本へ対応付け、両アーキテクチャで確認しました。
不足した原本・表示・コンパイラー情報は収集時に拒否します。保持と照合の成功を
許諾確認の完了へ読み替えません。

Eight further exact Debian decisions cover net-tools, netbase, netplug, npth,
pciutils, pystache, python-cffi and python-inotify. Their original notices and
primary license files were inspected in the recorded audit; these decisions
remain conditional on complete corresponding-source and notice distribution.
They do not clear the remaining components or the complete OS.

追加8件も、確認済みの原本・表示・版の組合せに限定します。対応ソースと原文表示の
配布条件を維持し、未確認の依存関係やOS全体への合格判定には流用しません。

Five further Salt SDK decisions cover psutil 5.8.0, setproctitle 1.3.2,
PyYAML 6.0.1, pyOpenSSL 24.0.0 and rpm-vercmp 0.1.2. Their complete combined
notices were regenerated from the fixed original archives and matched the
reviewed bytes, including rpm-vercmp AUTHORS. These 93 exact SDK decisions
leave 51 SDK components and complete-distribution clearance unresolved.

SDK分の追加5件は、固定した原本から表示を再生成して確認しました。確認済みは93件、
未確認は51件です。OS全体の配布可否は、残るソース・依存関係の検証で判断します。

Twenty further exact Debian decisions cover python-linux-procfs, python-psutil,
python-py, python-systemd, python-tabulate, python-tz, pyudev, screen, sgml-base,
six, squashfs-tools, ssl-cert, tpm-udev, ucf, usrmerge, virt-what, wireless-regdb,
wslay, xml-core and yajl. Full installed notices and original named legal files
were inspected, including vendored Python notices and GNU text differences.
These 68 decisions do not clear the remaining Debian groups or the complete OS.

追加20件も原本・表示・版を固定した判断です。同梱された原文表示とGNU条文の差分まで
確認し、依存関係やOS全体への合格判定とは分けて記録します。

Eleven further fixed Salt runtime/build inputs have separate exact-source and
complete-notice decisions: autocommand, cryptography, frozenlist,
python-dateutil, relenv, wheel, ppbt, calver, build pycparser, setuptools-rust and
trove-classifiers. Autocommand retains its LGPL-3.0 obligations, including the
incorporated GPL text, library replacement and corresponding application source.
Cryptography retains both offered license texts; dateutil uses its BSD grant
that explicitly covers all contributions. Apache NOTICE/copyright conditions
remain applicable. CFFI's embedded source notices and Immutables' separate header
license require additional bindings and are not cleared by these decisions.

Immutables 0.21 now additionally retains the exact pythoncapi_compat header
notice identified by its root license. Its separate decision covers Apache-2.0
and 0BSD, including the upstream NOTICE. The CFFI build source also retains its
original AUTHORS file; that addition does not clear its embedded source terms.

## Scoped charset-normalizer corresponding source

The original charset-normalizer 3.2.0 source archive remains uncleared: its
`data/NOTICE.md` mixes Wikipedia/public-domain claims without per-file attribution
and forbids modification of those samples. Its package configuration excludes
that data from installed libraries, and the notice explicitly says it is
unnecessary for installation/runtime use.

The source collector instead exports every library/build file and original
notice unchanged, omitting only the 18 actual `data/sample-*.txt` members by
exact file hashes. The original notice says 17; the actual archive contains 18.
The original archive is never included in this scoped public export. An adjacent
source-export record preserves the original hash, exclusion inventory and scope.
The exact resulting archive hash has its own MIT decision; it cannot inherit or
replace approval for the original full source archive. All other source/license
gates remain in force.

[Actual AMD64 component evidence](evidence/diagnostic-charset-source-export.json)
records an offline wheel rebuild, exact comparison of all 13 installed library
files, and ASCII/UTF-8 detection. The original sample-based pytest suite is
explicitly unavailable; full SDK/OS reconstruction and release clearance are
separate outstanding checks. Runtime library behavior and features are retained.

Kea source export retains its authenticated preferred source, the actual prepared
files from the pinned packaging repository and the exact Meson source archive.
Compiled Meson trees and package staging are excluded; modified Debian recipes
and required inputs remain. Five original TLS self-test keys were compared
byte-for-byte with the authenticated Stream input and receive only exact
source/member/hash recognition. [Source fixture evidence](evidence/diagnostic-kea-source-fixtures.json)
does not clear a runtime credential, Kea's license or the complete distribution.

A further 24 exact Debian source/copyright pairs have individual decisions,
including filesystem/network utilities, initramfs-tools, small X libraries and
runtime dependencies. Composite terms preserve GPL-licensed packaging alongside
BSD/LGPL upstream code; X11/HPND name-use restrictions and Boehm GC modification
notices remain applicable. GRC's explicit GPL grant is retained together with its
expressly optional commentary. Fontconfig's Unicode terms, Wget's documentation
license and Dialog's additional source/build grants remain outside this batch.

A batch scan of remaining original runtime-extra inputs found two additional
Podman TLS/verification self-tests. Their whole-file hashes match the signed
Stream source, including the vendored DSA verification test. They have exact
source-only recognition; unknown generated keys and runtime credentials remain
rejected. [Batch source evidence](evidence/diagnostic-runtime-extra-source-fixtures.json)
is separate from dependency licensing and completed source reconstruction.

## Actual linked permissive-license comparisons

[The linked-component comparison receipt](evidence/diagnostic-runtime-permissive-license-bindings.json)
adds 224 exact source/installed-notice decisions for MIT/BSD dependencies of the
actual runtime binaries. Complete grant bodies were compared against freshly
read original grants, preserving every word, punctuation mark, condition and
disclaimer; only whitespace was normalized. Complete copyright/header prefixes
were separately read and remain in the retained originals. Every decision still
binds the full original notice hash and actual source identity, including
source-qualified entries for different module representations.

Mixed grants, license pointers, templates, unknown text and supporting notices
were deferred. In particular, locating an MIT grant after an Apache license does
not permit discarding the preceding Apache terms. This is not an SPDX-name
lookup, a package-repository approval, or clearance for a parent package or OS.

A further [184 exact linked source/notice decisions](evidence/diagnostic-runtime-apache-license-bindings.json)
cover complete Apache-2.0 grants. Every operative term matched the original
license text; all appendix/copyright variations and the supporting notice were
read separately. Original application-instruction omissions do not remove any
operative condition, and the original full notices remain unchanged in the
artifacts. Appended BSD/other licenses, mixed terms and unknown supporting
notices were deferred rather than hidden behind an Apache-only decision.


## Scoped libxcrypt corresponding source

The fixed libxcrypt 4.5.2 upstream `LICENSING` inventory explicitly lists
source-only tests with unknown individual licensing. The source collector
exports a separately pinned archive omitting only the nine listed files still
present in that release. Every library algorithm, public header, configure and
install source remains unchanged. The two original Makefile controls remove the
absent test header from the noninstalled `all-am` prerequisites and retain their
original attribution plus a dated modification notice.

The export contains the original archive hash, every omitted file hash, and the
before/after hashes of both build controls. The complete original licensing
inventory, authors, acknowledgements, LGPL text and 86 C/H legal headers are
retained. The collector rejects raw-archive substitution, unexpected exclusions,
changed test bytes and a different final export hash.

[Native component evidence](evidence/diagnostic-libxcrypt-source-export-native-builds.json)
records successful normal library builds and installation from the exact same
exported bytes on AMD64 and ARM64. The upstream check suite is incomplete and
was not run; SDK binary reproduction, a current complete release candidate and
OCI validation are separate and remain unverified.

### libxcrypt の対応ソース

上流のライセンス一覧で個別の許諾が不明とされ、当該版に存在する試験用ファイル
9 件だけを除外したソースを固定します。ハッシュ方式、ライブラリ、公開ヘッダー、
configure とインストールのコードは保持します。Makefile 2 件では、除外した
試験用ヘッダーを通常ビルドの前提一覧から外し、変更記録と元の帰属を残します。
元アーカイブ、除外ファイル、変更前後の制御ファイルのハッシュを記録し、元の
ライセンス一覧と各ソースの許諾ヘッダーも配布します。同一の出力ソースから
AMD64 と ARM64 の通常ビルド・インストールを確認済みです。上流の試験一式は
不完全なため未実施であり、SDK バイナリの再現やリリース全体の合格とは区別します。

## Go dependency source delivery

Serf v0.10.1 separately restricts reproduction of its website design. Quic-go
v0.64.0 separately restricts its branding assets. Delivery therefore uses exact,
separately hashed preferred-source ZIPs that omit Serf's `website/` and quic-go's
`assets/`. The original module inputs remain verified during compilation.
Every other original file, including runtime source, dependency declarations
and root licenses, retains its original bytes. Original aggregate notices and
an omission manifest accompany each export. The raw original ZIPs have no
redistribution approval from these decisions.

The inventory records both the original identity and preferred-source hash.
Preferred files use the `.preferred-source.zip` suffix; they must not be placed
in a module proxy under the original h1 checksum. After verifying and restoring
the release source set, unpack the preferred tree in a fresh reconstruction
workspace inside the producer container. Preserve the original `go.mod` and
`go.sum`, and apply an explicit local replacement for the exact exported version
when running the retained parent build recipe. For example, from that workspace:

```sh
go mod edit -replace=github.com/hashicorp/serf@v0.10.1=./preferred/github.com/hashicorp/serf@v0.10.1
go mod edit -replace=github.com/quic-go/quic-go@v0.64.0=./preferred/github.com/quic-go/quic-go@v0.64.0
```

Use the retained parent-specific Go SDK and only the replacement relevant to
that parent. Record this reconstruction change alongside the originals; all
other dependency pins remain applicable. Rebuilt module provenance can differ,
so this procedure makes no byte-reproducibility claim.

[Actual source-delivery evidence](evidence/diagnostic-go-preferred-source-delivery.json)
checks all 659 retained original files, including 560 Go/C/assembly source files,
against the fixed public inputs and verifies a real delivery archive. Parent
binary recompilation from this delivered variant and final OS acceptance are
separate checks. Two unchanged public TLS test vectors in the quic-go source
have exact origin/member/archive-hash recognition; generated build or instance
keys have no exception.

### Go 依存の配布用ソース

Serf の付属サイトと quic-go の制限付きロゴを配布対象から除き、それ以外の元ファイル、
依存定義、実装コード、ライセンス本文を保持します。元の入力と配布用ソースには別々の
ハッシュを付け、省略したファイルの一覧と元のライセンス表示を同梱します。
配布用 ZIP は元の Go モジュール ZIP と同じ h1 を持たないため、復元後の新しい作業領域で
展開し、必要な対象版だけをローカルソースへ置換して、保持した SDK とビルド手順を使います。
元の `go.mod`、`go.sum` と再構築時の変更記録を残します。ソースのバイト一致と配布アーカイブの
検証は実施済みですが、この配布形態からの親バイナリ再コンパイル、OS 全体の受け入れ、
OCI 実機検証の成功を意味しません。

## OpenTracing preferred source

Debian OpenTracing 1.6.0-4 contains a saved GitHub HTML page where Catch2's
license text belongs. The export replaces that one page with the full Boost1.0
license explicitly named by the unchanged Catch2.0.1 header. All 117 other
original files, including every code, test, build and packaging file, retain
their exact bytes. The source export records both hashes, the original Debian
descriptor and its own distinct identity; the raw archive is not approved.
Inside the native producer container, configure the applied preferred tree
with CMake, build, run CTest and install normally. No features are disabled.
[Existing component evidence](evidence/diagnostic-debian-opentracing-preferred-source-export.json)
records normal build/install and all 11 tests passing on each architecture.
License-text-only changes use byte comparisons and notice hashes; they do not
require another library or ISO build. Final coherent OS acceptance is separate.

### OpenTracing の配布用ソース

ライセンス本文の位置に保存されていた GitHub の HTML を、元の Catch2 ヘッダーが
指定する Boost1.0 全文へ置き換えます。他の元ファイル 117 件はバイト単位で保持し、
コード、テスト、ビルド定義、パッケージ設定は変更しません。変更前後のハッシュ、
元の Debian 記述子、配布用ソース固有の識別子を記録します。コンテナ内で適用済みの
ソースを CMake で構成し、通常のビルド、CTest、インストールを行えます。
既に両アーキテクチャで 11 件ずつのテストとインストールを確認済みです。
許諾文書だけの変更では差分とハッシュを照合し、ライブラリや ISO を再ビルドしません。
最終的な OS の受け入れとは区別します。

## Kbd preferred source

The Debian kbd 2.5.1-1 package installs console and keyboard utilities,
translations and manuals. Its original install recipe does not install the
optional upstream font collection. The source export omits only three Agafari
font assets whose original notice restricts redistribution to non-commercial
Unix use. All 1,146 other original regular files remain byte-identical,
including executable sources, build/install scripts and keymaps. Other fonts
and all original notices remain. The original descriptor is retained as
provenance, and only the distinct preferred export is approved for delivery.

Reconstruct the executable package with the unchanged Debian
`dpkg-buildpackage` recipe. This does not assert that the optional upstream
font `install-data` target is complete. [Static export evidence](evidence/diagnostic-debian-kbd-preferred-source-export.json)
records exact member hashes and the unchanged packaging scope; it makes no
native compilation or boot claim. No binary rebuild is required for this
source-delivery-only omission of assets absent from the executable package.

### Kbd の配布用ソース

Debian の kbd パッケージは、コンソール・キーボード用コマンド、翻訳、マニュアルを
インストールし、上流の任意フォント集はインストールしません。配布用ソースからは、
元の許諾が商用利用を認めていない Agafari フォント 3 件だけを除外します。
他の元ファイル 1,146 件、実行コード、ビルド・インストール手順、キーマップ、
他のフォントとすべての著作権表示をバイト単位で保持します。
元の記述子は由来の記録として残し、固有の識別子を持つ配布用ソースだけを許諾対象とします。

実行パッケージは元の Debian `dpkg-buildpackage` 手順で再構築します。
上流の任意フォント用 `install-data` 全体が揃っているという意味ではありません。
この確認はファイル差分と元のパッケージ範囲の静的検証であり、コンパイルや起動試験の
成功を意味しません。実行パッケージに含まれない素材の配布用ソースからの除外のために、
バイナリを再ビルドする必要はありません。

## Chardet and OpenVPN delivery sources

Chardet's export omits 424 optional third-party corpus text files whose
permissions are not established by the library license. All other 98 original
regular files, including every runtime module, language model, CLI, test runner
and build/install script, are byte-identical. Reconstruct the Python library
normally; use `DEB_BUILD_OPTIONS=nocheck` for Debian packaging and do not report
the incomplete original corpus suite as passing.

OpenVPN's Linux export replaces one unused Windows service sample, whose
notice supplies no redistribution grant, with an independently authored GPL2
diagnostic stub. The stub preserves Automake's declared source-file presence;
the original `WIN32` condition excludes it from both Linux builds. All 619 other
original regular files remain byte-identical, including every Linux source and
the complete Debian build/install recipe. This export does not support the
Windows service build. All original GNU/OpenSSL/Apache2 exceptions and other
legal notices remain.

Both exports carry exact original-member hashes, distinct export identities
and original descriptors as provenance. Raw original archives are not cleared.
The [Chardet](evidence/diagnostic-debian-chardet-preferred-source-export.json) and
[OpenVPN](evidence/diagnostic-debian-openvpn-preferred-source-export.json)
records are static delivery-source checks, not native compile or boot evidence.
They do not require recompiling unchanged Linux runtime implementations.

### Chardet と OpenVPN の配布用ソース

Chardet は、ライブラリの許諾では権利を確認できない第三者テスト文面 424 件を除外します。
他の元ファイル 98 件、実行モジュール、言語モデル、CLI、テスト用コード、ビルド・
インストール手順をバイト単位で保持します。Python ライブラリは通常の手順で再構築でき、
Debian パッケージでは `DEB_BUILD_OPTIONS=nocheck` を指定します。
文面を除いた元のコーパステスト全体を、成功扱いにはしません。

OpenVPN は、再配布の許諾が示されていない未使用の Windows サービス用サンプル 1 件を、
独立して作成した GPL2 の診断用ソースへ置き換えます。Automake が要求するファイルは残し、
元の `WIN32` 条件に従って Linux ビルドから除外します。他の元ファイル 619 件、
すべての Linux 実装と Debian のビルド・インストール手順はバイト単位で保持します。
この配布形態では Windows サービスのビルドを扱いません。
元の GNU・OpenSSL・Apache2 の例外と個別の許諾文書も保持します。

両方に変更対象のハッシュ、配布用ソース固有の識別子、由来を示す元の記述子を同梱し、
元の未加工アーカイブは許諾対象にしません。確認記録は配布用ソースの静的検証であり、
コンパイルや起動試験の結果ではありません。変更していない Linux 実装を再コンパイルする
必要はありません。
