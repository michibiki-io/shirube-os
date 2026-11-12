<p align="center"><img src="shirube-os-logo.svg" alt="Shirube OS" width="412"></p>

# Shirube OS

An open-source router OS based on VyOS Stream, built and maintained by michibiki-io.

Shirube OS is an independent, unaffiliated distribution. VyOS and other upstream
projects retain their copyrights, licenses and compatibility interfaces.

**[v2026.03.8](https://github.com/michibiki-io/shirube-os/releases/tag/v2026.03.8)
was published on 2026-11-12.** Native images and
corresponding-source sets have been produced and tested. Exact artifact hashes,
passed/failed checks and current reviews are recorded in
[PR #2](https://github.com/michibiki-io/shirube-os/pull/2).
Static validation alone does not establish OS acceptance. See
[validation status](docs/VALIDATION.md) for evidence scope and historical results.

- [Staged hosted build and acceptance gaps](docs/STAGED-BUILD.md)
- [Build inputs and native runners](docs/BUILD.md)
- [Release contract and recovery](docs/RELEASE.md)
- [First boot and sanitization](docs/FIRST_BOOT.md)
- [NetBird integration](docs/NETBIRD.md)
- [Licenses and corresponding sources](docs/LICENSES.md)

Run safe static checks with `python3 -m unittest discover -s tests -v`.
Build tools, firmware and QEMU belong in containers. No cloud upload, image import
or deployment is provided by this repository.

Release versions follow the pinned Stream baseline: `2026.03` → `v2026.03.0`,
then `v2026.03.1` for the next Shirube revision. A new Stream baseline `2026.04`
starts `v2026.04.0`.

リリース版は固定したStream版に合わせます。`2026.03`の初回は`v2026.03.0`、
同じStream内の次の修正版は`v2026.03.1`、Streamを`2026.04`へ更新した初回は
`v2026.04.0`です。
