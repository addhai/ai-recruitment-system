"""
测试基础设施：在任何 src 导入前注入临时 SQLite 数据库，避免污染真实 recruitment.db。
"""
import os
import tempfile
from pathlib import Path

# 必须在 import src 之前设定，因为 database.py 在模块加载时即按 DATABASE_URL 创建 engine
_fd, _db_path = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ["DATABASE_URL"] = "sqlite:///" + Path(_db_path).as_posix()
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("LLM_API_KEY", "test-key")

import pytest
from fastapi.testclient import TestClient

from src.main import app
from src.models.database import init_db


init_db()  # 建表 + 种子 admin/hr 用户


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth_headers(client):
    """造一个 hr 账号并返回其鉴权头，供需要登录的接口测试使用。

    直接写库造账号，不走 /auth/register——该端点现在需要管理员权限，
    且造测试数据本就不该依赖被测的生产端点（见 tests/helpers.py 的说明）。
    """
    from tests.helpers import login_headers
    return login_headers(client, role="hr")


def teardown_module(module):
    # 清理临时测试库
    try:
        os.remove(_db_path)
    except OSError:
        pass
