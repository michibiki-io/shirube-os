#!/bin/bash
# Copyright (c) 2026 michibiki.io; MIT.
grep -Fq 'set(LIBYANG_MAJOR_VERSION 3)' CMakeLists.txt
grep -Fq 'set(LIBYANG_MINOR_VERSION 13)' CMakeLists.txt
grep -Fq 'set(LIBYANG_MICRO_VERSION 6)' CMakeLists.txt
cp -a distro/pkg/deb debian
sed -i 's/{{ version }}/3.13.6/g; s/{{ release }}/1/g' debian/changelog
