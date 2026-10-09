"""接口层的两项收敛：分页上限、API 文档开关。"""
import pytest

from src.api import pagination
from src.main import _docs_kwargs
from tests.helpers import login_headers


class TestPaginationParams:
    """5 个接口此前是 query.all()——没有上限，会把整表塞进一次响应。

    统一到 skip/limit 后要确认两件事：上界真的生效（否则传 limit=10_000_000
    等于没分页），以及默认值不把现有数据截断。
    """

    def test_limit_over_max_rejected(self, client, auth_headers):
        r = client.get("/positions/", params={"limit": 10_000_000},
                       headers=auth_headers)
        assert r.status_code == 422, "上界必须由服务端强制"

    def test_negative_skip_rejected(self, client, auth_headers):
        r = client.get("/positions/", params={"skip": -1}, headers=auth_headers)
        assert r.status_code == 422

    def test_limit_zero_rejected(self, client, auth_headers):
        r = client.get("/positions/", params={"limit": 0}, headers=auth_headers)
        assert r.status_code == 422

    def test_max_page_size_constant_is_bounded(self):
        assert 1 <= pagination.MAX_PAGE_SIZE <= 10000, \
            "上限要小到能防全表倾倒，又要大到不误伤真实数据量"

    @pytest.mark.parametrize("path", [
        "/positions/", "/questionnaires/", "/evaluations/",
        "/talent-pool/", "/reviews/", "/candidates/", "/interviews/",
    ])
    def test_all_list_endpoints_accept_pagination(self, client, auth_headers, path):
        """全部列表接口口径一致：不接受分页参数就说明还是全量返回"""
        r = client.get(path, params={"skip": 0, "limit": 1},
                       headers=login_headers(client, role="admin"))
        assert r.status_code == 200, r.text
        assert len(r.json()) <= 1

    def test_limit_actually_caps_results(self, client):
        """造 3 条岗位，limit=2 必须只回 2 条"""
        h = login_headers(client, role="admin")
        for i in range(3):
            r = client.post("/positions/", json={"title": f"分页测试岗位{i}"},
                            headers=h)
            assert r.status_code == 200, r.text

        r = client.get("/positions/", params={"limit": 2}, headers=h)
        assert r.status_code == 200
        assert len(r.json()) == 2


class TestApiDocsToggle:
    def test_enabled_by_default_returns_no_override(self, monkeypatch):
        monkeypatch.setattr("src.main.settings.ENABLE_API_DOCS", True)
        assert _docs_kwargs() == {}

    def test_disabled_turns_off_docs_and_schema(self, monkeypatch):
        monkeypatch.setattr("src.main.settings.ENABLE_API_DOCS", False)
        kwargs = _docs_kwargs()
        # 三个都要关：只关 /docs 而留着 /openapi.json，schema 依然可读
        assert kwargs == {"docs_url": None, "redoc_url": None, "openapi_url": None}

    def test_running_app_exposes_docs_when_enabled(self, client):
        """当前测试环境默认开启，联调脚本依赖 /openapi.json"""
        assert client.get("/openapi.json").status_code == 200
