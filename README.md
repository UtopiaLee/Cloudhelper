# CloudHelper

为白嫖党设计的多云免费账号管理工具。重点：**别让免费账号产生任何费用**。

## 当前进度（Phase 1）

- 支持 AWS Free Tier、GCP Always Free（Oracle / Azure 在 Phase 2/3）
- 多账号管理 + 分组标签 + 备注（如"到期 2026-12"）
- 实例列表 / 创建 / 启停 / 销毁 / 批量操作
- 防火墙规则（AWS Security Group / GCP VPC Firewall）
- 定时开关机（cron）
- **SSH 采集流量**：每 10 分钟从实例 `/proc/net/dev` 读字节数，与上次差值累加到月累计；超免费额度自动停机
- 月初 01:00 自动重启被流量停机的实例
- Dashboard 实时进度条 + 保活时间一目了然

## 完全零成本

| 数据 | 来源 | 成本 |
|------|------|------|
| 实例状态 | EC2/Compute API（按需调） | $0 |
| 流量统计 | **SSH 进实例读 /proc/net/dev** | $0 |
| 保活时间 | SSH 连接时间戳 | $0 |
| 实例操作 | EC2/Compute API | $0 |

**没有任何 CloudWatch / Monitoring / Cost Explorer / S3 / BigQuery 调用**。

## 快速上手

```powershell
copy .env.example .env
# 编辑 .env：MASTER_PASSWORD 改强密码
docker compose up -d --build
# 打开 http://localhost:8080
```

### 1. 创建 SSH 密钥

进 "SSH 密钥" 页 → 点 "生成 Ed25519" → 复制公钥。
**这把公钥会贴到所有云的实例里**，CloudHelper 用它登录采流量。

### 2. 添加云账号

"账户" 页 → "添加账号"：
- AWS：填 access_key_id + secret_access_key（IAM 用户最小权限：EC2 + STS）
- GCP：粘 Service Account JSON
- 标 "免费流量 GB/月"（AWS 1GB、GCP 1GB、Oracle 10TB）

### 3. 把 SSH 公钥加到云实例

- **AWS**：创建 EC2 时选 Key pair → 导入公钥；或开实例后改 `~/.ssh/authorized_keys`
- **GCP**：项目 → Compute Engine → Metadata → SSH Keys → 添加公钥
- **Oracle/Azure**（Phase 2+）：同样操作

### 4. 拉取实例

"实例" 页 → 点 "↻ 从云刷新"。每个实例显示当前状态。

### 5. 等 10 分钟看流量

调度器自动 SSH 每个 running 实例采流量。
- "总览" 看进度条
- "实例" 页可手动点 "采" 立即采集一次（调试用）
- 采集失败显示错误（最常见：SSH 用户不对，改实例那行的 `ssh_user` 列）

### SSH 用户怎么填？

| 镜像 | 用户 |
|------|------|
| Amazon Linux | `ec2-user` |
| Ubuntu | `ubuntu` |
| Debian | `admin` |
| CentOS / RHEL | `centos` |
| Oracle Linux | `opc` |

## 安全说明

- 凭据 + SSH 私钥都用 Fernet (AES-128-CBC + HMAC) 加密存 SQLite
- 主密钥由 `MASTER_PASSWORD` 经 PBKDF2-HMAC-SHA256 (480000 轮) 派生
- 建议：CloudHelper 仅本机访问 / 反代后台

## 目录结构

```
backend/app/
  api/         REST 路由
  core/        config / db / crypto / scheduler
  models/      SQLAlchemy
  providers/   AWS / GCP
  schemas/     Pydantic
  services/    audit / ssh_collector / scheduler_jobs
frontend/src/
  pages/       Dashboard / Accounts / Instances / Firewall / Schedules / SSHKeys / Audit
  lib/         api / account-context / ui
```

## 已知限制 / Phase 2 计划

- ⏳ Oracle Cloud provider（重点，免费 ARM 量大）
- ⏳ Azure provider
- ⏳ "保活探测"独立任务（SSH 失败时重试 + 记录）
- ⏳ 免费额度到期前提醒（按 `note` 里的日期解析）
- ⏳ 实例第一次启动后 IP 还没就绪 → 自动等待重试

## 故障排查

**采集失败 "no public ip"**：实例没分配公网 IP，改成有公网 IP 的，或者把 CloudHelper 跑在能内网通的机器。
**采集失败 "Authentication failed"**：SSH 用户不对，改实例行的 `ssh_user`。
**采集失败 "timed out"**：实例防火墙没开 22 端口（或自定义端口）。
