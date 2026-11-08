#!/usr/bin/env bash
# Full architecture candidate; only a validated directory can leave this stage.
set -euo pipefail
build_cpus=${SHIRUBE_BUILD_CPUS:-4}
build_memory=${SHIRUBE_BUILD_MEMORY_GIB:-8}
[[ $build_cpus =~ ^[1-9][0-9]?$ && $build_cpus -le 64 && $build_memory =~ ^[1-9][0-9]*$ && $build_memory -ge 2 && $build_memory -le 256 ]] || { echo 'Invalid native resource budget' >&2; exit 2; }
architecture=${SHIRUBE_ARCHITECTURE:?}
version=${SHIRUBE_VERSION:?}
commit=${SHIRUBE_SOURCE_COMMIT:?}
qemu_accelerator=${SHIRUBE_QEMU_ACCELERATOR:-kvm}
docker_runtime=${SHIRUBE_DOCKER_RUNTIME:-}
[[ $(git rev-parse HEAD) == "$commit" && -z $(git status --porcelain) ]] || { echo 'Exact clean merged checkout required' >&2; exit 1; }
case "$architecture" in
  amd64) base=debian@sha256:c67e92c8099ba22853ab4cfda7cfb494d253d6012ca82dee98ba8976772af018; aliases='^(x86_64|amd64)$' ;;
  arm64) base=debian@sha256:ec8534cda2cb30f29f44f23e135297af7a1496f2990fd8e4e84aa36b081a757a; aliases='^(aarch64|arm64)$' ;;
  *) echo 'Unsupported architecture' >&2; exit 1 ;;
esac
[[ $(docker info --format '{{.Architecture}}') =~ $aliases ]] || { echo 'Native Docker daemon required' >&2; exit 1; }
python3 -c 'from producer.architecture import release_accelerator,container_runtime; import sys; release_accelerator(sys.argv[1],sys.argv[2]); container_runtime(sys.argv[3],registered=True)' "$architecture" "$qemu_accelerator" "$docker_runtime"
devices=()
runtime_args=()
if [[ $qemu_accelerator == kvm ]]; then
  [[ -r /dev/kvm && -w /dev/kvm ]] || { echo 'Selected KVM mode requires a usable KVM device; no automatic fallback' >&2; exit 1; }
  devices=(--device /dev/kvm)
fi
if [[ -n $docker_runtime ]]; then runtime_args=(--runtime="$docker_runtime"); fi
output=${SHIRUBE_CANDIDATE_DIRECTORY:?}
[[ ! -e "$output" ]] || { echo 'Fresh task-owned candidate directory required' >&2; exit 1; }
mkdir -p "$output"
private=$(mktemp -d)
container_id=
task_nonce=$(basename "$private")
cleanup() {
  if [[ -n $container_id ]] && [[ $(docker inspect --format '{{index .Config.Labels "io.michibiki.shirube.task"}}' "$container_id" 2>/dev/null || true) == "$task_nonce" ]]; then
    docker rm -f "$container_id" >/dev/null
  fi
  rm -f "$private/image.id" "$private/project-source.tar"
  rm -rf "$private/source"
  if [[ -d "$private/libraries" ]]; then rm -rf "$private/libraries"; fi
  rmdir "$private"
}
trap cleanup EXIT
git archive --format=tar HEAD > "$private/project-source.tar"
mkdir "$private/source"
tar -xf "$private/project-source.tar" -C "$private/source"
docker buildx build --load --platform "linux/$architecture" --build-arg "BASE=$base" --iidfile "$private/image.id" -f "$private/source/containers/native/Dockerfile" "$private/source"
image=$(cat "$private/image.id")
[[ $(docker image inspect "$image" --format '{{.Architecture}}') == "$architecture" ]]
docker run --rm --network none "${runtime_args[@]}" "${devices[@]}" --entrypoint python3 -v "$output:/output" "$image" \
  -m producer.cli preflight "$architecture" --storage /output --qemu-accelerator "$qemu_accelerator" --memory-gib "$build_memory"
# Mount only the new task directory and the exact public project source archive.
# Runtime privileges are needed by live-build mounts; no host PID/network,
# credentials, Docker socket, SSH agent or unrelated filesystem is shared.
library_args=()
if [[ -n ${SHIRUBE_LIBRARY_CACHE_STORE:-} ]]; then
  shared_args=()
  if [[ -n ${SHIRUBE_LIBRARY_CACHE_SHARED_OUTPUT:-} ]]; then shared_args=(--shared-output "$SHIRUBE_LIBRARY_CACHE_SHARED_OUTPUT"); fi
  python3 "$private/source/scripts/prepare-library-cache.py" --project "$private/source" --image "$image" \
    --architecture "$architecture" --store "$SHIRUBE_LIBRARY_CACHE_STORE" --output "$private/libraries" \
    --cpus "$build_cpus" --memory-gib "$build_memory" "${shared_args[@]}"
  library_args=(--cached-libraries "$private/libraries")
fi
python3 "$private/source/scripts/run-release-container.py" --architecture "$architecture" --image "$image" \
  --output "$output" --project-source "$private/project-source.tar" --private "$private/netbird-fixture" \
  --version "$version" --commit "$commit" --cpus "$build_cpus" --memory-gib "$build_memory" \
  --qemu-accelerator "$qemu_accelerator" --docker-runtime "$docker_runtime" "${library_args[@]}"
