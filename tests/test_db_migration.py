"""迁移纳管路径的回归测试。

这里固定一个**极隐蔽的失败模式**：旧库纳管时如果 stamp 到 head（而不是基线），
旧库会被标记为"已是最新"，从而跳过基线之后的每一个迁移——库显示已迁移、
实际缺列，而且此后 upgrade 也修不回来（它认为自己已在 head）。
只有 baseline 一个版本时这个 bug 完全看不出来，加了第二个迁移才暴露。
"""
import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from src.models import database


@pytest.fixture
def legacy_db(tmp_path, monkeypatch):
    """造一个"alembic 之前建的库"：结构停在基线，且没有 alembic_version。"""
    path = tmp_path / "legacy.db"
    url = f"sqlite:///{path.as_posix()}"
    eng = create_engine(url)
    monkeypatch.setattr(database, "engine", eng)
    monkeypatch.setattr(database.settings, "DATABASE_URL", url)

    cfg = database._alembic_config()
    baseline = ScriptDirectory.from_config(cfg).get_bases()[0]
    command.upgrade(cfg, baseline)          # 只升到基线，模拟旧库
    with eng.begin() as conn:                # 抹掉版本表 = 纳管前的状态
        conn.execute(text("DROP TABLE alembic_version"))
    yield eng, cfg, baseline
    eng.dispose()


def _columns(eng, table):
    return {c["name"] for c in inspect(eng).get_columns(table)}


def _version(eng):
    with eng.connect() as conn:
        return conn.execute(text("SELECT version_num FROM alembic_version")).scalar()


class TestLegacyAdoption:
    def test_legacy_db_starts_below_head(self, legacy_db):
        """前置确认：造出来的库确实缺 thread_id、且没有版本记录"""
        eng, _, _ = legacy_db
        assert "alembic_version" not in inspect(eng).get_table_names()
        assert "thread_id" not in _columns(eng, "llm_call_logs")

    def test_adoption_applies_migrations_after_baseline(self, legacy_db):
        """纳管必须 stamp 到基线，让基线之后的迁移照常执行"""
        eng, _, _ = legacy_db
        database.migrate_database()

        assert "thread_id" in _columns(eng, "llm_call_logs"), \
            "纳管后必须补上基线之后新增的列（stamp head 会漏掉它）"
        assert "alembic_version" in inspect(eng).get_table_names()

    def test_adoption_ends_at_head(self, legacy_db):
        eng, cfg, _ = legacy_db
        database.migrate_database()
        assert _version(eng) == ScriptDirectory.from_config(cfg).get_current_head()

    def test_adoption_is_idempotent(self, legacy_db):
        """第二次调用不该报错，也不该重复建列"""
        eng, cfg, _ = legacy_db
        database.migrate_database()
        first = _version(eng)
        database.migrate_database()
        assert _version(eng) == first

    def test_fresh_db_gets_full_schema(self, tmp_path, monkeypatch):
        """全新库（无表）直接 upgrade 到最新"""
        url = f"sqlite:///{(tmp_path / 'fresh.db').as_posix()}"
        eng = create_engine(url)
        monkeypatch.setattr(database, "engine", eng)
        monkeypatch.setattr(database.settings, "DATABASE_URL", url)

        database.migrate_database()

        tables = set(inspect(eng).get_table_names())
        assert {"users", "llm_call_logs", "positions", "alembic_version"} <= tables
        assert "thread_id" in _columns(eng, "llm_call_logs")
        eng.dispose()

    def test_schema_matches_models_after_migration(self, legacy_db):
        """迁移跑完后，库结构与模型定义不应有差异"""
        eng, _, _ = legacy_db
        database.migrate_database()
        insp = inspect(eng)
        for table in ("llm_call_logs", "candidates", "users"):
            want = {c.name for c in database.Base.metadata.tables[table].columns}
            got = {c["name"] for c in insp.get_columns(table)}
            assert want == got, f"{table} 列与模型不一致"
