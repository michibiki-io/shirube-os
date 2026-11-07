# First boot and sanitization

The intended public export is a fresh 50 GiB install, never a reused test disk.
`producer.disks.convert` requires a standalone stopped source disk, produces
compressed QCOW2 and single-file streamOptimized VMDK, inspects format/size/backing
metadata, and runs integrity and byte-content comparisons against the source.
This conversion helper has not yet been exercised on a Shirube OS install.

`producer.sanitize` handles a mounted offline writable merged guest root. Symlinks resolve with guest-root semantics, so an
absolute guest path never follows a host path. It locks installed password credentials, clears saved configuration,
NetBird identity, configuration archives, user history, machine identity and SSH
host keys, and writes a minimal DHCP/architecture-specific console configuration.
It creates a marker for per-instance console enrollment.

Logical deletion is **not** complete disk sanitization. Free filesystem space,
other filesystem copies/partitions, journal and swap data still need secure
erasure/discard plus a complete offline image audit. The helper therefore reports
logical cleanup separately and leaves export sanitization and boot `NOT_RUN`.
No public distribution is permitted on logical cleanup evidence alone.

The proposed guest first-boot unit runs at the local console before normal login
and SSH. A fresh administrator name and matching passwords of at least 16
characters are entered with hidden password input. The generated password hash
goes through stdin to the configuration session; no password/hash appears in
process arguments or diagnostic output. The per-instance configuration is saved,
machine identity/SSH host keys are generated, and the marker is removed only after
successful completion. No reusable image password or fixed authorized key exists.

This is an implementation requiring guest integration and real verification. It
is not yet a demonstrated access path. In particular, verify writable persistence,
VyOS service ordering/configuration-session behavior, console ownership, DHCP and
the default route on each architecture. Generate public exports before tests;
boot disposable copies with fresh NVRAM and no ISO. Demonstrate a fresh
unprovisioned boot, actual login with different per-instance enrollment material,
new machine/SSH identities, and another offline audit of the immutable exports.
Keep test credentials and test disks out of every public asset and source bundle.

The release disk producer now repacks a credential-free root inside its private
QEMU guest, then performs a second offline cleanup with libguestfs, clears the
old ext4 journal, zeroes/discards free space, recreates the journal and checks both
conversions. Fresh-firmware unprovisioned/provisioned tests use disposable
backing overlays and compare new machine/SSH identities. These paths still need
actual native KVM validation before any sanitization/boot gate can be accepted.
