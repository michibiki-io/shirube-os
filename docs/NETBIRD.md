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
compiler and registers its configuration handler. These guest changes still need
actual runtime verification.

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

Unit tests cover default inactivity, disabling with identity retention, persistent
setup-key rejection, and one-shot erasure on success/failure/timeout. Actual
client enrollment, reconnection, save/reboot, upgrade compatibility and both
architectures still require guest runtime evidence. Unit mocks do not establish
those results.

The configuration and operational XML are compiled with the pinned upstream
schema and template compilers. Configuration changes run through the native
configuration handler and configuration daemon; boot restores saved non-secret
settings through the same handler. The setup key is intentionally excluded from
the saved configuration to satisfy the one-time enrollment contract.
