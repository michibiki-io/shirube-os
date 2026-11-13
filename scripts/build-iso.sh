#!/usr/bin/env bash
# ISO generation is independent of KVM/local disk acceptance.
set -euo pipefail
build_cpus=${SHIRUBE_BUILD_CPUS:-4}
build_memory=${SHIRUBE_BUILD_MEMORY_GIB:-8}
memory_reserve=${SHIRUBE_HOST_MEMORY_RESERVE_MIB:-2048}
[[ $build_cpus =~ ^[1-9][0-9]?$ && $build_cpus -le 64 && $build_memory =~ ^[1-9][0-9]*$ && $build_memory -ge 2 && $build_memory -le 256 ]] || { echo 'Invalid native resource budget' >&2; exit 2; }
[[ $memory_reserve =~ ^[1-9][0-9]*$ && $memory_reserve -ge 512 && $memory_reserve -le 65536 ]] || { echo 'Invalid host memory reserve' >&2; exit 2; }
architecture=${SHIRUBE_ARCHITECTURE:?}
version=${SHIRUBE_VERSION:?}
commit=${SHIRUBE_SOURCE_COMMIT:?}
docker_runtime=${SHIRUBE_DOCKER_RUNTIME:-}
output=${SHIRUBE_ISO_DIRECTORY:?}
preserve_failed=${SHIRUBE_PRESERVE_FAILED_BUILD:-0}
case "$preserve_failed" in
  0) ;;
  1) [[ -n ${SHIRUBE_BUILD_STATE_FILE:-} ]] || { echo 'Private diagnosis requires a new build state file' >&2; exit 2; } ;;
  *) echo 'SHIRUBE_PRESERVE_FAILED_BUILD must be 0 or 1' >&2; exit 2 ;;
esac
source_audit_args=()
case ${SHIRUBE_INCLUDE_SOURCES:-0} in
  1) source_audit_args=(--include-sources) ;;
  0) ;;
  *) echo 'SHIRUBE_INCLUDE_SOURCES must be 0 or 1' >&2; exit 2 ;;
esac
[[ $(git rev-parse HEAD) == "$commit" && -z $(git status --porcelain) ]] || { echo 'Exact clean source checkout required' >&2; exit 1; }
case "$architecture" in
  amd64) base=debian@sha256:c67e92c8099ba22853ab4cfda7cfb494d253d6012ca82dee98ba8976772af018; aliases='^(x86_64|amd64)$' ;;
  arm64) base=debian@sha256:ec8534cda2cb30f29f44f23e135297af7a1496f2990fd8e4e84aa36b081a757a; aliases='^(aarch64|arm64)$' ;;
  *) exit 2 ;;
esac
[[ $(docker info --format '{{.Architecture}}') =~ $aliases ]] || { echo 'Native Docker daemon required' >&2; exit 1; }
runtime_args=()
if [[ -n $docker_runtime ]]; then
  [[ $docker_runtime =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] || { echo 'Invalid scoped Docker runtime name' >&2; exit 2; }
  runtime_args=(--runtime="$docker_runtime")
fi
[[ ! -e "$output" ]] || { echo 'New ISO output directory required' >&2; exit 1; }
mkdir -p "$output"
private_root=${SHIRUBE_BUILD_STATE_DIRECTORY:-$PWD/.work/build-state}
mkdir -p "$private_root"
private=$(mktemp -d "$private_root/iso.XXXXXXXXXX")
container_id=
log_pid=
task_nonce=$(basename "$private")
cleanup() {
  result=$?
  if [[ -n $container_id ]] && [[ $(docker inspect --format '{{index .Config.Labels "io.michibiki.shirube.task"}}' "$container_id" 2>/dev/null || true) == "$task_nonce" ]]; then
    if (( result != 0 )) && [[ $preserve_failed == 1 ]]; then
      if [[ $(docker inspect --format '{{.State.Running}}' "$container_id") == true ]]; then
        docker stop --time 30 "$container_id" >/dev/null
      fi
      if [[ -n $log_pid ]]; then wait "$log_pid" || true; fi
      echo 'Owned failed build preserved for private diagnosis; this is not a distributable artifact.' >&2
      return "$result"
    fi
    docker rm -f "$container_id" >/dev/null
  fi
  if [[ -n $log_pid ]]; then wait "$log_pid" || true; fi
  rm -f "$private/image.id" "$private/project-source.tar"
  rm -rf "$private/source"
  if [[ -d "$private/libraries" ]]; then rm -rf "$private/libraries"; fi
  rmdir "$private"
}
trap cleanup EXIT
# Freeze the exact committed input before a long Docker metadata/build wait.
# Concurrent implementation edits must never enter an older-head ISO candidate.
mkdir "$private/source"
git archive --format=tar "$commit" > "$private/project-source.tar"
tar -xf "$private/project-source.tar" -C "$private/source"
docker buildx build --load --platform "linux/$architecture" --build-arg "BASE=$base" --iidfile "$private/image.id" -f "$private/source/containers/native/Dockerfile" "$private/source"
image=$(cat "$private/image.id")
docker run --rm --network none "${runtime_args[@]}" --entrypoint python3 -v "$output:/output" "$image" \
  -m producer.cli preflight "$architecture" --storage /output --stage iso --memory-gib "$build_memory"
cache_args=()
library_args=()
if [[ -n ${SHIRUBE_LIBRARY_CACHE_STORE:-} ]]; then
  library_source_args=()
  if [[ -n ${SHIRUBE_SOURCE_CACHE:-} ]]; then library_source_args=(--verified-input "$SHIRUBE_SOURCE_CACHE"); fi
  python3 "$private/source/scripts/prepare-library-cache.py" --project "$private/source" --image "$image" \
    --architecture "$architecture" --store "$SHIRUBE_LIBRARY_CACHE_STORE" --output "$private/libraries" \
    --cpus "$build_cpus" --memory-gib "$build_memory" "${library_source_args[@]}"
  library_args=(--cached-libraries /library-cache)
  cache_args+=(-v "$private/libraries:/library-cache:ro")
fi
if [[ -n ${SHIRUBE_SOURCE_CACHE:-} ]]; then
  cache_args+=(-v "$SHIRUBE_SOURCE_CACHE:/verified:ro")
  if [[ -d "$SHIRUBE_SOURCE_CACHE/python-inputs-$architecture" ]]; then
    cache_args+=(-v "$SHIRUBE_SOURCE_CACHE/python-inputs-$architecture:/public-inputs:ro")
  fi
  source_args=(--verified-input /verified)
else
  source_args=()
fi
container_id=$(docker create "${runtime_args[@]}" --label "io.michibiki.shirube.task=$task_nonce" --privileged \
  --cpus="$build_cpus" --memory="${build_memory}g" --memory-swap="${build_memory}g" --pids-limit=4096 \
  -v "$output:/output" -v "$private/project-source.tar:/project-source.tar:ro" "${cache_args[@]}" "$image" /src/native/build.py "$architecture" \
  --target iso --version "$version" --commit "$commit" "${source_args[@]}" "${source_audit_args[@]}" "${library_args[@]}")
if [[ -n ${SHIRUBE_BUILD_STATE_FILE:-} ]]; then
  [[ ! -e "$SHIRUBE_BUILD_STATE_FILE" ]] || { echo 'Build state file must be new' >&2; exit 1; }
  printf '%s\n' "$container_id" > "$SHIRUBE_BUILD_STATE_FILE"
fi
docker start "$container_id" >/dev/null
docker logs --follow "$container_id" &
log_pid=$!
# Bound task consumption and preserve headroom for other host workloads.
while [[ $(docker inspect --format '{{.State.Running}}' "$container_id") == true ]]; do
  available=$(df -Pk "$output" | awk 'NR==2 {print $4}')
  if (( available < 12 * 1024 * 1024 )); then
    echo 'Stopping owned build: less than 12 GiB host storage headroom remains' >&2
    docker stop --time 30 "$container_id" >/dev/null
    exit 1
  fi
  memory_kib=$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)
  if (( memory_kib < memory_reserve * 1024 )); then
    echo "Stopping owned build: less than $memory_reserve MiB host memory headroom remains" >&2
    docker stop --time 30 "$container_id" >/dev/null
    exit 1
  fi
  sleep 30
done
wait "$log_pid"
log_pid=
[[ $(docker inspect --format '{{.State.ExitCode}}' "$container_id") == 0 ]]
