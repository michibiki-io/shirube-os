# Offline security source fixtures

These compressed fixtures contain exact selected public upstream source files,
not build output. Each adjacent JSON manifest records source identity, selection
limits and per-file SHA-256. Test extraction verifies every member before replay.
Archive owners, timestamps and local paths are omitted. Source-file notices and
upstream patch attribution are retained; GPL-2.0 and LGPL-2.1 texts are available
under `LICENSES/`. FRR and accel fixtures also retain upstream COPYING files.

- Kernel: 32 selected Linux 6.6.128 corresponding-source files, including all
  Stream and new patch targets and prerequisite API headers, plus four exact
  Stream patches. Stream hunks are already present in those source bytes.
- FRR: selected files from immutable upstream 168ade7, plus ten complete Stream
  patches. The quilt series scaffold models Stream preparation. Several Stream
  changes are already present in the upstream baseline; normalize before replay.
- accel-ppp: original targets of the selected complete series; added test files
  are deliberately absent. Reconstruct them through the production patch.
- update-check: exact old script, process wrapper and default configuration.

Run `PYTHONPATH=tests:. python3 -m unittest test_security_backports -v` from the
repository root. Tests use local `patch`, `bash`, a C compiler with UBSan, and
OpenSSL development headers/libraries. They never download source or run daemons.
