# CloudHelper Docker 部署

## 一键启动

```bash
# 1. 复制配置
cp .env.example .env

# 2. 编辑 .env，必须改 MASTER_PASSWORD（强随机），强烈建议设 ACCESS_TOKEN
nano .env   # 或用任何编辑器

# 3. 构建并启动
docker compose up -d --build

# 4. 查看日志
docker compose logs -f
```

打开浏览器访问 `http://你的服务器IP:8080`，输入 `ACCESS_TOKEN` 登录（如果设了）。

## 数据存放

`./data` 目录挂到容器 `/data`：
- `cloudhelper.db` - SQLite 数据库（账号/实例/流量/审计）
- `.master_salt` - 主密钥派生盐（**重要！丢了所有云凭据无法解密**）

迁移到新机器只需：
1. 整个项目目录 + `./data` 目录复制过去
2. `.env` 的 `MASTER_PASSWORD` 必须保持**完全一致**（盐 + 密码组成解密密钥）
3. `docker compose up -d`

## 常用操作

```bash
# 重启
docker compose restart

# 升级（拉新代码后）
docker compose up -d --build

# 停止
docker compose down

# 完全清理（保留数据）
docker compose down --rmi local

# 销毁所有（含数据）—— 危险
docker compose down -v
rm -rf data/

# 进入后端 shell
docker compose exec backend bash

# 跑测试
docker compose exec backend pytest tests
```

## 端口

- `8080` - Web UI（可在 `.env` 改 `FRONTEND_PORT`）
- 后端 8000 仅内部，不对外暴露

## 反向代理 / HTTPS（生产用）

CloudHelper 提供 **3 种 HTTPS 方案**，按场景选：

| 场景 | 方案 |
|------|------|
| 有域名 + 服务器能对公网开 80/443 | **方案 A**：Caddy 自动 LE |
| 已有证书文件（手动签 / Cloudflare Origin / 内网自签） | **方案 B**：手动证书 overlay |
| 已有 Nginx/Caddy 反代在前面 | **方案 C**：默认 80，反代到上层 |

### 方案 A：Caddy 自动 HTTPS

前置：
1. 你有一个域名（如 `cloudhelper.example.com`）
2. 域名 A 记录指向服务器公网 IP
3. 服务器 80/443 端口对外开放

```bash
echo 'CADDY_DOMAIN=cloudhelper.example.com' >> .env
echo 'CADDY_EMAIL=you@example.com' >> .env
echo 'ACCESS_TOKEN=一个长随机字符串' >> .env  # 公网必须设
docker compose -f docker-compose.yml -f docker-compose.https.yml up -d --build
```

特性：自动 LE 申请 / 续期 / HTTP→HTTPS / HTTP/2 + HTTP/3 / 安全头 / 移除 Server 暴露。

### 方案 B：手动证书（推荐自签 / Cloudflare / 已有 LE 用这个）

适合：
- 已经用 certbot 跑过 `letsencrypt`，证书在 `/etc/letsencrypt/live/...`
- Cloudflare Origin Certificate（CF 签发的 15 年证书，仅 CF → 你服务器之间用）
- 内网自签证书（mkcert / openssl）
- 公司内部 CA 颁发的证书

```bash
echo 'TLS_CERT_PATH=/etc/letsencrypt/live/example.com/fullchain.pem' >> .env
echo 'TLS_KEY_PATH=/etc/letsencrypt/live/example.com/privkey.pem' >> .env
echo 'ACCESS_TOKEN=一个长随机字符串' >> .env
docker compose -f docker-compose.yml -f docker-compose.tls.yml up -d --build
```

容器会同时监听 80（跳转到 443）和 443（TLS）。

**自签证书生成示例**：
```bash
# 自己生成一对自签证书（浏览器会警告，但本机内网用够了）
mkdir -p /opt/cloudhelper/ssl
openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
  -keyout /opt/cloudhelper/ssl/key.pem \
  -out /opt/cloudhelper/ssl/cert.pem \
  -subj "/CN=cloudhelper.local"
# .env:
# TLS_CERT_PATH=/opt/cloudhelper/ssl/cert.pem
# TLS_KEY_PATH=/opt/cloudhelper/ssl/key.pem
```

**证书更新**：
- LE 证书：certbot 自动续期，无需操作（nginx 直接读文件）
- 续期后 `docker compose restart frontend` 让 nginx 重读
- 或写个 cron：`0 3 * * * docker compose -f docker-compose.yml -f docker-compose.tls.yml restart frontend`

### 方案 C：用你已有的反代

继续用你的 Caddy / Nginx / Traefik，按下面的 Nginx 配置改：

```nginx
server {
  listen 443 ssl http2;
  server_name cloudhelper.example.com;

  ssl_certificate /path/to/cert.pem;
  ssl_certificate_key /path/to/key.pem;

  location / {
    proxy_pass http://127.0.0.1:8080;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 300s;
  }
}
```

## 注意事项

1. **MASTER_PASSWORD 必须备份**，丢了无法解密已存的云凭据
2. **公网部署务必设 ACCESS_TOKEN**，否则任何人能访问拿到所有云凭据
3. SSH 密钥 / 实例密码都加密存 SQLite，但备份 `data/` 目录时要小心
4. 默认 SQLite 单文件，并发不高的场景足够用；超大规模可改 PostgreSQL
