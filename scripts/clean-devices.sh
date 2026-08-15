#!/usr/bin/env bash
# 强制清理所有由本平台创建的设备容器（按 label 过滤，不影响其它容器）
set -euo pipefail

ids=$(docker ps -aq --filter "label=ldm.managed=true" || true)
if [[ -z "$ids" ]]; then
  echo "没有需要清理的设备容器"
  exit 0
fi

echo "即将删除以下容器："
docker ps -a --filter "label=ldm.managed=true" --format '  {{.Names}}\t{{.Status}}'
read -r -p "确认？[y/N] " ans
[[ "${ans:-N}" =~ ^[yY]$ ]] || { echo "已取消"; exit 0; }

# shellcheck disable=SC2086
docker rm -f $ids
echo "完成。安卓 /data 目录保留在 data/android/ 下，如需彻底清空请手动删除。"
