#!/usr/bin/env bash
set +e
cd "$(dirname "$0")"
PORT="${SAREE_PORT:-8000}"
echo '=== Saree Store LAN diagnostics ==='
echo
echo '[1] LAN IPv4 addresses:'
ip -4 -o addr show scope global 2>/dev/null | awk '{print "  interface=" $2, "address=" $4}' || true
echo
echo '[2] Server listener on port' "$PORT":
ss -ltnp 2>/dev/null | grep -E ":${PORT}[[:space:]]" || echo '  No listener found. Start the server with ./start.sh first.'
echo
echo '[3] Local health check:'
curl -fsS "http://127.0.0.1:${PORT}/api/health" || echo '  Health check failed; is the server running?'
echo
echo '[4] Firewall status:'
if command -v ufw >/dev/null 2>&1; then sudo ufw status verbose; else echo '  UFW not installed; check your firewall tool if needed.'; fi
echo
echo 'If the local health check works but phones cannot connect:'
echo '  - use the host computer IPv4 address shown above, not 127.0.0.1 or 0.0.0.0'
echo '  - put phone and host on the same non-guest Wi-Fi'
echo '  - disable router/AP client isolation for that trusted network'
echo '  - allow TCP port' "$PORT" 'through the Linux firewall only on your private LAN'
echo '  - do not expose/port-forward this port to the public internet'
