# Copyright (c) 2026 michibiki.io; MIT License (see NOTICE).
if [ "$BUILD_RECIPE" = http-api ]; then
    # The upstream wrapper ships an empty copyright notice. Build our own
    # packaging from the exact dependency facts; do not copy its wrapper code.
    exec python3 /src/native/http_api.py "$DEB_ARCH" /output
fi
