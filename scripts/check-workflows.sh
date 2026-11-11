#!/usr/bin/env bash
set -euo pipefail
work=$(mktemp -d)
trap 'rm -f "$work/actionlint" "$work/actionlint.tar.gz"; rmdir "$work"' EXIT
curl --fail --location --retry 3 --connect-timeout 15 --max-time 120 \
  --output "$work/actionlint.tar.gz" \
  https://github.com/rhysd/actionlint/releases/download/v1.7.12/actionlint_1.7.12_linux_amd64.tar.gz
printf '%s  %s\n' 8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8 "$work/actionlint.tar.gz" | sha256sum --check -
tar -xzf "$work/actionlint.tar.gz" -C "$work" actionlint
# GitHub supports queue: max, but the latest actionlint (1.7.12) does not.
# Validate that property independently, then suppress only its unsupported-key
# diagnostic. Every other actionlint syntax/expression check remains enabled.
python3 scripts/check_workflow_concurrency.py .github/workflows/*.yml
"$work/actionlint" -ignore '^unexpected key "queue" for "concurrency" section\.' .github/workflows/*.yml
