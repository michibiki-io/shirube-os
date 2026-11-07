#!/usr/bin/env bash
# ISO generation is independent of KVM/local disk acceptance.
set -euo pipefail
architecture=${SHIRUBE_ARCHITECTURE:?}
version=${SHIRUBE_VERSION:?}
commit=${SHIRUBE_SOURCE_COMMIT:?}
output=${SHIRUBE_ISO_DIRECTORY:?}
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
[[ ! -e "$output" ]] || { echo 'New ISO output directory required' >&2; exit 1; }
mkdir -p "$output"
private=$(mktemp -d)
container_id=
log_pid=
task_nonce=$(basename "$private")
cleanup() {
  if [[ -n $container_id ]] && [[ $(docker inspect --format '{{index .Config.Labels "io.michibiki.shirube.task"}}' "$container_id" 2>/dev/null || true) == "$task_nonce" ]]; then
    docker rm -f "$container_id" >/dev/null
  fi
  if [[ -n $log_pid ]]; then wait "$log_pid" || true; fi
  rm -f "$private/image.id" "$private/project-source.tar"
  rm -rf "$private/source"
  rmdir "$private"
}
trap cleanup EXIT
# Freeze the exact committed input before a long Docker metadata/build wait.
# Concurrent implementation edits must never enter an older-head ISO candidate.
mkdir "$private/source"
git archive --format=tar "$commit" > "$private/project-source.tar"
tar -xf "$private/project-source.tar" -C "$private/source"
docker build --platform "linux/$architecture" --build-arg "BASE=$base" --iidfile "$private/image.id" -f "$private/source/containers/native/Dockerfile" "$private/source"
image=$(cat "$private/image.id")
docker run --rm --network none --entrypoint python3 -v "$output:/output" "$image" \
  -m producer.cli preflight "$architecture" --storage /output --stage iso
cache_args=()
if [[ -n ${SHIRUBE_SOURCE_CACHE:-} ]]; then
  cache_args=(-v "$SHIRUBE_SOURCE_CACHE:/verified:ro")
  if [[ -d "$SHIRUBE_SOURCE_CACHE/python-inputs-$architecture" ]]; then
    cache_args+=(-v "$SHIRUBE_SOURCE_CACHE/python-inputs-$architecture:/public-inputs:ro")
  fi
  source_args=(--verified-input /verified)
else
  source_args=()
fi
container_id=$(docker create --label "io.michibiki.shirube.task=$task_nonce" --privileged \
  --cpus=2 --memory=8g --memory-swap=8g --pids-limit=4096 \
  -v "$output:/output" -v "$private/project-source.tar:/project-source.tar:ro" "${cache_args[@]}" "$image" "$architecture" \
  --target iso --version "$version" --commit "$commit" "${source_args[@]}" "${source_audit_args[@]}")
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
  if (( memory_kib < 2 * 1024 * 1024 )); then
    echo 'Stopping owned build: less than 2 GiB host memory headroom remains' >&2
    docker stop --time 30 "$container_id" >/dev/null
    exit 1
  fi
  sleep 30
done
wait "$log_pid"
log_pid=
[[ $(docker inspect --format '{{.State.ExitCode}}' "$container_id") == 0 ]]
