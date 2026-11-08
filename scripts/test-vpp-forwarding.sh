#!/usr/bin/env bash
# Component probe only. Run in a new native compiler container with /packages
# read-only, NET_ADMIN/SYS_ADMIN/IPC_LOCK and --ulimit memlock=-1:-1.
# No host networking, credentials, Docker socket or writable host mount needed.
set -euo pipefail
[[ -f /.dockerenv && $EUID == 0 && ! -e /vpp-probe ]]
apt-get update
apt-get install -y --no-install-recommends iproute2 iputils-ping curl
python3 - <<'PY'
from pathlib import Path
import subprocess,hashlib
root=Path('/packages')
changes=max(root.glob('vpp_*.changes'),key=lambda p:p.stat().st_mtime_ns)
active=False;count=0
for line in changes.read_text().splitlines():
    if line=='Checksums-Sha256:':active=True;continue
    if active and not line.startswith(' '):active=False
    if active:
        digest,size,name=line.split()
        if not name.endswith('.deb'):continue
        assert Path(name).name==name and not (root/name).is_symlink()
        assert (root/name).stat().st_size==int(size)
        with (root/name).open('rb') as stream:
            assert hashlib.file_digest(stream,'sha256').hexdigest()==digest
        subprocess.run(['dpkg-deb','-x',str(root/name),'/vpp-probe'],check=True)
        count+=1
assert count==10
PY
multi=$(dpkg-architecture -qDEB_HOST_MULTIARCH)
export LD_LIBRARY_PATH="/vpp-probe/usr/lib/$multi:/vpp-probe/usr/lib"
unshare --net -- sleep 300 & client_pid=$!
unshare --net -- sleep 300 & server_pid=$!
cleanup() {
  result=$?
  if (( result != 0 )) && [[ -S /run/vpp-probe/cli.sock ]]; then
    /vpp-probe/usr/bin/vppctl -s /run/vpp-probe/cli.sock show log || true
  fi
  kill ${vpp_pid:-} "$client_pid" "$server_pid" 2>/dev/null || true
}
trap cleanup EXIT
sleep 0.5
ip link add vpp0 type veth peer name client0
ip link add vpp1 type veth peer name server0
ip link set client0 netns "$client_pid"
ip link set server0 netns "$server_pid"
ip link set vpp0 up
ip link set vpp1 up
nsenter -t "$client_pid" -n ip address add 10.88.1.2/24 dev client0
nsenter -t "$client_pid" -n ip link set client0 up
nsenter -t "$client_pid" -n ip route add default via 10.88.1.1
nsenter -t "$server_pid" -n ip address add 10.88.2.2/24 dev server0
nsenter -t "$server_pid" -n ip link set server0 up
nsenter -t "$server_pid" -n ip route add default via 10.88.2.1
mkdir -p /run/vpp-probe
cat > /run/vpp-probe/startup.conf <<CONFIG
unix { nodaemon cli-listen /run/vpp-probe/cli.sock log /run/vpp-probe/vpp.log }
memory { main-heap-size 128M main-heap-page-size 4k }
statseg { socket-name /run/vpp-probe/stats.sock size 16M page-size 4k }
buffers { buffers-per-numa 1024 page-size 4k }
plugins { path /vpp-probe/usr/lib/$multi/vpp_plugins plugin dpdk_plugin.so { disable } }
CONFIG
/vpp-probe/usr/bin/vpp -c /run/vpp-probe/startup.conf > /run/vpp-probe/stdout.log 2>&1 & vpp_pid=$!
for i in $(seq 1 120); do
  if [[ -S /run/vpp-probe/cli.sock ]]; then break; fi
  if ! kill -0 "$vpp_pid" 2>/dev/null; then cat /run/vpp-probe/stdout.log; exit 1; fi
  sleep 0.25
done
vppctl() {
  local result
  result=$(/vpp-probe/usr/bin/vppctl -s /run/vpp-probe/cli.sock "$@")
  printf '%s\n' "$result"
  if printf '%s\n' "$result" | grep -Eiq 'unknown (input|interface)|no such file|failed|error'; then return 1; fi
}
vppctl show version
vppctl show plugins > /run/vpp-probe/plugins.txt
for plugin in hs_apps_plugin http_static_plugin prom_plugin af_packet_plugin; do
  grep "$plugin" /run/vpp-probe/plugins.txt
done
vppctl create host-interface name vpp0
vppctl create host-interface name vpp1
vppctl set interface ip address host-vpp0 10.88.1.1/24
vppctl set interface ip address host-vpp1 10.88.2.1/24
vppctl set interface state host-vpp0 up
vppctl set interface state host-vpp1 up
# Allow initial neighbor discovery, then measure forwarding independently.
nsenter -t "$client_pid" -n ping -c 1 -W 2 10.88.2.2 >/dev/null || true
LC_ALL=C nsenter -t "$client_pid" -n ping -c 5 -W 3 10.88.2.2 > /run/vpp-probe/ping.txt
cat /run/vpp-probe/ping.txt
grep -q '5 packets transmitted, 5 received, 0% packet loss' /run/vpp-probe/ping.txt
vppctl session enable
vppctl http static server uri tcp://10.88.1.1/18081 url-handlers
vppctl prom enable
nsenter -t "$client_pid" -n curl --noproxy '*' --fail --silent --show-error --max-time 15 \
  http://10.88.1.1:18081/stats.prom > /run/vpp-probe/prom.txt
grep -Eq '^# (HELP|TYPE)|^vpp_' /run/vpp-probe/prom.txt
head -n 3 /run/vpp-probe/prom.txt
printf '%s\n' 'PASS: native VPP startup, upstream plugins, two-namespace IPv4 forwarding and Prometheus over http_static; not a full guest OS boot'
