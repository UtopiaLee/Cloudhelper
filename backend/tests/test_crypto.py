"""加密 / 主密钥派生 单元测试。"""

import pytest

from app.core.crypto import Crypto


def test_crypt_roundtrip(tmp_path):
    c = Crypto("hello-world", tmp_path)
    enc = c.encrypt("secret payload")
    assert enc != "secret payload"
    assert c.decrypt(enc) == "secret payload"


def test_crypt_wrong_password_fails(tmp_path):
    c1 = Crypto("password-A", tmp_path)
    enc = c1.encrypt("data")
    # 同一个 salt（因为 tmp_path 已经写了 .master_salt），换密码就解不开
    c2 = Crypto("password-B", tmp_path)
    with pytest.raises(ValueError):
        c2.decrypt(enc)


def test_salt_persisted(tmp_path):
    c1 = Crypto("pw", tmp_path)
    enc = c1.encrypt("xx")
    # 第二次实例化用同一个 tmp_path，应该读到同一个 salt → 能解密
    c2 = Crypto("pw", tmp_path)
    assert c2.decrypt(enc) == "xx"


def test_unicode_password_and_data(tmp_path):
    c = Crypto("密码中文 \U0001f600", tmp_path)
    payload = "数据 🚀 with emoji and ünìcødé"
    assert c.decrypt(c.encrypt(payload)) == payload
