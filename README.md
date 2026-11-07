<p align="center"><img src="shirube-os-logo.svg" alt="Shirube OS" width="412"></p>

# Shirube OS

An open-source router OS based on VyOS Stream, built and maintained by michibiki-io.

Shirube OS is an independent, unaffiliated distribution. VyOS and other upstream
projects retain their copyrights, licenses and compatibility interfaces.

**Implementation in progress. There is no validated Shirube OS ISO or public disk
release yet.** The merged-PR release workflow and producer are implemented but still require
full acceptance evidence. Input verification, package-stage results and static validation do
not establish a successful OS build or boot. See [validation status](docs/VALIDATION.md).

- [Build inputs and native runners](docs/BUILD.md)
- [Release contract and recovery](docs/RELEASE.md)
- [First boot and sanitization](docs/FIRST_BOOT.md)
- [NetBird integration](docs/NETBIRD.md)
- [Licenses and corresponding sources](docs/LICENSES.md)

Run safe static checks with `python3 -m unittest discover -s tests -v`.
Build tools, firmware and QEMU belong in containers. No cloud upload, image import
or deployment is provided by this repository.
