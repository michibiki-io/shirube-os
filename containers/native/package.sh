#!/bin/bash
# Copyright (c) 2026 michibiki.io; MIT License (see NOTICE).
# Compatibility path for direct callers; use the same selected physical recipe.
set -euo pipefail
stage=native
if [ "$BUILD_RECIPE" = kernel ]; then stage=kernel; fi
exec bash -c "$(PYTHONPATH=/src python3 -c 'from pathlib import Path; import sys; from producer.recipes import component_script; print(component_script(Path(sys.argv[1]), sys.argv[2]))' "$(dirname "$0")" "$stage")"
