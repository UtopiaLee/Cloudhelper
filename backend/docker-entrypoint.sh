#!/usr/bin/env bash
set -euo pipefail

# 以 root 启动时：修正绑定挂载的 /data 属主（可能由旧的 root 容器创建），
# 然后用 gosu 降权到非 root 用户运行真正的进程。
# 若已经是非 root（被 compose 的 user: 覆盖），直接 exec。
if [ "$(id -u)" = "0" ]; then
    mkdir -p /data
    chown -R cloudhelper:cloudhelper /data || true
    exec gosu cloudhelper:cloudhelper "$@"
fi

exec "$@"
