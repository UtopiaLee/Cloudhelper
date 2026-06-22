#!/usr/bin/env bash
# CloudHelper 一键部署脚本（多发行版兼容）
#
# 用法：
#   curl -fsSL https://raw.githubusercontent.com/UtopiaLee/Cloudhelper/main/deploy.sh | sudo bash
#   或克隆仓库后:  sudo ./deploy.sh
#
# 可选参数：
#   --dir <path>           安装目录，默认 /opt/cloudhelper
#   --port <num>           前端端口，默认 8080
#   --branch <name>        Git 分支，默认 main
#   --domain <example.com> 启用 HTTPS（Caddy 自动 LE 证书）
#   --email  <you@x.com>   配合 --domain 使用，必填
#   --no-update            如果目录已存在，不执行 git pull
#   --restart              不重建镜像，只 restart 服务
#
# 支持发行版：Debian/Ubuntu/Mint/Raspbian、RHEL/CentOS/Rocky/Alma/Fedora/Oracle Linux、
#             openSUSE/SLES、Arch/Manjaro、Alpine
#
# 失败时脚本会立刻退出并打印建议；安全特性：自动随机生成 .env 中的敏感项。

set -euo pipefail

###############################################################################
# 参数与默认值
###############################################################################
REPO_URL="https://github.com/UtopiaLee/Cloudhelper.git"
INSTALL_DIR="/opt/cloudhelper"
FRONTEND_PORT="8080"
BRANCH="main"
DOMAIN=""
EMAIL=""
DO_UPDATE=1
RESTART_ONLY=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir) INSTALL_DIR="$2"; shift 2;;
    --port) FRONTEND_PORT="$2"; shift 2;;
    --branch) BRANCH="$2"; shift 2;;
    --domain) DOMAIN="$2"; shift 2;;
    --email) EMAIL="$2"; shift 2;;
    --no-update) DO_UPDATE=0; shift;;
    --restart) RESTART_ONLY=1; shift;;
    -h|--help)
      sed -n '1,30p' "$0"
      exit 0
      ;;
    *) echo "未知参数：$1" >&2; exit 2;;
  esac
done

if [[ -n "$DOMAIN" && -z "$EMAIL" ]]; then
  echo "❌ --domain 需要同时提供 --email" >&2
  exit 2
fi

###############################################################################
# 颜色 & 日志
###############################################################################
if [[ -t 1 ]]; then
  C_R="\033[31m"; C_G="\033[32m"; C_Y="\033[33m"; C_B="\033[34m"; C_RESET="\033[0m"
else
  C_R=""; C_G=""; C_Y=""; C_B=""; C_RESET=""
fi
log()  { printf "${C_B}[*]${C_RESET} %s\n" "$*"; }
ok()   { printf "${C_G}[ok]${C_RESET} %s\n" "$*"; }
warn() { printf "${C_Y}[!]${C_RESET} %s\n" "$*"; }
die()  { printf "${C_R}[x]${C_RESET} %s\n" "$*" >&2; exit 1; }

###############################################################################
# 权限 & 探测
###############################################################################
SUDO=""
if [[ $EUID -ne 0 ]]; then
  if command -v sudo >/dev/null 2>&1; then
    SUDO="sudo"
  else
    die "需要 root 或 sudo。请改用：sudo bash $0"
  fi
fi

OS_ID=""; OS_LIKE=""; PKG_MGR=""
detect_os() {
  if [[ -r /etc/os-release ]]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    OS_ID="${ID:-}"
    OS_LIKE="${ID_LIKE:-}"
  fi
  for cand in apt-get dnf yum zypper pacman apk; do
    if command -v "$cand" >/dev/null 2>&1; then
      PKG_MGR="$cand"
      break
    fi
  done
  [[ -n "$PKG_MGR" ]] || die "未识别的包管理器，请手动安装 Docker 后重试"
}

pkg_install() {
  case "$PKG_MGR" in
    apt-get)
      $SUDO apt-get update -y
      DEBIAN_FRONTEND=noninteractive $SUDO apt-get install -y "$@"
      ;;
    dnf)   $SUDO dnf install -y "$@" ;;
    yum)   $SUDO yum install -y "$@" ;;
    zypper) $SUDO zypper --non-interactive install -y "$@" ;;
    pacman) $SUDO pacman -Sy --noconfirm "$@" ;;
    apk)   $SUDO apk add --no-cache "$@" ;;
  esac
}

ensure_base_tools() {
  local need=()
  command -v curl >/dev/null 2>&1 || need+=("curl")
  command -v git  >/dev/null 2>&1 || need+=("git")
  command -v openssl >/dev/null 2>&1 || need+=("openssl")
  command -v ca-certificates >/dev/null 2>&1 || true
  if [[ ${#need[@]} -gt 0 ]]; then
    log "安装基础工具: ${need[*]}"
    pkg_install "${need[@]}" || warn "部分基础工具安装失败，继续尝试"
  fi
}

###############################################################################
# Docker
###############################################################################
ensure_docker() {
  if command -v docker >/dev/null 2>&1; then
    ok "已检测到 Docker：$(docker --version)"
  else
    log "未检测到 Docker，开始安装"
    # Alpine 的 get.docker.com 兼容性较差 → 直接走包管理器
    if [[ "$PKG_MGR" == "apk" ]]; then
      pkg_install docker docker-cli-compose
      $SUDO rc-update add docker boot || true
      $SUDO service docker start || true
    else
      curl -fsSL https://get.docker.com | $SUDO sh \
        || die "Docker 安装失败。请按 https://docs.docker.com/engine/install/ 手动安装后重试"
    fi
  fi

  # Compose 插件
  if ! docker compose version >/dev/null 2>&1; then
    log "安装 docker compose 插件"
    case "$PKG_MGR" in
      apt-get) pkg_install docker-compose-plugin || pkg_install docker-compose ;;
      dnf|yum) pkg_install docker-compose-plugin || pkg_install docker-compose ;;
      zypper) pkg_install docker-compose ;;
      pacman) pkg_install docker-compose ;;
      apk)    pkg_install docker-cli-compose ;;
    esac
  fi
  docker compose version >/dev/null 2>&1 \
    || die "docker compose 插件不可用，请手动安装 docker-compose-plugin"

  # 启动 daemon
  if command -v systemctl >/dev/null 2>&1; then
    $SUDO systemctl enable docker >/dev/null 2>&1 || true
    $SUDO systemctl start docker  >/dev/null 2>&1 || true
  fi
}

###############################################################################
# 仓库
###############################################################################
sync_repo() {
  if [[ -d "$INSTALL_DIR/.git" ]]; then
    if [[ $DO_UPDATE -eq 1 ]]; then
      log "更新已有仓库：$INSTALL_DIR"
      $SUDO git -C "$INSTALL_DIR" fetch origin "$BRANCH" --depth=1 || die "git fetch 失败"
      $SUDO git -C "$INSTALL_DIR" reset --hard "origin/$BRANCH"
    else
      log "跳过仓库更新（--no-update）"
    fi
  else
    log "克隆仓库到 $INSTALL_DIR"
    $SUDO mkdir -p "$(dirname "$INSTALL_DIR")"
    $SUDO git clone --depth=1 --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
  fi
}

###############################################################################
# .env
###############################################################################
gen_secret() {
  # 优先 openssl，其次 /dev/urandom
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -base64 32 | tr -d '/+=\n' | cut -c1-32
  else
    head -c 24 /dev/urandom | base64 | tr -d '/+=\n' | cut -c1-32
  fi
}

ensure_env() {
  local env_file="$INSTALL_DIR/.env"
  if [[ -f "$env_file" ]]; then
    ok ".env 已存在，保持原值（如要重置请删除该文件再跑）"
    return
  fi

  log "生成 .env（含强随机密钥）"
  local master knock auth_pwd
  master=$(gen_secret)
  knock=$(gen_secret)
  auth_pwd=$(gen_secret | cut -c1-16)

  $SUDO tee "$env_file" >/dev/null <<EOF
# 由 deploy.sh 自动生成，请立刻保存以下凭据
MASTER_PASSWORD=$master

AUTH_USERNAME=admin
AUTH_PASSWORD=$auth_pwd
ACCESS_TOKEN=

KNOCK_SECRET=$knock

FRONTEND_PORT=$FRONTEND_PORT
TZ=Asia/Shanghai
NOTIFY_WEBHOOK_URL=
CORS_ORIGINS=http://localhost:$FRONTEND_PORT
EOF

  GENERATED_USERNAME="admin"
  GENERATED_PASSWORD="$auth_pwd"
  GENERATED_KNOCK="$knock"
}

###############################################################################
# Compose 启动
###############################################################################
compose_files() {
  local args=(-f "$INSTALL_DIR/docker-compose.yml")
  if [[ -n "$DOMAIN" ]]; then
    args+=(-f "$INSTALL_DIR/docker-compose.https.yml")
  fi
  printf '%s\n' "${args[@]}"
}

bring_up() {
  pushd "$INSTALL_DIR" >/dev/null

  if [[ -n "$DOMAIN" ]]; then
    log "HTTPS 模式：将注入 CADDY_DOMAIN=$DOMAIN CADDY_EMAIL=$EMAIL"
    $SUDO sed -i "/^CADDY_DOMAIN=/d;/^CADDY_EMAIL=/d" "$INSTALL_DIR/.env"
    echo "CADDY_DOMAIN=$DOMAIN" | $SUDO tee -a "$INSTALL_DIR/.env" >/dev/null
    echo "CADDY_EMAIL=$EMAIL"   | $SUDO tee -a "$INSTALL_DIR/.env" >/dev/null
  fi

  mapfile -t COMPOSE_ARGS < <(compose_files)

  if [[ $RESTART_ONLY -eq 1 ]]; then
    log "重启容器（不重建镜像）"
    $SUDO docker compose "${COMPOSE_ARGS[@]}" restart
  else
    log "构建并启动容器（首次需要几分钟）"
    $SUDO docker compose "${COMPOSE_ARGS[@]}" up -d --build
  fi
  popd >/dev/null
}

wait_health() {
  local target="http://127.0.0.1:$FRONTEND_PORT"
  if [[ -n "$DOMAIN" ]]; then target="https://$DOMAIN"; fi
  log "等待服务就绪：$target"
  for _ in $(seq 1 60); do
    if curl -fsSk "$target" >/dev/null 2>&1; then
      ok "服务已就绪"
      return
    fi
    sleep 2
  done
  warn "60s 内未探活到 $target ，可用 'docker compose logs -f backend' 看后端日志"
}

###############################################################################
# Summary
###############################################################################
print_summary() {
  local access
  if [[ -n "$DOMAIN" ]]; then
    access="https://$DOMAIN"
  else
    access="http://<本机或域名>:$FRONTEND_PORT"
  fi

  echo
  echo "============================================================"
  ok "CloudHelper 部署完成"
  echo "  目录: $INSTALL_DIR"
  echo "  访问: $access"

  if [[ -n "${GENERATED_USERNAME:-}" ]]; then
    echo "  登录用户名: $GENERATED_USERNAME"
    echo "  登录密码:   $GENERATED_PASSWORD"
    echo "  KNOCK_SECRET: $GENERATED_KNOCK"
    echo "  访问链接（带 key）: $access/?key=$GENERATED_KNOCK"
    warn "上述凭据仅本次输出，请立即保存到密码管理器"
    warn "如需更换：在服务器上执行 docker compose exec backend python manage.py reset-auth"
  else
    echo "  使用既有 .env 中的凭据；如忘记登录密码："
    echo "    docker compose exec backend python manage.py reset-auth"
    echo "  如需查看 / 轮换 knock："
    echo "    docker compose exec backend python manage.py show-knock"
    echo "    docker compose exec backend python manage.py rotate-knock"
  fi
  echo "============================================================"
}

###############################################################################
# Main
###############################################################################
detect_os
ensure_base_tools
ensure_docker
sync_repo
ensure_env
bring_up
wait_health
print_summary
