"""主密钥派生 + Fernet 字段加密。

启动时由用户提供主密码（环境变量 MASTER_PASSWORD），
经 PBKDF2-HMAC-SHA256 派生为 Fernet key，
凭据写库前用此 key 加密，读出后解密。
盐值持久化在 data_dir 下，确保重启后能解密历史数据。
"""

from __future__ import annotations

import base64
import os
import secrets
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

PBKDF2_ITERATIONS = 480_000
SALT_FILENAME = ".master_salt"


def _load_or_create_salt(data_dir: Path) -> bytes:
    data_dir.mkdir(parents=True, exist_ok=True)
    salt_path = data_dir / SALT_FILENAME
    if salt_path.exists():
        return salt_path.read_bytes()
    salt = secrets.token_bytes(16)
    salt_path.write_bytes(salt)
    os.chmod(salt_path, 0o600)
    return salt


def _derive_key(password: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    return base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8")))


class Crypto:
    def __init__(self, password: str, data_dir: Path):
        salt = _load_or_create_salt(data_dir)
        self._fernet = Fernet(_derive_key(password, salt))

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, token: str) -> str:
        try:
            return self._fernet.decrypt(token.encode("ascii")).decode("utf-8")
        except InvalidToken as e:
            raise ValueError("主密钥错误或数据已损坏") from e


_crypto: Crypto | None = None


def init_crypto(password: str, data_dir: Path) -> Crypto:
    global _crypto
    _crypto = Crypto(password, data_dir)
    return _crypto


def get_crypto() -> Crypto:
    if _crypto is None:
        raise RuntimeError("Crypto 未初始化，先调用 init_crypto")
    return _crypto
