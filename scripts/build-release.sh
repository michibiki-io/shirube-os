#!/usr/bin/env bash
# Full architecture candidate; only a validated directory can leave this stage.
set -euo pipefail
architecture=${SHIRUBE_ARCHITECTURE:?}
version=${SHIRUBE_VERSION:?}
commit=${SHIRUBE_SOURCE_COMMIT:?}
[[ $(git rev-parse HEAD) == "$commit" && -z $(git status --porcelain) ]] || { echo 'Exact clean merged checkout required' >&2; exit 1; }
case "$architecture" in
  amd64) base=debian@sha256:c67e92c8099ba22853ab4cfda7cfb494d253d6012ca82dee98ba8976772af018; aliases='^(x86_64|amd64)$' ;;
  arm64) base=debian@sha256:ec8534cda2cb30f29f44f23e135297af7a1496f2990fd8e4e84aa36b081a757a; aliases='^(aarch64|arm64)$' ;;
  *) echo 'Unsupported architecture' >&2; exit 1 ;;
esac
[[ $(docker info --format '{{.Architecture}}') =~ $aliases ]] || { echo 'Native Docker daemon required' >&2; exit 1; }
[[ -r /dev/kvm && -w /dev/kvm ]] || { echo 'KVM unavailable; TCG is not validated' >&2; exit 1; }
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
  rmdir "$private"
}
trap cleanup EXIT
git archive --format=tar HEAD > "$private/project-source.tar"
mkdir "$private/source"
tar -xf "$private/project-source.tar" -C "$private/source"
docker build --platform "linux/$architecture" --build-arg "BASE=$base" --iidfile "$private/image.id" -f "$private/source/containers/native/Dockerfile" "$private/source"
image=$(cat "$private/image.id")
[[ $(docker image inspect "$image" --format '{{.Architecture}}') == "$architecture" ]]
docker run --rm --network none --device /dev/kvm --entrypoint python3 -v "$output:/output" "$image" \
  -m producer.cli preflight "$architecture" --storage /output --require-kvm
# Mount only the new task directory and the exact public project source archive.
# Runtime privileges are needed by live-build mounts; no host PID/network,
# credentials, Docker socket, SSH agent or unrelated filesystem is shared.
container_id=$(docker create --label "io.michibiki.shirube.task=$task_nonce" --privileged --device /dev/kvm --cpus=2 --memory=8g --memory-swap=8g \
  --pids-limit=4096 -v "$output:/output" -v "$private/project-source.tar:/project-source.tar:ro" \
  "$image" "$architecture" --target release --version "$version" --commit "$commit")
docker start --attach "$container_id"
[[ $(docker inspect --format '{{.State.ExitCode}}' "$container_id") == 0 ]]
