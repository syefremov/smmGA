#!/usr/bin/env bash
set -euo pipefail
# Docker's default iptables backend only. Never flush Docker or Tailscale rules.
iptables -w -n -L DOCKER-USER >/dev/null
rule=(! -i smmbr0 -o smmbr0 -m conntrack --ctstate NEW -j DROP)
if ! iptables -w -C DOCKER-USER "${rule[@]}" 2>/dev/null; then
    iptables -w -I DOCKER-USER 1 "${rule[@]}"
fi
