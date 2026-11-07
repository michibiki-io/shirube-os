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
