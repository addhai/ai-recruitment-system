"""Alembic 环境配置。

要点：
1. **连接串来自应用配置**（src.config.settings.DATABASE_URL），不写在 alembic.ini 里，
   避免同一份连接信息维护两处、也避免把凭据提交进版本库。
2. 开启 render_as_batch：SQLite 不支持大部分 ALTER TABLE，批量模式会走
   「建新表 → 拷数据 → 换名」，让同一份迁移脚本在 SQLite 与 PostgreSQL 上都能跑。
3. compare_type=True：改列类型时 autogenerate 能识别出来（默认识别不到）。
"""
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from src.config import settings
from src.models.database import Base

config = context.config

# 用应用配置覆盖连接串（alembic.ini 中该字段留空）
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

# 只在 CLI 场景配置日志。
# 应用启动时会以编程方式调用 alembic（src/models/database.py: migrate_database），
# 而 fileConfig 默认 disable_existing_loggers=True —— 在应用进程里执行会把
# 已配置好的日志器全部静默关掉，埋点日志就没了。调用方通过
# config.attributes["configure_logger"] = False 关掉它。
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """离线模式：只生成 SQL，不连库。"""
    context.configure(
        url=settings.DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：连库执行迁移。"""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
