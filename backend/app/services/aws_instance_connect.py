"""AWS EC2 Instance Connect：临时公钥推送 + 立即 SSH。

工作流：
  1. 本地生成 ephemeral Ed25519 keypair
  2. 调 ec2-instance-connect.send_ssh_public_key 把公钥推到目标实例（默认 60s 有效）
  3. 用对应私钥 SSH 上去
  4. 完成后无需清理（key 自动过期）

权限要求：调用的 AWS 凭据需要
  - ec2-instance-connect:SendSSHPublicKey
  - ec2:DescribeInstances（已有）

仅适用于 AWS。
"""

from __future__ import annotations

import io
import json
import logging
from typing import Optional, Tuple

import boto3
import paramiko

from app.core.crypto import get_crypto
from app.models import CloudAccount

log = logging.getLogger(__name__)


def push_ephemeral_key(account: CloudAccount, instance_id: str, region: str,
                       az: str, user: str) -> paramiko.PKey:
    """生成临时 keypair，把公钥推到实例，返回私钥 PKey 对象。

    az: availability zone (e.g. us-east-1a)。AWS Instance Connect 要求传 AZ。
    user: 推送的目标用户名（如 root / ubuntu / ec2-user）
    """
    creds = json.loads(get_crypto().decrypt(account.credentials_enc))
    eic = boto3.client(
        "ec2-instance-connect",
        region_name=region,
        aws_access_key_id=creds.get("access_key_id"),
        aws_secret_access_key=creds.get("secret_access_key"),
        aws_session_token=creds.get("session_token") or None,
    )

    # 生成临时 Ed25519
    pkey = paramiko.Ed25519Key.generate()
    pub_b64 = pkey.get_base64()
    public_key_str = f"{pkey.get_name()} {pub_b64} cloudhelper-eic"

    eic.send_ssh_public_key(
        InstanceId=instance_id,
        InstanceOSUser=user,
        SSHPublicKey=public_key_str,
        AvailabilityZone=az,
    )
    log.info("推送 EIC 公钥到 %s (%s@%s)", instance_id, user, az)
    return pkey


def is_eic_available(account: CloudAccount) -> bool:
    return account.provider == "aws"
