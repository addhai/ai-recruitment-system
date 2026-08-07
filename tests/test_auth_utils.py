"""密码哈希工具测试。"""
from src.api.auth import get_password_hash, verify_password


def test_hash_and_verify():
    hashed = get_password_hash("secret123")
    assert hashed != "secret123"
    assert verify_password("secret123", hashed) is True


def test_verify_wrong_password_fails():
    hashed = get_password_hash("secret123")
    assert verify_password("wrong", hashed) is False
