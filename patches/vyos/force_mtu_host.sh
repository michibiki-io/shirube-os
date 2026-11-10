#!/usr/bin/env bash
#
# Copyright VyOS maintainers and contributors <maintainers@vyos.io>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

# Modified by michibiki-io on 2026-11-10: delegate PMTU/MSS presentation
# to the independently licensed Nmap NSE script. No scanner result parsing.
set -euo pipefail
if (( $# < 1 || $# > 2 )) || [[ -z ${1:-} || $1 == -* ]]; then
    echo 'Usage: force_mtu_host.sh TARGET [INTERFACE]' >&2
    exit 1
fi
probe=(/usr/bin/nmap -T4 --script /usr/share/nmap/scripts/shirube-path-mtu.nse -F)
if (( $# == 2 )); then
    [[ -n $2 ]] || { echo 'Interface cannot be empty.' >&2; exit 1; }
    probe+=(-e "$2")
fi
if (( EUID != 0 )); then probe=(sudo -- "${probe[@]}"); fi
exec "${probe[@]}" -- "$1"
