# First boot and sanitization

## Initialization methods

The image supports upstream-native cloud-init and local console enrollment.
The pinned public VyOS cloud-init fork retains `vyos_config_commands` and
`write_files`. NoCloud/config-drive data can supply instance-specific users and
SSH keys. The default cloud account name is `shirube`; no account receives a
distribution password. Without usable cloud initialization, the console asks
for a new administrator and a password of at least 16 characters.

Cloud configuration runs before the native router loads its configuration.
After the router has applied it, enrollment verifies a real saved administrator
with password or SSH-key authentication. Successful cloud configuration with a
usable administrator enables normal login without asking for another account.
Saved enrollment is checked again after installation; SSH-only administrators
retain their configured keys. Instance state remains persistent at runtime and
is erased when producing a new distribution image.
After provisioning, booting without a metadata source preserves the saved native
configuration. The cloud-init handler must not replace a saved hostname or
network configuration with factory defaults merely because metadata is absent.
The persistence test ejects its metadata seed before rebooting.

For persistent `write_files` inputs, use `/opt/vyatta/etc/config/` (for example,
its `scripts/` directory). The later `/config` bind mount is not yet present
when cloud-init writes files. This follows the
[upstream VyOS cloud-init interface](https://docs.vyos.io/en/1.5/automation/cloud-init.html).
cloud-initで永続ファイルを配置する際は、上流と同じく
`/opt/vyatta/etc/config/`配下を使用します。この段階では`/config`は未マウントです。

The release gate must exercise metadata keys, user-data keys, native commands,
`write_files`, password-free SSH access and reboot persistence on disposable
copies of both disk formats on both architectures. Console fallback remains a
separate required check. A prior console-only result does not certify cloud-init.
Local cloud-init tests do not certify OCI execution.

AMD64 installation media supports both BIOS and UEFI. The producer separately
installs that same ISO and boots the resulting disk under each firmware.
ARM64 uses UEFI. The published QCOW2/VMDK export profile uses UEFI on both
architectures; ISO BIOS support does not imply a BIOS-based OCI profile.

cloud-initとコンソール初期化の両方に対応します。cloud-initでは上流の
`vyos_config_commands`と`write_files`を維持し、ユーザーとSSH鍵を個別に設定できます。
既定のクラウド用ユーザー名は`shirube`で、配布用の共通パスワードはありません。
設定済みの管理者を確認できた場合は追加のコンソール登録を要求せず、
初期化データがない場合はコンソールで管理者を作成します。
両アーキテクチャ・両ディスク形式で鍵によるアクセスと再起動後の保持を検証します。
AMD64の同じISOでBIOSとUEFIを検証し、ARM64と配布用ディスクの基本構成はUEFIとします。
ローカル試験の成功をOCI実機試験の成功とは扱いません。

The intended public export is a fresh 50 GiB install, never a reused test disk.
`producer.disks.convert` requires a standalone stopped source disk, produces
compressed QCOW2 and single-file streamOptimized VMDK, inspects format/size/backing
metadata, and runs integrity and byte-content comparisons against the source.

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

The guest first-boot unit runs after native configuration has loaded, before
normal console login. For console enrollment, A fresh administrator name and matching passwords of at least 16
characters are entered with hidden password input. The generated password hash
goes through stdin to the configuration session; no password/hash appears in
process arguments or diagnostic output. The per-instance configuration is saved,
machine identity/SSH host keys are generated, and the marker is removed only after
successful completion. No reusable image password or fixed authorized key exists.

Each candidate requires actual verification of writable persistence, native
service ordering, console ownership, DHCP and the default route. Generate public
exports before tests; boot disposable copies with fresh firmware state and no
installation ISO. Verify cloud-init and console access, new identities and
another offline audit of the immutable exports. Keep all test credentials and
test disks out of public assets and source bundles.

The release disk producer now repacks a credential-free root inside its private
QEMU guest, then performs a second offline cleanup with libguestfs, clears the
old ext4 journal, zeroes/discards free space, recreates the journal and checks both
conversions. Fresh-firmware unprovisioned/provisioned tests use disposable
backing overlays and compare new machine/SSH identities. Each candidate needs actual
boot evidence with its explicitly selected KVM or TCG accelerator before these
gates can be accepted.

## Distribution disk preparation

The producer installs into a new disposable disk, confirms access with the
originally enrolled administrator and shuts the guest down cleanly. Offline
inspection requires the installed immutable SquashFS to match the input ISO
by SHA256 before deleting its runtime overlay. The factory filesystem is then
sanitized and audited, and the ext4 journal/free space are cleared before
conversion. Existing test disks are never distribution inputs. Tests boot
separate overlays with fresh firmware; the exported files remain unchanged.

配布用には新規ディスクへインストールし、最初の管理者でアクセスを確認して正常停止します。
インストール済みの読み取り専用OS領域がISOとSHA256で一致することを確認してから、
実行時の書き込み領域をオフラインで削除します。工場出荷用ファイルシステムの消去・監査と
journal／空き領域の消去後に変換します。既存の試験ディスクは配布用に転用しません。
起動試験は新しいfirmwareと別の書き込み領域を使用し、配布ファイルを変更しません。
