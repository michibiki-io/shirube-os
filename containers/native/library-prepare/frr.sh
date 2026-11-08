#!/bin/bash
# Copyright (c) 2026 michibiki.io; MIT.
export DEB_BUILD_PROFILES='pkg.frr.rtrlib pkg.frr.lua'
BUILD_FLAGS=(-Ppkg.frr.rtrlib,pkg.frr.lua)
if [ -f debian/patches/series ]; then
    if [ -e .pc ]; then mv .pc /output/archive-quilt-state; fi
    awk '!/^[[:space:]]*(#|$)/ {print $1}' debian/patches/series | tac | while IFS= read -r name; do
        patchfile="debian/patches/$name"
        if patch --dry-run --reverse --fuzz=0 -p1 < "$patchfile" >/dev/null 2>&1; then
            patch --reverse --fuzz=0 -p1 < "$patchfile"
        elif patch --dry-run --fuzz=0 -p1 < "$patchfile" >/dev/null 2>&1; then
            echo "Not yet applied in snapshot: $name"
        else
            echo "Ambiguous archived FRR patch state: $name" >&2
            exit 1
        fi
    done
fi
