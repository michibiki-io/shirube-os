# Copyright (c) 2026 michibiki.io; MIT License (see NOTICE).
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
