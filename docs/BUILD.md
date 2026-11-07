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
