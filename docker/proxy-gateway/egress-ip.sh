#!/bin/bash
# 打印当前出口 IP（JSON）。控制器通过 docker exec 调用它来校验代理是否生效。
set -uo pipefail

ENDPOINTS=(
  "https://api.ipify.org?format=json"
  "https://ifconfig.co/json"
  "https://api.ip.sb/geoip"
)

for url in "${ENDPOINTS[@]}"; do
  body=$(curl -fsS --max-time 10 "$url" 2>/dev/null) || continue
  ip=$(printf '%s' "$body" | jq -r '.ip // .query // empty' 2>/dev/null)
  [[ -z "$ip" ]] && continue
  country=$(printf '%s' "$body" | jq -r '.country // .country_name // .country_code // empty' 2>/dev/null)
  city=$(printf '%s' "$body" | jq -r '.city // empty' 2>/dev/null)
  jq -cn --arg ip "$ip" --arg country "${country:-}" --arg city "${city:-}" --arg src "$url" \
     '{ip:$ip, country:$country, city:$city, source:$src}'
  exit 0
done

echo '{"error":"unable to determine egress ip"}' >&2
exit 1
