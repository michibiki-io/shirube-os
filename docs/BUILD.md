# Build inputs and native runners

The initial OS baseline is official VyOS Stream 2026.03. `source-lock.json` records
the exact archive, SHA256, minisign key and its official public authority:
https://vyos.net/get/stream/. A checksum mismatch fails before signature validation;
a signature failure produces no successful verification receipt. No replacement
baseline or private package repository fallback is allowed.

Source verification is an independent container stage:

```sh
docker build -f containers/verify/Dockerfile -t shirube-verify .
mkdir -p .work
docker run --rm -v "$PWD/.work:/output" shirube-verify \
  fetch-stream --destination /output/verified-stream
```

For ARM64, pass `--platform linux/arm64` and the ARM64 `BASE` digest recorded in
`source-lock.json`. Use a native Docker daemon. The default Dockerfile base is
AMD64; do not mistake container emulation for a native ARM64 build.

The package-stage entry point is `scripts/build-packages.sh ARCH NEW_OUTPUT`.
Its catalog includes core/network/routing packages and the kernel. It is **not**
the complete ISO runtime package closure. The kernel configuration maps Debian
AMD64 to upstream `config/x86`, and ARM64 to `config/arm64`. Module signing stays
mandatory and archived kernel signing keys are removed before rebuilding.
Foreign runtime ELF files and modules without matching signatures fail inspection.
The Linux stable perf patch retains its original attribution and GPL license.

Native toolchain inputs use architecture-specific Debian image digests and fixed
Debian/security snapshot timestamps. APT authenticates repository signatures and
package hashes; HTTP is used to bootstrap CA certificates. The compiler/toolchain
runs in a container. OPAM uses a pinned public repository revision and OCaml
4.14.2. Python pyhumps uses a fixed wheel hash. No host compiler, libraries or
QEMU installation is required by the package-stage wrapper.

The wrapper bounds runtime compilation to two CPUs and 8 GiB memory without swap
expansion. Compilation parallelism is two. Private build work stays inside its
own disposable container. Only the new task output directory is mounted. It
never mounts host credentials, the Docker socket or sibling checkouts. It never
prunes shared Docker state or stops unrelated workloads.

Runner policy accepts `ACTIONS_RUNNER_AMD64` and `ACTIONS_RUNNER_ARM64`, individually
set or unset. Defaults are `ubuntu-24.04` and `ubuntu-24.04-arm`. A value may be a
single label or JSON label array, for example `["self-hosted","linux","ARM64"]`.
Labels are preserved exactly: an offline selected runner stays queued. There is
no provider/architecture fallback. Native host and actual package/binary
architecture checks are separate from label selection.

A conservative complete-build preflight currently requires 160 GiB free storage
and 8 GiB available memory. This is a planning budget, not an observed minimum.
KVM is a separate boot-stage requirement. TCG has not been validated and cannot
satisfy the boot gate. The hosted runner defaults are selection defaults, not
claims that their available resources or nested virtualization meet these needs.

An explicit experimental TCG live-ISO probe is available in
`scripts/test-iso-console.py`, executed with the native toolchain container.
It uses two virtual CPUs, 2 GiB guest RAM and a 1200-second boot timeout,
fresh firmware and a new temporary disk. It checks console enrollment/access
and a live default route. It has not yet been exercised against a completed ISO
and does not relax the release KVM requirement or establish final-disk acceptance.

The FPM bootstrap installs all ten gems from
`containers/native/rubygems.lock.json`, verifying their public archive hashes
before installing locally with dependency resolution disabled. The native
toolchain containing that locked bootstrap has been built on AMD64.

The full workflow calls `scripts/build-release.sh` for each selected native runner.
It requires the exact clean merged source commit and runs the ISO, fresh-install,
sanitization, conversion, source and publication gates in containers. Outputs use
fresh run/attempt-specific task directories; incomplete candidates are not staged.
The AMD64 virtual profile omits vendor-only physical NIC/QAT packages; this
difference is explicit in the source lock. Physical hardware parity is not claimed.

Remaining producer validation, complete toolchain/source closure and real
per-architecture verification are listed in `VALIDATION.md`. Toolchain image
creation, package compilation, ISO assembly, installed-disk boot and OCI execution
are different milestones.

## ISO-first execution

`scripts/build-iso.sh` builds an ISO independently of VM validation. It needs
`SHIRUBE_ARCHITECTURE`, `SHIRUBE_VERSION`, `SHIRUBE_SOURCE_COMMIT` and a new
`SHIRUBE_ISO_DIRECTORY`. An optional `SHIRUBE_SOURCE_CACHE` contains only the
public archive/signature and is reverified inside the container. It never accepts
an existing test disk. The ISO-only admission budget is 40 GiB free storage and
8 GiB available memory; the task stops if host headroom falls below 12 GiB disk
or 2 GiB memory. These are conservative operating budgets, not validated minimums.
An ISO build receipt leaves local boot, OCI and distribution clearance unverified.

Set `SHIRUBE_INCLUDE_SOURCES=1` for source/license collection immediately after
ISO assembly, using the same private build tree and exact project archive. This
path requires no KVM and preserves the ISO receipt if collection fails. A
successful collection records inventory/archive names and individual license
clearance separately; complete source reconstruction remains `NOT_RUN` until
demonstrated. The optional audit has not yet run on a completed ISO. It does not
clear binary publication or replace installed-disk tests.

An optional `python-inputs-amd64` or `python-inputs-arm64` subdirectory in that
public-input cache holds the pinned HTTP API wheels/source archives. Each file
is rechecked against the architecture-specific lock before use. It contains no
build-internal keys or guest state and is mounted read-only. The dependency
environment can be packaged offline from this complete verified input set.

Sequential package jobs remove only their generated dependency metapackages
before resolving the next job; libraries are retained unless the next job's
dependency resolver replaces conflicting development headers. This prevents
one job's libedit/readline constraints from blocking a later routing build.

Core build-time lint/unit tests still run. The optional `vyos-1x-smoketest`
on-image toolkit is excluded from Debian package generation because it contains
fixed private-key/VPN test fixtures. Generating that forbidden package or finding
it in the assembled filesystem package list fails the producer. The ordinary
private-key payload audit remains unchanged for every generated runtime package.

After verifying the entire public Stream archive, the producer extracts only
the nested `vyos_tarballs.tar.gz` needed by the declared recipes. Unused outer
source bundles are not written into the build container. The preparation receipt
records both archive hashes, the exact selected member and the generated-output
exclusions applied inside it. This selection was checked against the actual
pinned public input; it does not establish complete corresponding-source closure.

VPP's DPDK Meson bootstrap is preseeded from
`containers/native/vpp-python.lock.json`: exact public Meson, setuptools, wheel,
packaging and pyelftools archives. All hashes are verified before the upstream
recipe runs. pip index access and persistent pip caching are disabled for this
stage, so an unavailable fixed input fails rather than resolving a newer one.
The fixed inputs have passed an actual cache-disabled, network-disabled Meson
bootstrap check on AMD64; the full modified VPP recipe still requires integrated
build validation.

Public VPP patch application preserves upstream authors and fixes the local
preparer identity/timestamp. This avoids introducing host identity or wall-clock
changes into the generated local patch commit identifiers. These local preparation
commits are not described as upstream commits.

The core pathname-discovery test uses a regular filesystem-only dummy at
`/dev/input/event0`. A privileged Docker runtime can populate real device nodes
after its configured tmpfs mounts. Before opening the dummy, the recipe detects
non-regular/link entries and hides them with a fresh nodev tmpfs inside the
container namespace, then verifies that only a regular file is used. Failure
to isolate the path stops the build. No host input device is opened for this test.

## Runtime component build inputs

OWAMP uses the source-built, pinned I2util development package and executes
its daemon tests as an unprivileged build user with normal fakeroot packaging.
Kea uses its own hash-pinned Meson 1.9.2 source, separately from VPP's Meson.
The tool source is retained with Kea's corresponding build inputs.

Node exporter 1.9.1 uses the upstream `nobtrfs` tag on both architectures,
excluding Btrfs metrics because the exact optional ioctl dependency lacks a
permission notice. Other collectors remain available. Telegraf stays at 1.28.3
with all plugins; RCON is fixed to the upstream MIT-license-only commit recorded
in `source-lock.json`, which leaves its two Go files unchanged. Kinesis parent
license and NOTICE files are pinned to the exact Go submodule commit. Embedded
README license notices are retained only through explicit file/hash mappings.

Each packaged Go runtime retains its actual linked module sources, upstream
notices and compiler/standard-library source. The collector checks these against
the installed inventory, executable hash and actual Go build information;
missing dependency clearance blocks release. Source retention does not itself
establish license clearance or a completed whole-OS reconstruction.

For a local ISO investigation, `SHIRUBE_PRESERVE_FAILED_BUILD=1` together with a
new `SHIRUBE_BUILD_STATE_FILE` preserves the stopped, task-owned container and
its private inputs after failure. This avoids discarding diagnostic compiler
outputs. It is opt-in, is not enabled by release automation, and does not turn
failed or older-source output into an accepted candidate. The retained container
may contain build signing keys and must stay private; remove that exact owned
container and its private inputs when diagnosis is complete.

## Release duration and runner limits

Complete clean-build and repeat-build durations have not yet been established.
AMD64 and ARM64 candidates run in parallel; publication waits for both complete
candidates and their source, disk and runtime gates. Individual package results
and diagnostic retries do not establish a production release duration.

The job deadline is 360 minutes for default or other labels, and 720 minutes
when the selected label array explicitly includes `self-hosted`. GitHub-hosted
runners have a six-hour job execution limit ([GitHub Actions limits](https://docs.github.com/en/actions/reference/limits)).
These deadlines are bounds, not measured build-time estimates. Explicit labels
and preflight checks must still establish sufficient native resources and usable
virtualization; default runner selection does not establish release support.
