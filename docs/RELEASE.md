# Release contract and recovery

`.github/workflows/release.yml` runs on `pull_request_target` **closed/merged**
events for main. The merged-only guard and exact base-repository merge SHA are
mandatory: an unmerged fork head is never checked out with publishing permissions.
Checkout v7's explicit opt-in applies only inside these merged-only jobs; ordinary
PR `Test` uses read-only permissions and no publishing secrets. Action SHAs are
pinned. Self-hosted runners must support their Node 24 runtime.

The workflow resolves the commit/version and both runners once, reserves an
unpublished draft, builds native ISO/QCOW2/VMDK/source candidates, stages complete
per-architecture sets, validates the aggregate, uploads its Actions artifact, and
only then promotes the draft. A missing/failed/skipped required stage blocks the
publication transition. `producer.actions.promote` requires the uploaded
artifact ID/digest and checks its exact workflow source commit.

The pipeline is not yet accepted: complete source reconstruction evidence,
component license clearance and actual NetBird enrollment/save/reboot evidence
remain blocking work. These are explicitly FAIL/NOT_RUN, not automatic waivers.
The currently empty `license-audit.json` cannot clear a distribution.

`Test` is static validation. Its success does not establish image acceptance.

`producer.policy.eligible` accepts relevant PRs merged into main and their exact
40-character merge commit. Unmerged PRs, docs-only changes and direct pushes are
excluded from expensive builds and publication. Relevant paths are producer,
containers, source lock, patches, guest integration, tests, workflow/schema and
logo changes. The project policy intentionally uses native AMD64/ARM64 runner variables and
requires at least a patch release for relevant no-bump commits. Those issue-specific
rules override generic container-registry/QEMU release defaults.

Versions come from commit messages, never PR titles. The first release is
`v0.1.0`. Subsequent Conventional Commit breaking changes bump major, `feat`
bumps minor, and every other eligible image change receives at least a patch
bump. Individual PR commit subjects, bodies and SHAs form release notes. GitHub commit
metadata is checked for complete coverage; a squash subject derived from the PR
title is not substituted for the original commits. A completed workflow must
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
live release state before retrying. Draft staging and final promotion are implemented in `producer.actions`.
They recheck draft ownership before writes and reconcile uncertain final
publication against live release state without rewriting a published set.

OCI status is independently `NOT_RUN`. The current validator refuses a positive
OCI claim because live-evidence validation is not implemented. Local QEMU boot
success cannot establish live OCI compatibility.

The skill-based release baseline was rendered and inspected before adaptation.
This OS producer intentionally does not publish builder containers to a registry,
use QEMU for cross-architecture compilation, or require Helm/cloud deployment.
Relevant merged PRs are selected by actual changed paths rather than PR-title
formatting. `scripts/release/compute_release.py` and
`scripts/release/generate_release_notes.py` expose the same canonical policy for
local inspection.
