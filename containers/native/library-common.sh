#!/bin/bash
# Copyright (c) 2026 michibiki.io; MIT License (see NOTICE).
# Library-only cached recipe. Kernel/core and signing are deliberately separate.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
mkdir -p /output/source
cp -a /snapshot/. /output/source/
cd /output/source
apt-get update
if compgen -G '/local-debs/*.deb' >/dev/null; then
    apt-get install -y --no-install-recommends /local-debs/*.deb
fi
BUILD_FLAGS=()
source /prepare.sh
dpkg-source --before-build .
mk-build-deps --install --remove --tool 'apt-get -y --no-install-recommends' debian/control
dpkg-buildpackage -b -us -uc "${BUILD_FLAGS[@]}"
