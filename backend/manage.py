"""CloudHelper 离线管理工具：重置登录账号密码 / 轮换 knock。

使用：
    python manage.py reset-auth                          # 用户名保持，密码随机
    python manage.py reset-auth --username admin         # 指定用户名，密码随机
    python manage.py reset-auth --password 自定义密码    # 指定密码（不推荐写命令行历史）
    python manage.py rotate-knock                        # 轮换 knock secret，打印一次
    python manage.py show-knock                          # 仅显示当前 knock

安全说明：
    - 命令必须在能读写本仓库 .env 的机器上执行
    - 新值仅在本次执行的终端打印一次，请立即保存到密码管理器
    - 不会输出已存在的登录密码（明文密码并不存储，无法回显）
    - 设置/轮换后，后端进程要重启或在运行时让 .env 重新加载
"""

from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

from app.core.config import reload_settings, update_env_vars  # noqa: E402


def _gen_password(length: int = 20) -> str:
    return secrets.token_urlsafe(length)[:length]


def _gen_knock(length: int = 24) -> str:
    return secrets.token_urlsafe(length)


def cmd_reset_auth(args: argparse.Namespace) -> int:
    username = (args.username or "admin").strip()
    if not username:
        print("❌ 用户名不能为空", file=sys.stderr)
        return 2
    password = args.password or _gen_password()
    if len(password) < 6:
        print("❌ 密码至少 6 位", file=sys.stderr)
        return 2

    env_path = update_env_vars({
        "AUTH_USERNAME": username,
        "AUTH_PASSWORD": password,
    })
    reload_settings()

    print("=" * 60)
    print("✅ 登录账号已重置（写入 %s）" % env_path)
    print(f"   用户名: {username}")
    print(f"   密  码: {password}")
    print("=" * 60)
    print("⚠ 仅本次显示，请立即保存到密码管理器。")
    print("⚠ 后端进程需重启才能完全生效（或在运行时由代码 reload_settings）。")
    return 0


def cmd_rotate_knock(_: argparse.Namespace) -> int:
    new_secret = _gen_knock()
    env_path = update_env_vars({"KNOCK_SECRET": new_secret})
    reload_settings()

    print("=" * 60)
    print("✅ KNOCK_SECRET 已轮换（写入 %s）" % env_path)
    print(f"   {new_secret}")
    print("=" * 60)
    print("访问链接：http://localhost:5173/?key=%s" % new_secret)
    print("⚠ 旧密钥立即失效。后端进程需重启才会让内存中的 knock 同步。")
    return 0


def cmd_show_knock(_: argparse.Namespace) -> int:
    from app.core.config import get_settings

    reload_settings()
    secret = get_settings().knock_secret.strip()
    if not secret:
        print("当前 .env 未配置 KNOCK_SECRET（后端启动时会随机生成，每次重启变化）", file=sys.stderr)
        return 1
    print(secret)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="manage.py", description="CloudHelper 离线管理工具")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_reset = sub.add_parser("reset-auth", help="重置登录账号密码（密码默认随机）")
    p_reset.add_argument("--username", help="新用户名，默认 admin")
    p_reset.add_argument("--password", help="自定义密码（不传则随机）")
    p_reset.set_defaults(func=cmd_reset_auth)

    p_rotate = sub.add_parser("rotate-knock", help="轮换 KNOCK_SECRET")
    p_rotate.set_defaults(func=cmd_rotate_knock)

    p_show = sub.add_parser("show-knock", help="显示当前 KNOCK_SECRET（不显示密码）")
    p_show.set_defaults(func=cmd_show_knock)

    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
