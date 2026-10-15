#!/usr/bin/env bash
# OPTIONAL: turn this laptop's Wi-Fi into a private access point so phones can reach the kit with no other network.
# This CHANGES the laptop's network configuration (it drops its current Wi-Fi connection). Run it yourself, on purpose;
# nothing in the kit calls it. Usage: infra/kit/hotspot.sh up <ssid> <password-of-8-to-63-chars> | down
set -euo pipefail
NAME=stozher-kit
case "${1:-}" in
  up)
    ssid="${2:?ssid}"; pass="${3:?password (8-63 chars)}"
    nmcli connection delete "$NAME" >/dev/null 2>&1 || true
    nmcli connection add type wifi ifname '*' con-name "$NAME" autoconnect no ssid "$ssid" \
      802-11-wireless.mode ap 802-11-wireless.band bg ipv4.method shared \
      wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$pass" >/dev/null
    nmcli connection up "$NAME"
    echo "access point up; the kit is at http://10.42.0.1:8000/"
    ;;
  down) nmcli connection down "$NAME" ;;
  *) echo "usage: $0 up <ssid> <password> | down" >&2; exit 1 ;;
esac
