# NetBird client integration

The initial headless client baseline is v0.82.0. Both public release asset hashes
are fixed in `source-lock.json`, verified against the upstream checksums file.
The persistent configuration interface contains only `management-url` (HTTPS)
and `disable`. There is no persistent `setup-key` node.

The `service netbird` XML and commit handler are under `guest/`. The lifecycle
controller is `producer/netbird.py`; intended installation names are
`/usr/libexec/shirube/netbird_control.py` for the imported module and
`/usr/libexec/shirube/netbird-control` for its executable entry point. The pinned
client belongs at `/usr/libexec/shirube/netbird`. The integration package generates templates/cache with the pinned Stream
compiler and registers its configuration handler. Native ARM64 diagnostic QCOW2 guest checks have passed the lifecycle below; AMD64 and both-format acceptance remain incomplete.

Use the OS configuration CLI for persistent service settings:

```text
configure
set service netbird management-url 'https://api.netbird.io:443'
commit
save
exit
generate netbird enrollment
show netbird
```

`generate netbird enrollment` prompts for a one-time setup key without echoing
it or adding it to command history. From configuration mode use
`run generate netbird enrollment`. Disable with `set service netbird disable`,
then `commit` and `save`; re-enable with `delete service netbird disable`,
then `commit` and `save`. Delete the entire `service netbird` tree and commit/save
to remove the persistent service settings.

Without configuration the client remains inactive. An enabled configuration
creates non-secret runtime settings. Enrollment is a separate privileged action
through the OS operational CLI. It reads a hidden prompt or
stdin from a secure provider. Never put the key in a shell command, environment
assignment, saved configuration or script history. The controller writes a
0600 task file only under `/run`, passes its filename to the client, suppresses
client output and deletes it on success, failure or timeout. Enrollment settings
and receipts contain no setup key. Runtime directory cleanup is also provided by
the `/run` lifecycle after abrupt machine loss.

Peer state lives under `/config/netbird`. Disable stops the daemon and retains
identity. Re-enable/reboot starts an enrolled peer for the same management URL.
Changing management servers requires explicit fresh enrollment; an old receipt
cannot silently authorize a different server. Removing the configuration stops
the service; state is retained until explicitly erased. Image upgrades must
preserve the persistent config area, including peer state, while public base
images must contain none of that state.

Unit tests cover default inactivity, disabling with identity retention, restoration
of saved settings after volatile runtime loss, rejection of an old enrollment
receipt for a changed server, persistent setup-key rejection, and one-shot
erasure on success/failure/timeout. These use mocked service operations. Actual
client enrollment, reconnection, save/reboot, upgrade compatibility and both
architectures still require guest runtime evidence. Unit mocks do not establish
those results.

The configuration and operational XML are compiled with the pinned upstream
schema and template compilers. Configuration changes run through the native
configuration handler and configuration daemon; boot restores saved non-secret
settings through the same handler. The setup key is intentionally excluded from
the saved configuration to satisfy the one-time enrollment contract.

## Container client verification

`scripts/test-netbird-container.py` runs the real pinned headless client against
a digest-pinned, temporary local management/signal server. Supply the native
toolchain image ID with `--helper-image`, `--architecture amd64` or `arm64`, and a
new `--work` directory. It requires Docker and the task's TUN device; compilers
and test dependencies remain inside containers. No external account, deployment
credential or cloud operation is used.

The test checks actual registration, management/signal connectivity, identity
retention after daemon/container restart, and absence of the one-use setup key
from persistent client state and its error log. The management server runs on
an internal Docker network with no published host ports. Admin credentials,
setup keys and the temporary CA private key are task-private and removed on
exit; cleanup verifies ownership labels before deleting resources.

This is preliminary client verification. Its evidence explicitly leaves guest
configuration commit/save/reboot and image acceptance as `NOT_RUN`. Those tests
must still run through the actual OS CLI on both architecture-specific images.

## Installed guest lifecycle verification

`scripts/test-netbird-guest.py` is the separate installed-OS test orchestrator.
Supply an immutable native toolchain image built from the current checkout,
`--disk` with a standalone, unprovisioned 50 GiB QCOW2, `--version`,
`--architecture`, and a new private `--work` directory. KVM is the default;
explicit `--accelerator tcg` is an experimental component test only and does
not change release acceleration requirements.

The harness starts a local isolated management fixture and boots a new overlay
with fresh firmware and instance credentials. It uses the native configuration
CLI for set/commit/save and the operational hidden prompt for registration. It
first attempts an unregistered key and requires failure, temporary-key erasure,
inactivity and absence of that input from configuration/state/history/logs.
It then checks successful enrollment, management/signal connectivity, disable/enable identity retention,
saved settings after reboot, key absence from state/config/history/logs and
inactivity after configuration removal and another reboot. Fixture keys enter
only through private stdin and the guest's hidden prompt. The original disk is
read-only and its hash is checked again. Temporary peer disks, firmware, CA
private key and management state are removed; only component evidence remains.

The native ARM64 sanitized diagnostic QCOW2 passed real registration/rejection, native commit/save, disable/enable, saved reboot identity, secret scans and removal/reboot inactivity. This component result does not clear the complete distribution or substitute for AMD64/VMDK evidence. The full publication gate remains closed.

The full `build-release.sh` path starts the same ephemeral fixture through
`run-release-container.py`. It passes only the public CA as a file and the
temporary authorization through private stdin, consumed before any build
subprocess starts. Each provisioned QCOW2/VMDK test clone requests its own
one-use key at test time, so an hours-long build cannot consume the key's
enrollment lifetime. Both clones must complete the native guest checks before the producer can record a NetBird `PASS`. Complete clean-head candidates and both-architecture/formats acceptance remain required.

## Component source reconstruction

`containers/native/rebuild_netbird.py` prepares the exact pinned NetBird source,
the verified Go 1.26.10 compiler/source archives, and vendored dependency sources.
Its `prepare` phase fails if vendoring changes the pinned `go.mod` or `go.sum`.
The `offline` phase uses a fresh compiler/module cache, vendored inputs and
disabled module fetching. Run this phase in a network-disabled native toolchain
container. It builds the headless client and checks its actual version and ELF
architecture; the AMD64 prepare/offline builds have passed.

This is a component reconstruction proof. It neither establishes complete OS
source coverage nor clears NetBird's linked-dependency licenses. The upstream
published client remains the pinned ISO input; its source commit and compiler
were inspected from its build information. Identical release bytes are not
claimed for a reconstructed client or complete OS.

The producer retains the exact source ZIP and notices of every dependency linked
by the verified reference client, checking its embedded Go h1 identities. The
Go compiler/source archives and generated vendor declarations are also pinned.
Retained vendor files are checked byte-for-byte against those original ZIPs.
The producer restores a new source/SDK tree from these materials and compiles
it with a fresh cache in a network namespace without connectivity. Its linked
module versions must match the reference client. Dependency and standard-library
notices are installed in the integration package and mapped individually in
the source/license inventory. Each requires its own license decision; parent
approval cannot waive an unresolved linked component. This component proof does
not establish a complete OS reconstruction or byte-identical upstream output.

The pinned native operational shell can return exit status zero when its internal
command fails. Failed enrollment must display the controller's fixed failure
message and leave the service inactive, with no enrollment receipt or temporary
key. Runtime acceptance checks all these conditions and then proves successful
registration independently; it never infers enrollment from shell status alone.

固定した上流の操作用シェルは、内部コマンドの失敗時にも終了コード0を返す場合があります。
登録失敗は固定の失敗表示、サービスの停止、登録済み情報と一時キーの不在で確認します。
実動作試験では、その後の有効なキーによる登録も別に確認します。
