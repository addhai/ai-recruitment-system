"""checkpointer 后端选择：按 DATABASE_URL 选 postgres/sqlite，且不做静默降级。"""
import asyncio
import os

import pytest

from src.workflow import runner


class TestBackendSelection:
    @pytest.mark.parametrize("url,expected", [
        ("postgresql://u:p@h:5432/db", True),
        ("postgres://u:p@h:5432/db", True),
        ("postgresql+psycopg2://u:p@h:5432/db", True),
        ("sqlite:///./recruitment.db", False),
        ("sqlite:///tmp/x.db", False),
    ])
    def test_is_postgres(self, url, expected):
        assert runner._is_postgres(url) is expected

    @pytest.mark.parametrize("url,expected", [
        # SQLAlchemy 允许带驱动后缀，psycopg 不认，必须去掉
        ("postgresql+psycopg2://u:p@h:5432/db", "postgresql://u:p@h:5432/db"),
        ("postgresql+asyncpg://u:p@h/db", "postgresql://u:p@h/db"),
        ("postgresql://u:p@h:5432/db", "postgresql://u:p@h:5432/db"),
    ])
    def test_pg_conninfo_strips_driver_suffix(self, url, expected):
        assert runner._pg_conninfo(url) == expected

    def test_conninfo_passthrough_without_scheme(self):
        assert runner._pg_conninfo("not-a-url") == "not-a-url"


class TestNoSilentFallback:
    """postgres 模式初始化失败必须报错，绝不能偷偷退回本地 sqlite 文件。

    多副本下退回本地文件会让副本间的挂起状态互不可见——A 启动的流程在 B 上
    resume 找不到状态，表现为"进行中的流程莫名卡住"，且日志没有明显错误。
    """

    def _reset(self, monkeypatch):
        monkeypatch.setattr(runner, "_graph", None)
        monkeypatch.setattr(runner, "_saver", None)
        monkeypatch.setattr(runner, "_pool", None, raising=False)

    def test_postgres_init_failure_raises_and_leaves_no_saver(self, monkeypatch):
        self._reset(monkeypatch)
        monkeypatch.setattr(runner.settings, "DATABASE_URL",
                            "postgresql://u:p@127.0.0.1:1/db")
        # 单独测"连接池初始化失败"这条路径，把平台相关的循环守卫排除在外
        monkeypatch.setattr(runner, "_psycopg_incompatible_loop", lambda: False)

        import psycopg_pool

        class _Boom:
            # langgraph 在导入时会对 AsyncConnectionPool 做类型下标
            # （Conn = AsyncConnectionPool[AsyncConnection[DictRow]]），
            # 替身必须支持 __class_getitem__ 否则会先把 import 弄崩
            def __class_getitem__(cls, item):
                return cls

            def __init__(self, *a, **kw):
                pass

            async def open(self):
                raise RuntimeError("cannot reach postgres")

            async def close(self):
                pass

        monkeypatch.setattr(psycopg_pool, "AsyncConnectionPool", _Boom)

        with pytest.raises(RuntimeError, match="cannot reach postgres"):
            asyncio.run(runner.get_graph())

        assert runner._saver is None, "不能退回 sqlite checkpointer"
        assert runner._graph is None
        assert getattr(runner, "_pool", None) is None, "失败后必须关掉连接池"

    def test_sqlite_backend_used_for_sqlite_url(self, monkeypatch, tmp_path):
        self._reset(monkeypatch)
        monkeypatch.setattr(runner.settings, "DATABASE_URL",
                            f"sqlite:///{(tmp_path / 'cp.db').as_posix()}")
        monkeypatch.setattr(runner, "_CHECKPOINT_DB",
                            str(tmp_path / "cp.db"))

        async def _noop_setup():
            return None

        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        monkeypatch.setattr(AsyncSqliteSaver, "setup", lambda self: _noop_setup())

        asyncio.run(runner.get_graph())
        assert isinstance(runner._saver, AsyncSqliteSaver)
        asyncio.run(runner.close_graph())


class TestProactorLoopGuard:
    """Windows 的 ProactorEventLoop 不被 psycopg 异步支持，需前置拦截。

    真实部署是 Linux/容器（默认 Selector），不受影响；这里保证命中该组合时
    立刻给出可操作的原因，而不是等连接池 30 秒超时。
    """

    def test_pure_helper_ignores_non_windows(self):
        # 用纯函数断言，避免 monkeypatch 全局 os.name 影响其它模块
        assert runner._loop_is_incompatible("posix", object()) is False
        assert runner._loop_is_incompatible("linux", object()) is False

    def test_pure_helper_handles_none_loop(self):
        assert runner._loop_is_incompatible("nt", None) is False

    @pytest.mark.skipif(os.name != "nt", reason="仅 Windows 有 ProactorEventLoop")
    def test_pure_helper_flags_proactor_only(self):
        proactor = asyncio.ProactorEventLoop()
        selector = asyncio.SelectorEventLoop()
        try:
            assert runner._loop_is_incompatible("nt", proactor) is True
            assert runner._loop_is_incompatible("nt", selector) is False
        finally:
            proactor.close()
            selector.close()

    @pytest.mark.skipif(os.name != "nt", reason="仅 Windows 有 ProactorEventLoop")
    def test_real_loop_detection(self):
        async def _check():
            return runner._psycopg_incompatible_loop()

        assert asyncio.run(_check(), loop_factory=asyncio.ProactorEventLoop) is True
        assert asyncio.run(_check(), loop_factory=asyncio.SelectorEventLoop) is False
