ARG BASE=debian@sha256:c67e92c8099ba22853ab4cfda7cfb494d253d6012ca82dee98ba8976772af018
FROM ${BASE}
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
      xorriso mtools debootstrap live-build dosfstools gdisk parted udev sudo \
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
WORKDIR /src
COPY producer /src/producer
COPY containers/native /src/native
COPY patches/kernel /kernel-patches
COPY source-lock.json shirube-os-logo.svg /src/
# Keys and source archives are acquired/generated only during docker run.
ENTRYPOINT ["python3", "/src/native/build.py"]
