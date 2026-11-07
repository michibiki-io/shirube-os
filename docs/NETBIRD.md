# NetBird client integration

The initial headless client baseline is v0.82.0. Both public release asset hashes
are fixed in `source-lock.json`, verified against the upstream checksums file.
The persistent configuration interface contains only `management-url` (HTTPS)
and `disable`. There is no persistent `setup-key` node.

The `service netbird` XML and commit handler are under `guest/`. The lifecycle
controller is `producer/netbird.py`; intended installation names are
`/usr/libexec/shirube/netbird_control.py` for the imported module and
`/usr/libexec/shirube/netbird-control` for its executable entry point. The pinned
client belongs at `/usr/libexec/shirube/netbird`. Package/template generation and
installation wiring are not yet complete.

Without configuration the client remains inactive. An enabled configuration
creates non-secret runtime settings. Enrollment is a separate root-only action:
`sudo /usr/libexec/shirube/netbird-control enroll`. It reads a hidden prompt or
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
