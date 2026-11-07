# Release contract and recovery

No binary publication workflow is enabled while the producer and required runtime
gates remain incomplete. `Test` runs safe unit/failure-path checks on PRs and main
pushes with read-only permissions and no persisted checkout credentials. A green
`Test` result is static validation, not release acceptance.

`producer.policy.eligible` accepts relevant PRs merged into main and their exact
40-character merge commit. Unmerged PRs, docs-only changes and direct pushes are
excluded from expensive builds and publication. Relevant paths are producer,
containers, source lock, patches, guest integration, tests, workflow/schema and
logo changes. The policy functions are tested; wiring the complete build/release
workflow remains outstanding.

Versions come from commit messages, never PR titles. The first release is
`v0.1.0`. Subsequent Conventional Commit breaking changes bump major, `feat`
bumps minor, and every other eligible image change receives at least a patch
bump. Commit subjects and SHAs form release notes. A completed workflow must
resolve the immutable merged source commit/version once for both architectures.

`producer.release.validate` validates the metadata contract against actual files:
identity, complete architecture/role sets, physical sizes, hashes, checksum
coverage, candidate generations, required check outcomes, detailed evidence and
exact source/license members. Every required result must be `PASS`; `SKIP`,
`NOT_RUN`, missing checks and inconsistent evidence fail. This validator does not
execute a VM: it consumes evidence from a trusted producer. Synthetic validator
fixtures are explicitly marked and are not runtime acceptance evidence.

Candidate generations bind each architecture's ISO/disks/source/evidence to the
same build-internal signing keys. Before publication, reuse only a complete
verified candidate; otherwise rebuild the whole architecture candidate and
regenerate aggregate metadata in unpublished staging. Never mix generations.

Publish only after every required gate passes and the entire asset set exists in
an unpublished draft. Every source component archive must be below the GitHub
2 GiB per-asset limit. Hash every asset plus `manifest.json` in `SHA256SUMS`;
checksums do not include themselves. Verify all source archives and their member
hashes. Retain source archives with the binaries in the same release.

After publication, reruns must download and verify the complete recorded set,
including metadata, before doing any build work. An exact match is a verification-
only no-op. Missing or changed published material fails without any upload,
replacement, metadata rewrite or repair. Recover with a new eligible merged
change and superseding version. Reconcile uncertain publication outcomes against
live release state before retrying. The current immutable-set comparison helper
is only a primitive; GitHub staging/upload/reconciliation orchestration is not yet
implemented.

OCI status is independently `NOT_RUN`. The current validator refuses a positive
OCI claim because live-evidence validation is not implemented. Local QEMU boot
success cannot establish live OCI compatibility.
