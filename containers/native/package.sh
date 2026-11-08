#!/bin/bash
# Copyright (c) 2026 michibiki.io; MIT License (see NOTICE).
# Modified 2026-11-08 for native AMD64/ARM64 builds and ephemeral private keys.
DEB_ARCH=$(dpkg --print-architecture)
case "$DEB_ARCH" in amd64) KERNEL_ARCH=x86 ;; arm64) KERNEL_ARCH=arm64 ;; *) exit 1 ;; esac
export DEB_ARCH KERNEL_ARCH

kernel_dependencies() {
    apt-get install -y --no-install-recommends bc debhelper rsync kmod cpio bison flex libelf-dev libssl-dev dwarves libdw-dev libslang2-dev libtraceevent-dev libpfm4-dev python3-dev python3-setuptools
    /usr/bin/python3 -c 'import setuptools, sys; assert sys.version_info >= (3, 6)'
    /usr/bin/python3-config --embed --ldflags >/dev/null
}
clean_kernel_tools() {
    # Kernel mrproper does not remove all tools/ outputs from the source archive.
    make -C tools/build clean
    make -C tools/perf JOBS=2 clean
}
apply_kernel_tool_fixes() {
    : > "$PATCH_RECEIPT"
    for patchfile in /kernel-patches/*.patch; do
        [ -f "$patchfile" ] || continue
        sha256sum "$patchfile" >> "$PATCH_RECEIPT"
        if patch --dry-run --fuzz=0 -p1 < "$patchfile" >/dev/null 2>&1; then
            patch --fuzz=0 -p1 < "$patchfile"
        elif patch --dry-run --reverse --fuzz=0 -p1 < "$patchfile" >/dev/null 2>&1; then
            echo "Repository patch already applied: $(basename "$patchfile")"
        else
            echo "Ambiguous repository kernel patch state: $patchfile" >&2
            exit 1
        fi
    done
}
package_kernel() {
    # perf's wrapper ignores MAKEFLAGS and otherwise selects all online CPUs.
    make ARCH="$KERNEL_ARCH" -j2 bindeb-pkg JOBS=2 PYTHON=/usr/bin/python3 PYTHON_CONFIG=/usr/bin/python3-config BUILD_TOOLS=1 LOCALVERSION=-vyos KDEB_PKGVERSION=6.6.128-1 KDEB_CHANGELOG_DIST=unstable
    # The snapshot's perf packaging hardcodes only three dependencies, despite
    # linking libpython/libdw/libcrypto/etc. Derive runtime dependencies from the
    # actual native binary so installation cannot silently omit required libs.
    deps=$(dpkg-shlibdeps -O -e debian/linux-perf/bin/perf)
    deps=${deps#shlibs:Depends=}
    test -n "$deps"
    dpkg-gencontrol -plinux-perf-6.6.128-vyos -Pdebian/linux-perf "-DDepends=$deps"
    dpkg-deb --build debian/linux-perf ..
}

set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
trap 'chown -R "$HOST_UID:$HOST_GID" /output' EXIT
[ "$(dpkg --print-architecture)" = "$DEB_ARCH" ]
if grep -R -E '^[[:space:]]*deb.*packages\.vyos\.net|^[[:space:]]*URIs:.*packages\.vyos\.net' /etc/apt/sources.list /etc/apt/sources.list.d 2>/dev/null; then
    echo 'Private VyOS APT repository configured; refusing bootstrap' >&2
    exit 1
fi
if [ "$BUILD_RECIPE" = http-api ]; then
    # The upstream wrapper ships an empty copyright notice. Build our own
    # packaging from the exact dependency facts; do not copy its wrapper code.
    exec python3 /src/native/http_api.py "$DEB_ARCH" /output
fi
mkdir /output/source
cp -a /snapshot/. /output/source/
cd /output/source
find /snapshot -type f -print0 | sort -z | xargs -0 sha256sum > /output/source-checksums.txt
if [ "$BUILD_RECIPE" = vici ]; then
    vici=src/libcharon/plugins/vici/python
    test -d "$vici/vici"
    mkdir -p "$vici/debian"
    cat > "$vici/debian/control" <<'CONTROL'
Source: strongswan
Section: python
Priority: optional
Maintainer: VyOS Package Maintainers <maintainers@vyos.net>
Build-Depends: debhelper (>= 10), dh-python, python3, python3-setuptools
Standards-Version: 3.9.6

Package: python3-vici
Architecture: all
Depends: ${misc:Depends}, ${python3:Depends}
Description: Native Python interface for strongSwan's VICI protocol
CONTROL
    printf '#!/usr/bin/make -f\n%%:\n\tdh $@ --with python3\n' > "$vici/debian/rules"
    chmod +x "$vici/debian/rules"
    printf '10\n' > "$vici/debian/compat"
    printf 'vici /usr/lib/python3/dist-packages/\n' > "$vici/debian/install"
    cp debian/changelog "$vici/debian/changelog"
    cp debian/copyright "$vici/debian/copyright"
    cd "$vici"
fi
if [ "$BUILD_RECIPE" = libyang ]; then
    grep -Fq 'set(LIBYANG_MAJOR_VERSION 3)' CMakeLists.txt
    grep -Fq 'set(LIBYANG_MINOR_VERSION 13)' CMakeLists.txt
    grep -Fq 'set(LIBYANG_MICRO_VERSION 6)' CMakeLists.txt
    cp -a distro/pkg/deb debian
    sed -i 's/{{ version }}/3.13.6/g; s/{{ release }}/1/g' debian/changelog
fi
if [ "$BUILD_RECIPE" = radvd ]; then
    # Same upstream C sources/service as the Stream FPM recipe; generate Debian
    # metadata locally so the seed container needs no Ruby/FPM bootstrap.
    grep -Fq 'AC_INIT(radvd, [2.20])' configure.ac
    mkdir -p debian/source
    cat > debian/control <<'CONTROL'
Source: radvd
Section: net
Priority: optional
Maintainer: VyOS Package Maintainers <maintainers@vyos.net>
Build-Depends: debhelper-compat (= 13), dh-autoreconf, bison, flex, pkg-config, check, libbsd-dev
Standards-Version: 4.6.2

Package: radvd
Architecture: any
Depends: ${shlibs:Depends}, ${misc:Depends}
Description: RADVD router advertisement daemon
CONTROL
    cat > debian/rules <<'RULES'
#!/usr/bin/make -f
%:
	dh $@ --with autoreconf
override_dh_auto_configure:
	dh_auto_configure -- --with-check
override_dh_auto_install:
	install -D -m0755 radvd debian/radvd/usr/sbin/radvd
	install -D -m0644 radvd.service debian/radvd/lib/systemd/system/radvd.service
RULES
    chmod +x debian/rules
    printf '3.0 (native)\n' > debian/source/format
    cp COPYRIGHT debian/copyright
    printf 'radvd (2.20) unstable; urgency=medium\n\n  * Build verified Stream 2026.03 source with Debian metadata.\n\n -- VyOS Package Maintainers <maintainers@vyos.net>  Wed, 18 Mar 2026 00:00:00 +0000\n' > debian/changelog
fi
snapshot_git_import() {
    # The source archive has no .git directory, but upstream lint/version rules
    # use git ls-files/describe. Create deterministic metadata for this snapshot;
    # this is explicitly a local source import, not an upstream Git commit.
    test ! -e .git
    chown -R "$(id -u):$(id -g)" .
    export GIT_AUTHOR_DATE='2026-03-18T00:00:00Z'
    export GIT_COMMITTER_DATE="$GIT_AUTHOR_DATE"
    git init
    git add .
    git -c user.name='michibiki-io source bootstrap' -c user.email='maintainers@michibiki.io' commit --quiet -m 'Import verified Stream source snapshot'
    git tag "vyos/${STREAM_VERSION}-source"
    git rev-parse HEAD > /output/snapshot-git-commit.txt
    # Upstream's pathname-discovery test assumes an input device pathname.
    # Provide a filesystem-only fixture inside this container; never open a
    # host input device. This is not a hardware/driver verification.
    mkdir -p /dev/input
    # Privileged Docker populates host device nodes after configured tmpfs
    # mounts. Hide any such node with a new mount in this container namespace
    # before opening the filesystem-only fixture. Ordinary package containers
    # have no input devices and do not require mount privileges.
    if [[ ( -e /dev/input/event0 || -L /dev/input/event0 ) &&
          ( ! -f /dev/input/event0 || -L /dev/input/event0 ) ]]; then
        mount -t tmpfs -o nodev,nosuid,noexec,size=1m shirube-test-input /dev/input
    fi
    if [[ -e /dev/input/event0 || -L /dev/input/event0 ]]; then
        [[ -f /dev/input/event0 && ! -L /dev/input/event0 ]] || {
            echo 'Refusing to open a real input device as a test fixture' >&2
            exit 1
        }
    fi
    : > /dev/input/event0
    printf 'Filesystem fixture only: /dev/input/event0\n' > /output/test-fixtures.txt
}
apt-get update
# Jobs share a private runtime, while generated dependency metapackages must not
# pin an earlier job's mutually exclusive development headers (for example
# nftables' libedit versus FRR's readline). Keep installed libraries; remove only
# these task-generated metapackages before resolving the next job.
mapfile -t old_build_deps < <(dpkg-query -W -f='${binary:Package}\t${db:Status-Status}\n' 2>/dev/null | awk '$2 == "installed" && $1 ~ /-build-deps$/ {print $1}')
if (( ${#old_build_deps[@]} )); then
    apt-get -o APT::Get::AutomaticRemove=false remove -y "${old_build_deps[@]}"
fi
if [ "$BUILD_RECIPE" = nftables ]; then
    # The signed snapshot's documentation target invokes a2x even though the
    # minimal bootstrap build dependencies do not install its XML toolchain.
    apt-get install -y --no-install-recommends asciidoc-base docbook-xml docbook-xsl xsltproc libxml2-utils
    command -v a2x
fi
if [ -d /local-debs ] && find /local-debs -maxdepth 1 -name '*.deb' -print -quit | grep -q .; then
    apt-get install -y --no-install-recommends /local-debs/*.deb
fi
export DEB_BUILD_OPTIONS=parallel=2
export MAKEFLAGS=-j2
export OPAMJOBS=2
if [ "$BUILD_RECIPE" = kernel ]; then
    kernel_dependencies
    # Remove foreign architecture outputs and the archived build's signing key.
    test "$(make -s kernelversion)" = 6.6.128
    find /stream-build/scripts/package-build/linux-kernel/config /stream-build/scripts/package-build/linux-kernel/patches/kernel -type f -print0 | sort -z | xargs -0 sha256sum > /output/recipe-checksums.txt
    make ARCH="$KERNEL_ARCH" mrproper
    clean_kernel_tools
    rm -f certs/signing_key.pem certs/signing_key.x509
    cfg=/stream-build/scripts/package-build/linux-kernel
    test -f "$cfg/config/$KERNEL_ARCH/vyos_defconfig"
    for patchfile in "$cfg"/patches/kernel/*.patch; do
        if patch --dry-run --reverse -p1 < "$patchfile" >/dev/null 2>&1; then
            echo "Already applied in snapshot: $(basename "$patchfile")"
        elif patch --dry-run -p1 < "$patchfile" >/dev/null 2>&1; then
            patch -p1 < "$patchfile"
        else
            echo "Kernel patch state is ambiguous: $patchfile" >&2
            exit 1
        fi
    done
    cp "$cfg/config/$KERNEL_ARCH/vyos_defconfig" arch/$KERNEL_ARCH/configs/vyos_defconfig
    for fragment in "$cfg"/config/*.config; do
        cat "$fragment" >> arch/$KERNEL_ARCH/configs/vyos_defconfig
    done
    make ARCH="$KERNEL_ARCH" vyos_defconfig
    for required in CONFIG_MODULE_SIG=y CONFIG_MODULE_SIG_FORCE=y CONFIG_MODULE_SIG_ALL=y; do
        grep -qx "$required" .config || { echo 'Mandatory module signing disabled' >&2; exit 1; }
    done
    cp .config /output/kernel.config
    touch .scmversion
    export PATCH_RECEIPT=/output/kernel-tool-patches.sha256
    apply_kernel_tool_fixes
    package_kernel
else
    if [ "$BUILD_RECIPE" = frr ]; then
        export DEB_BUILD_PROFILES='pkg.frr.rtrlib pkg.frr.lua'
    fi
    if [ "$BUILD_RECIPE" = frr ] && [ -f debian/patches/series ]; then
        # The snapshot includes some already-applied FRR patches without a
        # matching quilt stack. Restore a consistent base in this working copy,
        # then let dpkg-source apply the complete series exactly once.
        if [ -e .pc ]; then mv .pc /output/archive-quilt-state; fi
        awk '!/^[[:space:]]*(#|$)/ {print $1}' debian/patches/series | tac | while IFS= read -r name; do
            patchfile="debian/patches/$name"
            if patch --dry-run --reverse --fuzz=0 -p1 < "$patchfile" >/dev/null 2>&1; then
                patch --reverse --fuzz=0 -p1 < "$patchfile"
            elif patch --dry-run --fuzz=0 -p1 < "$patchfile" >/dev/null 2>&1; then
                echo "Not yet applied in snapshot: $name"
            else
                echo "Ambiguous archived FRR patch state: $name" >&2
                exit 1
            fi
        done
    fi
    # Quilt patches can change Build-Depends (e.g. Dropbear adds PAM).
    dpkg-source --before-build .
    mk-build-deps --install --remove --tool 'apt-get -y --no-install-recommends' debian/control
    if [ "$BUILD_RECIPE" = vyos-1x ]; then
        snapshot_git_import
        # Keep upstream build-time tests, but never build the optional on-image
        # smoketest toolkit, which carries fixed VPN/private-key test fixtures.
        export DH_OPTIONS='-Nvyos-1x-smoketest'
    fi
    if [ "$BUILD_RECIPE" = frr ]; then
        dpkg-buildpackage -b -us -uc -Ppkg.frr.rtrlib,pkg.frr.lua
    else
        dpkg-buildpackage -b -us -uc
    fi
fi
if [ "$BUILD_RECIPE" = vici ]; then
    cp ../python3-vici_*.deb /output/
fi
cd /output
found=0
for deb in ./*.deb; do
    [ -f "$deb" ] || continue
    case "$deb" in *-build-deps_*) continue ;; esac
    arch=$(dpkg-deb -f "$deb" Architecture)
    case "$arch" in "$DEB_ARCH"|all) ;; *) echo "Unexpected architecture: $arch" >&2; exit 1 ;; esac
    dpkg-deb -f "$deb" Package Version Architecture
    found=$((found + 1))
done > /output/packages.txt
[ "$found" -gt 0 ]
