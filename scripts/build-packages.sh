#!/usr/bin/env bash
# Native package stage only. No ISO/distribution validation is implied.
set -euo pipefail
build_cpus=${SHIRUBE_BUILD_CPUS:-4}
build_memory=${SHIRUBE_BUILD_MEMORY_GIB:-8}
[[ $build_cpus =~ ^[1-9][0-9]?$ && $build_cpus -le 64 && $build_memory =~ ^[1-9][0-9]*$ && $build_memory -ge 2 && $build_memory -le 256 ]] || { echo 'Invalid native resource budget' >&2; exit 2; }
if [[ $# != 2 ]]; then
  echo 'Usage: scripts/build-packages.sh amd64|arm64 NEW_OUTPUT_DIRECTORY' >&2
  exit 2
fi
architecture=$1
output=$2
case "$architecture" in
  amd64) base=debian@sha256:c67e92c8099ba22853ab4cfda7cfb494d253d6012ca82dee98ba8976772af018; aliases='^(x86_64|amd64)$' ;;
  arm64) base=debian@sha256:ec8534cda2cb30f29f44f23e135297af7a1496f2990fd8e4e84aa36b081a757a; aliases='^(aarch64|arm64)$' ;;
  *) echo 'Select amd64 or arm64' >&2; exit 2 ;;
esac
[[ $(docker info --format '{{.Architecture}}') =~ $aliases ]] || { echo 'A native Docker daemon for the selected architecture is required' >&2; exit 1; }
[[ ! -e "$output" ]] || { echo 'Output must be a new task directory' >&2; exit 1; }
mkdir -p "$output"
output=$(cd "$output" && pwd)
work=$(mktemp -d)
trap 'rm -f "$work/image.id"; rmdir "$work"' EXIT
docker build --platform "linux/$architecture" --build-arg "BASE=$base" \
  --iidfile "$work/image.id" -f containers/native/Dockerfile .
image=$(cat "$work/image.id")
[[ $(docker image inspect "$image" --format '{{.Architecture}}') == "$architecture" ]]
docker run --rm --platform "linux/$architecture" --cpus="$build_cpus" --memory="${build_memory}g" --memory-swap="${build_memory}g" \
  --pids-limit=4096 -v "$output:/output" "$image" /src/native/build.py "$architecture"
