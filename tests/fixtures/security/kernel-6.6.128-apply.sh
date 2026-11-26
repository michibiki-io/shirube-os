# Historical production helper for the immutable Linux 6.6.128 evidence.
apply_kernel_tool_fixes() {
    : > "$PATCH_RECEIPT"
    for patchfile in /kernel-patches/*.patch; do
        [ -f "$patchfile" ] || continue
        sha256sum "$patchfile" >> "$PATCH_RECEIPT"
        if patch --dry-run --fuzz=0 -p1 < "$patchfile" >/dev/null 2>&1; then
            patch --fuzz=0 -p1 < "$patchfile"
        elif patch --dry-run --reverse --fuzz=0 -p1 < "$patchfile" >/dev/null 2>&1; then
            echo "Repository patch already applied: $(basename "$patchfile")"
        else
            echo "Ambiguous repository kernel patch state: $patchfile" >&2
            exit 1
        fi
    done
}