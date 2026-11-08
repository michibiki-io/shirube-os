ARG BASE=debian@sha256:c67e92c8099ba22853ab4cfda7cfb494d253d6012ca82dee98ba8976772af018
FROM ${BASE} AS toolchain
ARG TARGETARCH
ENV DEBIAN_FRONTEND=noninteractive OPAMJOBS=2 MAKEFLAGS=-j2 GOMAXPROCS=2
# APT verifies signed metadata and package hashes. HTTP bootstraps CA certificates.
RUN rm -f /etc/apt/sources.list.d/debian.sources && \
    echo 'deb [check-valid-until=no] http://snapshot.debian.org/archive/debian/20261105T000000Z/ bookworm main' > /etc/apt/sources.list && \
    echo 'deb-src [check-valid-until=no] http://snapshot.debian.org/archive/debian/20261105T000000Z/ bookworm main' >> /etc/apt/sources.list && \
    echo 'deb [check-valid-until=no] http://snapshot.debian.org/archive/debian/20261105T000000Z/ bookworm-updates main' >> /etc/apt/sources.list && \
    echo 'deb [check-valid-until=no] http://snapshot.debian.org/archive/debian-security/20261105T000000Z/ bookworm-security main' >> /etc/apt/sources.list && \
    echo 'deb-src [check-valid-until=no] http://snapshot.debian.org/archive/debian-security/20261105T000000Z/ bookworm-security main' >> /etc/apt/sources.list && \
    apt-get -o APT::Update::Error-Mode=any update && apt-get install -y --no-install-recommends \
      ca-certificates curl minisign git python3 python3-debian python3-setuptools python3-all \
      python3-pip python3-stdeb python3-lxml python3-jinja2 python3-pystache python3-git python3-tomli \
      python3-tomli-w python3-yaml python3-jsonschema python3-pexpect \
      build-essential devscripts equivs debhelper dh-python dh-autoreconf fakeroot \
      libtool pkg-config quilt bc rsync kmod cpio bison flex libelf-dev libssl-dev \
      dwarves libdw-dev libslang2-dev libtraceevent-dev libpfm4-dev python3-dev \
      cmake libffi-dev libpcre2-dev libzmq3-dev opam gnupg apt-utils squashfs-tools \
      xorriso mtools debootstrap live-build pbuilder dosfstools gdisk kpartx parted udev sudo \
      librsvg2-bin qemu-utils qemu-system-x86 qemu-system-arm ovmf qemu-efi-aarch64 \
      liblua5.3-dev libpcre3-dev libnl-genl-3-dev libsnmp-dev libxtables-dev \
      libcap-dev libunwind-dev llvm zstd locales && \
    rm -rf /var/lib/apt/lists/*
RUN echo 'pyhumps==3.8.0 --hash=sha256:060e1954d9069f428232a1adda165db0b9d8dfdce1d265d36df7fbff540acfd6' > /tmp/python.lock && \
    pip install --break-system-packages --no-deps --only-binary=:all: --require-hashes -r /tmp/python.lock
RUN opam init --root=/opt/opam --bare --disable-sandboxing --no-setup default \
      git+https://github.com/ocaml/opam-repository.git#d74e2449deccd679e0a28814bfc75ba47656a7b2 && \
    opam switch create --root=/opt/opam --jobs=2 4.14.2 && \
    opam install --root=/opt/opam --jobs=2 -y re pcre2 num ctypes ctypes-foreign ctypes-build containers fileutils xml-light
COPY containers/native/install_gems.py containers/native/rubygems.lock.json /tmp/shirube-gems/
RUN apt-get -o APT::Update::Error-Mode=any update && apt-get install -y --no-install-recommends \
      python3-guestfs openssh-client ruby ruby-dev libgdbm-dev liblzma-dev libffi-dev libpcap0.8-dev clang \
      libseccomp-dev libgpgme-dev libapparmor-dev go-md2man libguestfs-tools \
      python3-inotify python3-xmltodict python3-nose2 python3-netifaces python3-jmespath \
      python3-psutil python3-netaddr python3-paramiko python3-passlib python3-tabulate \
      python3-zmq python3-fastapi python3-pyudev python3-systemd python3-dbus \
      python3-pyroute2 python3-voluptuous python3-cracklib python3-pam python3-certbot-nginx \
      whois pylint libffi-dev libpcre2-dev && \
    python3 /tmp/shirube-gems/install_gems.py /tmp/shirube-gems/rubygems.lock.json && \
    rm -rf /tmp/shirube-gems
RUN case "$TARGETARCH" in \
      amd64) digest=542d3c1705f1c6a1c5a80d5dc62e2e45171af291e755d591c5e6531ef63b454e ;; \
      arm64) digest=f626cdd92fc21a88b31c1251f419c17782933a42903db87a174ce74eeecc66a9 ;; \
      *) exit 1 ;; esac && \
    curl --fail --location --output /tmp/go.tar.gz "https://go.dev/dl/go1.23.2.linux-$TARGETARCH.tar.gz" && \
    echo "$digest  /tmp/go.tar.gz" | sha256sum -c - && tar -C /opt -xzf /tmp/go.tar.gz && rm /tmp/go.tar.gz
RUN pip install --break-system-packages --no-deps --no-build-isolation git+https://github.com/aristanetworks/j2lint.git@341b5d5db86e095b622f09770cb6367a1583620e
ENV PATH=/opt/go/bin:$PATH GOTOOLCHAIN=local
RUN printf '#!/bin/sh\nexit 101\n' > /usr/sbin/policy-rc.d && chmod 755 /usr/sbin/policy-rc.d
WORKDIR /src
FROM toolchain AS producer
# Source collection must cover every suite used by the ISO. Keep source-only
# indexes outside the compiler stage so this cannot invalidate library caches.
RUN printf '%s\n' \
    'deb-src [check-valid-until=no] http://snapshot.debian.org/archive/debian/20261105T000000Z/ bookworm-updates main' \
    'deb-src [check-valid-until=no] http://snapshot.debian.org/archive/debian/20261105T000000Z/ bookworm-backports main' \
    >> /etc/apt/sources.list && \
    apt-get -o APT::Update::Error-Mode=any update && \
    apt-get install -y --no-install-recommends initramfs-tools-core && \
    mkdir -p /lib/modules && \
    rm -rf /var/lib/apt/lists/*
COPY producer /src/producer
COPY guest /src/guest
COPY containers/native /src/native
COPY patches/kernel /kernel-patches
COPY LICENSE NOTICE source-lock.json license-audit.json shirube-os-logo.svg /src/
# Keys and source archives are acquired/generated only during docker run.
ENTRYPOINT ["python3", "/src/native/build.py"]
