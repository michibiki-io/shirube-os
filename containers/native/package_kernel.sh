# Copyright (c) 2026 michibiki.io; MIT License (see NOTICE).
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
    # Modified 2026-11-09: generated metadata must identify the distributor,
    # never a transient build-container account or hostname.
    export KBUILD_BUILD_USER=michibiki-io KBUILD_BUILD_HOST=shirube-os
    export DEBFULLNAME=michibiki-io DEBEMAIL=maintainers@michibiki.io
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

# @COMMON@
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
# @VALIDATE@
