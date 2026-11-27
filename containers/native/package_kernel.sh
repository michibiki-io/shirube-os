# Copyright (c) 2026 michibiki.io; MIT License (see NOTICE).
kernel_dependencies() {
    apt-get install -y --no-install-recommends bc debhelper rsync kmod cpio bison flex libelf-dev libssl-dev dwarves libdw-dev libslang2-dev libtraceevent-dev libpfm4-dev python3-dev python3-setuptools
    /usr/bin/python3 -c 'import setuptools, sys; assert sys.version_info >= (3, 6)'
    /usr/bin/python3-config --embed --ldflags >/dev/null
}
# The source lock supplies the ABI; do not accept an older snapshot or
# a package label that differs from the kernel's own release.
check_kernel_version() {
    test "$SHIRUBE_KERNEL_RELEASE" = "${SHIRUBE_KERNEL_VERSION}-vyos"
    test "$SHIRUBE_KERNEL_PACKAGE_VERSION" = "${SHIRUBE_KERNEL_VERSION}-1"
    test "$(make -s kernelversion)" = "$SHIRUBE_KERNEL_VERSION"
}

apply_kernel_patches() {
    local stream=$1 series=/kernel-patches/linux-6.6.163
    local receipt=$2 patch_receipt=$3 patchfile
    local stream_series=("$stream"/*.patch)
    local repository_series=("$series"/*.patch)
    test "${#stream_series[@]}" -eq 4
    test "${#repository_series[@]}" -eq 2
    # Never infer equivalence from a failed patch application. Match every
    # original recipe and verify the exact upstream DNAT behavior separately.
    (cd "$stream" && sha256sum --check --strict "$series/stream-patches.sha256")
    (cd "$series" && sha256sum --check --strict repository-patches.sha256)
    sha256sum --check --strict "$series/stream-dnat-source.sha256"
    : > "$receipt"
    for patchfile in "$stream"/000{1,2,3}-*.patch; do
        patch --batch --forward --no-backup-if-mismatch --fuzz=0 -p1 < "$patchfile"
        printf 'applied-stream %s\n' "${patchfile##*/}" >> "$receipt"
    done
    printf 'upstream-equivalent v4-0001-nft_ct-Added-nfct_seqadj_ext_add-for-DNAT-ed-conn.patch\n' >> "$receipt"
    sha256sum "${repository_series[@]}" > "$patch_receipt"
    for patchfile in "${repository_series[@]}"; do
        patch --batch --forward --no-backup-if-mismatch --fuzz=0 -p1 < "$patchfile"
        printf 'applied-shirube %s\n' "${patchfile##*/}" >> "$receipt"
    done
    # Check all Stream/local modifications plus the upstream security fixes
    # against reviewed files from the signed stable baseline, after patching.
    sha256sum --check --strict "$series/effective-files.sha256"
    cat "$series/effective-files.sha256" >> "$receipt"
}

package_kernel() {
    # Modified 2026-11-09: generated metadata must identify the distributor,
    # never a transient build-container account or hostname.
    export KBUILD_BUILD_USER=michibiki-io KBUILD_BUILD_HOST=shirube-os
    export DEBFULLNAME=michibiki-io DEBEMAIL=maintainers@michibiki.io
    # perf's wrapper ignores MAKEFLAGS and otherwise selects all online CPUs.
    local compiler=()
    if [ -n "${SHIRUBE_KERNEL_COMPILE_CACHE:-}" ]; then
        compiler=(CC=/src/native/kernel-cc)
    fi
    make "${compiler[@]}" ARCH="$KERNEL_ARCH" -j2 bindeb-pkg JOBS=2 PYTHON=/usr/bin/python3 PYTHON_CONFIG=/usr/bin/python3-config BUILD_TOOLS=1 LOCALVERSION=-vyos KDEB_PKGVERSION="$SHIRUBE_KERNEL_PACKAGE_VERSION" KDEB_CHANGELOG_DIST=unstable
    # The snapshot's perf packaging hardcodes only three dependencies, despite
    # linking libpython/libdw/libcrypto/etc. Derive runtime dependencies from the
    # actual native binary so installation cannot silently omit required libs.
    local deps
    deps=$(dpkg-shlibdeps -O -e debian/linux-perf/bin/perf)
    deps=${deps#shlibs:Depends=}
    test -n "$deps"
    dpkg-gencontrol -p"linux-perf-$SHIRUBE_KERNEL_RELEASE" -Pdebian/linux-perf "-DDepends=$deps"
    dpkg-deb --build debian/linux-perf ..
    test "$(cat include/config/kernel.release)" = "$SHIRUBE_KERNEL_RELEASE"
    test -s certs/signing_key.pem
    test -s certs/signing_key.x509
    local name deb
    for name in "linux-image-$SHIRUBE_KERNEL_RELEASE" "linux-headers-$SHIRUBE_KERNEL_RELEASE" \
            "linux-perf-$SHIRUBE_KERNEL_RELEASE" linux-libc-dev; do
        deb="../${name}_${SHIRUBE_KERNEL_PACKAGE_VERSION}_${DEB_ARCH}.deb"
        test "$(dpkg-deb -f "$deb" Package)" = "$name"
        test "$(dpkg-deb -f "$deb" Version)" = "$SHIRUBE_KERNEL_PACKAGE_VERSION"
        test "$(dpkg-deb -f "$deb" Architecture)" = "$DEB_ARCH"
    done
}

# @COMMON@
    check_kernel_version
    kernel_dependencies
    cfg=/stream-build/scripts/package-build/linux-kernel
    find "$cfg/config" "$cfg/patches/kernel" -type f -print0 | sort -z | xargs -0 sha256sum > /output/recipe-checksums.txt
    make ARCH="$KERNEL_ARCH" mrproper
    # The clean upstream tree has no old kernel keys. Never accept an
    # alternate key path from the retained recipes or a cache.
    test ! -e certs/signing_key.pem
    test ! -e certs/signing_key.x509
    test -f "$cfg/config/$KERNEL_ARCH/vyos_defconfig"
    apply_kernel_patches "$cfg/patches/kernel" /output/kernel-patch-state.txt /output/kernel-tool-patches.sha256
    cp "$cfg/config/$KERNEL_ARCH/vyos_defconfig" arch/$KERNEL_ARCH/configs/vyos_defconfig
    for fragment in "$cfg"/config/*.config; do
        cat "$fragment" >> arch/$KERNEL_ARCH/configs/vyos_defconfig
    done
    make ARCH="$KERNEL_ARCH" vyos_defconfig
    for required in CONFIG_MODULE_SIG=y CONFIG_MODULE_SIG_FORCE=y CONFIG_MODULE_SIG_ALL=y \
            CONFIG_MODVERSIONS=y CONFIG_MODULE_SIG_KEY_TYPE_RSA=y 'CONFIG_MODULE_SIG_HASH="sha512"' \
            'CONFIG_MODULE_SIG_KEY="certs/signing_key.pem"' 'CONFIG_SYSTEM_TRUSTED_KEYS=""'; do
        grep -qx "$required" .config || { echo 'Mandatory module signing disabled' >&2; exit 1; }
    done
    cp .config /output/kernel.config
    touch .scmversion
    test "$(make -s ARCH="$KERNEL_ARCH" LOCALVERSION=-vyos kernelrelease)" = "$SHIRUBE_KERNEL_RELEASE"
    PYTHONPATH=/src python3 -m producer.kernel_compile_cache --root "$PWD" --architecture "$DEB_ARCH"
    package_kernel
# @VALIDATE@
