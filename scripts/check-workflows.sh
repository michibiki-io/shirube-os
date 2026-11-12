#!/usr/bin/env bash
set -euo pipefail
work=$(mktemp -d)
trap 'rm -f "$work/actionlint" "$work/actionlint.tar.gz"; rmdir "$work"' EXIT
curl --fail --location --retry 3 --connect-timeout 15 --max-time 120 \
  --output "$work/actionlint.tar.gz" \
  https://github.com/rhysd/actionlint/releases/download/v1.7.12/actionlint_1.7.12_linux_amd64.tar.gz
printf '%s  %s\n' 8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8 "$work/actionlint.tar.gz" | sha256sum --check -
tar -xzf "$work/actionlint.tar.gz" -C "$work" actionlint
# GitHub supports queue and cache-mode, but actionlint 1.7.12 does not.
# Validate these properties independently, then suppress only their unsupported-key
# diagnostic. Every other actionlint syntax/expression check remains enabled.
python3 scripts/check_workflow_concurrency.py .github/workflows/*.yml
python3 -m scripts.check_workflow_cache_mode .github/workflows/*.yml
"$work/actionlint" -ignore '^unexpected key "queue" for "concurrency" section\.' \
  -ignore '^unexpected key "cache-mode" for "(workflow|job)" section\.' .github/workflows/*.yml
