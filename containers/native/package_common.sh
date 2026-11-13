#!/bin/bash
# Copyright (c) 2026 michibiki.io; MIT License (see NOTICE).
# Modified 2026-11-08 for native AMD64/ARM64 builds and ephemeral private keys.
DEB_ARCH=$(dpkg --print-architecture)
case "$DEB_ARCH" in amd64) KERNEL_ARCH=x86 ;; arm64) KERNEL_ARCH=arm64 ;; *) exit 1 ;; esac
export DEB_ARCH KERNEL_ARCH

set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
trap 'chown -R "$HOST_UID:$HOST_GID" /output' EXIT
[ "$(dpkg --print-architecture)" = "$DEB_ARCH" ]
if grep -R -E '^[[:space:]]*deb.*packages\.vyos\.net|^[[:space:]]*URIs:.*packages\.vyos\.net' /etc/apt/sources.list /etc/apt/sources.list.d 2>/dev/null; then
    echo 'Private VyOS APT repository configured; refusing bootstrap' >&2
    exit 1
fi
# @EARLY@
mkdir /output/source
cp -a /snapshot/. /output/source/
cd /output/source
find /snapshot -type f -print0 | sort -z | xargs -0 sha256sum > /output/source-checksums.txt
apt-get update
# Jobs share a private runtime, while generated dependency metapackages must not
# pin an earlier job's mutually exclusive development headers (for example
# nftables' libedit versus FRR's readline). Keep installed libraries; remove only
# these task-generated metapackages before resolving the next job.
mapfile -t old_build_deps < <(dpkg-query -W -f='${binary:Package}\t${db:Status-Status}\n' 2>/dev/null | awk '$2 == "installed" && $1 ~ /-build-deps$/ {print $1}')
if (( ${#old_build_deps[@]} )); then
    apt-get -o APT::Get::AutomaticRemove=false remove -y "${old_build_deps[@]}"
fi
if [ -d /local-debs ] && find /local-debs -maxdepth 1 -name '*.deb' -print -quit | grep -q .; then
    apt-get install -y --no-install-recommends /local-debs/*.deb
fi
export DEB_BUILD_OPTIONS=parallel=2
export MAKEFLAGS=-j2
export OPAMJOBS=2
