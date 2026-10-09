from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, ForeignKey, JSON, Boolean, Float
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime
from pathlib import Path
from src.config import settings

engine = create_engine(settings.DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, nullable=False)
    email = Column(String(100), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(20), default="viewer")
    full_name = Column(String(100))
    department = Column(String(50))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    # 令牌版本：令牌里带签发时的版本号，与它不一致即失效（改密/停用时 +1）。
    # 用版本号而不是时间戳比对，是为了绕开精度问题：时间戳方案下"同一秒内签发的
    # 旧令牌"会逃过撤销，而把精度提到微秒又要求 iat 用浮点、与 JWT 的秒级惯例不符。
    # 版本号没有时钟参与，判定是精确的。
    token_version = Column(Integer, default=0, nullable=False, server_default="0")
    # 是否启用。停用代替删除：interviews.interviewer_id / evaluations.evaluator_id /
    # questionnaires.created_by 等外键指向本表且未声明 ondelete，
    # 硬删除在 PostgreSQL 上会直接违反外键（SQLite 默认不强制，两边行为还不一致），
    # 且会让历史评估变成指向不存在的人——"谁做的评估"必须一直可追溯。
    is_active = Column(Boolean, default=True, nullable=False, server_default="1")

    interviews = relationship("Interview", back_populates="interviewer")
    questionnaires = relationship("Questionnaire", back_populates="created_by_user")
    evaluations = relationship("Evaluation", back_populates="evaluator")


class Position(Base):
    """岗位：招聘需求本身，可先建岗位再补 JD。

    与 JobDescription 分两层：岗位承载"要招什么人"（名称、部门、编制），
    JD 承载"具体要求"。一个岗位可以先没有 JD，此时不能绑定候选人跑匹配；
    JD 可有多个版本（如面向不同渠道的精简版/完整版）。
    """
    __tablename__ = "positions"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(100), nullable=False)
    department = Column(String(100))
    location = Column(String(100))
    headcount = Column(Integer)
    description = Column(Text)
    # draft: 草稿 | active: 招聘中 | archived: 已关闭
    status = Column(String(20), default="draft")
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    job_descriptions = relationship("JobDescription", back_populates="position")


class JobDescription(Base):
    """岗位 JD：人岗匹配的真实依据。

    此前匹配用的是 candidate.position 这个岗位名字符串，等于拿简历自述的职责
    去匹配简历自己。现在要求先录入 JD、AI 解析成结构化画像、人工核对后启用。
    """
    __tablename__ = "job_descriptions"

    id = Column(Integer, primary_key=True, index=True)
    position_id = Column(Integer, ForeignKey("positions.id"))
    title = Column(String(100), nullable=False)
    department = Column(String(100))
    # draft: 已录入未启用 | active: 启用中，可被候选人绑定 | archived: 停用
    status = Column(String(20), default="draft")
    # pending: 待解析 | parsed: 解析成功 | failed: 解析失败（不可启用）
    parse_status = Column(String(20), default="pending")
    parse_error = Column(Text)
    raw_text = Column(Text)
    parsed_data = Column(JSON)
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    position = relationship("Position", back_populates="job_descriptions")
    candidates = relationship("Candidate", back_populates="job_description")


class Candidate(Base):
    __tablename__ = "candidates"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    email = Column(String(100), unique=True)
    phone = Column(String(20))
    resume_file = Column(String(255))
    resume_text = Column(Text)
    status = Column(String(20), default="pending")
    source = Column(String(50))
    position = Column(String(100))
    # 绑定的岗位 JD；同一人投不同岗位按岗位拆成多条候选档案
    job_description_id = Column(Integer, ForeignKey("job_descriptions.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # cascade：删除候选人时级联清理全部关联业务行，避免孤儿数据
    resume = relationship("Resume", back_populates="candidate", uselist=False, cascade="all, delete-orphan")
    interviews = relationship("Interview", back_populates="candidate", cascade="all, delete-orphan")
    questionnaire_responses = relationship("QuestionnaireResponse", back_populates="candidate", cascade="all, delete-orphan")
    evaluations = relationship("Evaluation", back_populates="candidate", cascade="all, delete-orphan")
    talent_pool = relationship("TalentPool", back_populates="candidate", uselist=False, cascade="all, delete-orphan")
    workflow_runs = relationship("WorkflowRun", back_populates="candidate", cascade="all, delete-orphan")
    job_description = relationship("JobDescription", back_populates="candidates")


class Resume(Base):
    __tablename__ = "resumes"

    id = Column(Integer, primary_key=True, index=True)
    candidate_id = Column(Integer, ForeignKey("candidates.id"))
    file_name = Column(String(255))
    file_path = Column(String(255))
    parsed_data = Column(JSON)
    skills = Column(JSON)
    experience = Column(Text)
    education = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    candidate = relationship("Candidate", back_populates="resume")


class Interview(Base):
    __tablename__ = "interviews"

    id = Column(Integer, primary_key=True, index=True)
    candidate_id = Column(Integer, ForeignKey("candidates.id"))
    position = Column(String(100))
    round = Column(Integer, default=1)
    status = Column(String(20), default="scheduled")
    interviewer_id = Column(Integer, ForeignKey("users.id"))
    scheduled_at = Column(DateTime)
    completed_at = Column(DateTime)
    notes = Column(Text)
    score = Column(Integer)
    feedback = Column(Text)
    questions = Column(JSON)  # AI 生成的该轮面试题 [{question, focus}]
    created_at = Column(DateTime, default=datetime.utcnow)

    candidate = relationship("Candidate", back_populates="interviews")
    interviewer = relationship("User", back_populates="interviews")


class Questionnaire(Base):
    __tablename__ = "questionnaires"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    type = Column(String(30), default="technical")
    questions = Column(JSON)
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    created_by_user = relationship("User", back_populates="questionnaires")
    responses = relationship("QuestionnaireResponse", back_populates="questionnaire")

class QuestionnaireResponse(Base):
    __tablename__ = "questionnaire_responses"

    id = Column(Integer, primary_key=True, index=True)
    candidate_id = Column(Integer, ForeignKey("candidates.id"))
    questionnaire_id = Column(Integer, ForeignKey("questionnaires.id"))
    responses = Column(JSON)
    score = Column(Integer)
    status = Column(String(20), default="completed")
    submitted_at = Column(DateTime, default=datetime.utcnow)

    candidate = relationship("Candidate", back_populates="questionnaire_responses")
    questionnaire = relationship("Questionnaire", back_populates="responses")


class Evaluation(Base):
    __tablename__ = "evaluations"

    id = Column(Integer, primary_key=True, index=True)
    candidate_id = Column(Integer, ForeignKey("candidates.id"))
    evaluator_id = Column(Integer, ForeignKey("users.id"))
    dimension = Column(String(50))
    score = Column(Integer)
    comment = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    candidate = relationship("Candidate", back_populates="evaluations")
    evaluator = relationship("User", back_populates="evaluations")


class TalentPool(Base):
    __tablename__ = "talent_pool"

    id = Column(Integer, primary_key=True, index=True)
    candidate_id = Column(Integer, ForeignKey("candidates.id"))
    status = Column(String(20), default="active")
    tags = Column(JSON)
    notes = Column(Text)
    last_contact = Column(DateTime)
    next_contact = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    candidate = relationship("Candidate", back_populates="talent_pool")


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"

    id = Column(Integer, primary_key=True, index=True)
    candidate_id = Column(Integer, ForeignKey("candidates.id"))
    status = Column(String(20), default="running")
    current_step = Column(String(50))
    progress = Column(Integer, default=0)
    results = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    candidate = relationship("Candidate", back_populates="workflow_runs")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _ensure_columns():
    """【仅用于 alembic 纳管前的旧库】轻量列迁移：补齐缺失列、执行历史列重命名。

    新增这个函数的年代没有迁移框架：create_all 不会给已存在的旧表加列，
    所以靠它检查后补列。现在 schema 由 migrations/ 驱动，本函数只在
    migrate_database() 把**旧库**纳入 alembic 管理前跑一次，之后不再需要。

    新库不会走到这里（没有旧表），因此不必再扩这张表；
    今后的结构变更请写成 migrations/versions/ 下的正式迁移。
    """
    from sqlalchemy import inspect, text
    migrations = {
        "interviews": [("questions", "JSON")],
        "candidates": [("job_description_id", "INTEGER")],
        "job_descriptions": [("position_id", "INTEGER")],
        "llm_call_logs": [("currency", "VARCHAR(8)"), ("model_served", "VARCHAR(64)")],
    }
    # 列重命名：cost_usd 在引入多币种后名不副实（DeepSeek 报价为人民币）
    renames = {
        "llm_call_logs": [("cost_usd", "cost")],
    }

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table, cols in migrations.items():
            if table not in existing_tables:
                continue  # 新库 create_all 已建全，无需迁移
            existing_cols = {c["name"] for c in inspector.get_columns(table)}
            for col_name, col_type in cols:
                if col_name not in existing_cols:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}"))
                    print(f"[db] 迁移：{table} 新增列 {col_name}")

        for table, pairs in renames.items():
            if table not in existing_tables:
                continue
            existing_cols = {c["name"] for c in inspector.get_columns(table)}
            for old_name, new_name in pairs:
                if old_name in existing_cols and new_name not in existing_cols:
                    conn.execute(text(f"ALTER TABLE {table} RENAME COLUMN {old_name} TO {new_name}"))
                    print(f"[db] 迁移：{table} 重命名列 {old_name} -> {new_name}")


class KnowledgeDocument(Base):
    """知识库自定义文档。

    此前自定义文档只存在内存列表 _extra_documents 中，重启即丢：
    文档列表不显示、BM25 关键词检索丢失它们；若期间触发向量库维度重建，
    还会从向量库里一并消失（因为重建用的 split_docs 已不含这些文档）。
    此处落库持久化。
    """
    __tablename__ = "knowledge_documents"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(200), nullable=False)
    content = Column(Text, nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class LLMCallLog(Base):
    """LLM 调用埋点：token 用量、耗时、成本、成功/降级状态。

    只记录调用元数据，不落 prompt 全文与模型输出全文——简历含 PII，
    日志留存应最小化；排查具体内容用 candidate_id 关联业务表。
    """
    __tablename__ = "llm_call_logs"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    call_site = Column(String(64), index=True)     # parse_resume / evaluate_skill_match / knowledge_qa …
    candidate_id = Column(Integer, index=True)     # 非候选人链路可空
    # 所属工作流运行的 thread_id（见 WorkflowRun.results）。
    # 一次简历上传会触发 4~8 次 LLM 调用，只有 candidate_id 时同一候选人重跑就分不开；
    # 有了它才能把"一次运行"的调用串成一条链来排查。
    thread_id = Column(String(64), index=True)
    model = Column(String(64))                    # 请求的模型名（配置里填的）
    # 供应商实际提供服务的模型版本。DeepSeek 会按模型名路由到具体版本
    # （如 deepseek-flash -> DeepSeek-V4.1-Flash），只记请求名会让
    # "这个分数是哪一版模型打出来的"无从追溯——评分不可复现时这是关键线索。
    model_served = Column(String(64))
    base_url = Column(String(128))
    input_tokens = Column(Integer)
    output_tokens = Column(Integer)
    # 成本以 currency 标明的币种计价（DeepSeek 官方报价为人民币）
    cost = Column(Float)
    currency = Column(String(8))
    latency_ms = Column(Integer)
    status = Column(String(20))                   # ok / failed / budget_blocked
    degraded = Column(Boolean, default=False)      # 是否走了 default 兜底分
    usage_missing = Column(Boolean, default=False)  # 供应商未返回 token 用量
    prompt_hash = Column(String(64))              # prompt 前 500 字符的 sha256
    error = Column(Text)


class LLMSettings(Base):
    """模型接入配置（单行表，id 恒为 1）。

    项目不是模型中转站，不预置某家供应商的固定接入：由使用者在界面上
    填写模型名、API Key、Base URL 与单价，后端据此切换。
    .env 中的配置作为兜底默认值。

    API Key 用 SECRET_KEY 派生的密钥加密存储，接口永不回传明文
    （只回传掩码预览），避免密钥随接口响应或日志泄露。
    """
    __tablename__ = "llm_settings"

    id = Column(Integer, primary_key=True)
    model = Column(String(100))
    base_url = Column(String(200))
    api_key_encrypted = Column(Text)
    # 单价：每百万 token，币种由 currency 决定
    input_price = Column(Float)
    output_price = Column(Float)
    currency = Column(String(8), default="CNY")
    # 高峰期价格倍数（DeepSeek 高峰 = 空闲 × 2；其它供应商填 1.0 表示无分时计价）
    peak_multiplier = Column(Float, default=1.0)
    # 预留：温度、超时等可按模型调整的参数
    temperature = Column(Float)
    timeout_seconds = Column(Integer)
    updated_by = Column(Integer, ForeignKey("users.id"))
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Cooldown(Base):
    """跨副本 / 跨重启共享的「冷却期」记录（登录限流 + LLM 熔断）。

    此前这两处状态只在进程内存里：多副本时各副本独立计数（实际阈值 ≈ 副本数 ×
    配置值）、进程重启即清零。这里落库消除该局限。

    **为什么用数据库而不是 Redis**：限流/熔断是加固手段，为它引入一个必须高可用的
    中间件不划算；而数据库本就是部署的硬依赖，且这两个机制写入极少（只在"跨过
    阈值/熔断"那一刻写一条）、读取也轻（登录路径本就要查 users 表）。

    刻意只存 `until` 而不存计数器：共享记录只表达"这个 key 在什么时刻之前应被
    拒绝"，不参与计数，因此不需要跨进程的原子递增，也就不存在竞态。复合主键
    `(kind, key)` 天然保证每个 key 至多一行。
    """
    __tablename__ = "cooldowns"

    # kind: login_user / login_ip / llm_circuit …；key: 去前缀后的原值（用户名 / IP）
    kind = Column(String(32), primary_key=True)
    key = Column(String(200), primary_key=True)
    # UTC naive 墙钟（跨进程可比；进程内计数仍用单调钟）
    until = Column(DateTime, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)


_ensure_tables_done = False

# 项目根目录（src/models/database.py -> 上溯三级）
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _alembic_config():
    """构造 alembic 配置，连接串取自 settings.DATABASE_URL（与运行时同一个库）。

    configure_logger=False 是必须的：env.py 里的 fileConfig 默认
    disable_existing_loggers=True，若在应用进程内调用，会把已经配置好的
    应用日志器**全部静默关掉**——埋点日志正是靠它输出的。
    """
    from alembic.config import Config
    cfg = Config(str(_PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_PROJECT_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", settings.DATABASE_URL)
    cfg.attributes["configure_logger"] = False
    return cfg


def migrate_database() -> None:
    """把库结构升到最新版本（幂等），schema 由 migrations/ 下的脚本决定。

    两种情形：
    - **全新库**：没有表，从 baseline 起逐版本 upgrade，schema 完全来自迁移脚本
      （不再用 create_all，否则基线之外的迁移里若有数据回填，全新库会被漏掉）。
    - **alembic 之前建的旧库**（有业务表但没有 alembic_version）：
      先用 _ensure_columns() 把历史上靠它补的列变更补齐，再 stamp 到 **baseline**
      （旧库的结构对应的就是 baseline），随后 upgrade 会把 baseline 之后的迁移全部施加。

    **必须 stamp 到 baseline，不能 stamp head**：stamp head 会把旧库直接标记为最新，
    从而**跳过 baseline 之后的每一个迁移**——库被标成"已迁移"、实际缺列，
    而且此后再跑 upgrade 也修不回来（它认为自己已在 head）。这个坑在只有 baseline
    时看不出来，加了第二个迁移才会暴露。

    注意：stamp baseline 断言"旧库当前结构等于 baseline"。本项目历史上的结构变更
    只有新增列与列重命名两类，_ensure_columns() 恰好覆盖这两种，因此该断言成立。
    今后若出现改类型/回填等变更，必须写成正式迁移，不能再依赖 _ensure_columns()。
    """
    from alembic import command
    from alembic.script import ScriptDirectory
    from sqlalchemy import inspect as sa_inspect

    tables = set(sa_inspect(engine).get_table_names())
    has_version = "alembic_version" in tables
    app_tables = tables - {"alembic_version"}

    cfg = _alembic_config()
    if app_tables and not has_version:
        _ensure_columns()
        # 从迁移脚本里取基线版本号，而不是硬编码——基线文件本身约定不可修改
        script = ScriptDirectory.from_config(cfg)
        bases = script.get_bases()
        if len(bases) != 1:
            raise RuntimeError(
                f"迁移历史存在 {len(bases)} 个基线，无法自动纳管已有库，"
                "请人工确认应先 stamp 到哪一个版本"
            )
        command.stamp(cfg, bases[0])
        print(f"[db] 已有库首次纳入 alembic 管理，"
              f"已标记到基线 {bases[0]}，随后施加其后的迁移")
    command.upgrade(cfg, "head")


def ensure_tables() -> None:
    """确保表结构就绪（幂等、进程内只跑一次）。

    不经应用启动的入口（评测脚本、CLI 工具）写埋点或读模型配置时会走到这里。
    与 init_db() 的区别：这里是**尽力而为**——失败只告警不外抛，
    因为埋点/配置读取不该因为迁移问题把业务打挂；启动路径的 init_db()
    会照常抛出，让部署阶段就暴露问题。
    """
    global _ensure_tables_done
    if _ensure_tables_done:
        return
    try:
        migrate_database()
    except Exception as e:
        print(f"[db] 表结构迁移失败（埋点与配置读取可能异常）: {e}")
        return
    _ensure_tables_done = True


def purge_old_llm_logs(days: int = 90) -> int:
    """清理过期的 LLM 调用日志，返回删除行数。

    日志表会随调用量线性增长，不清理迟早撑爆磁盘。启动时调一次即可。
    """
    if days <= 0:
        return 0
    from datetime import timedelta
    cutoff = datetime.utcnow() - timedelta(days=days)
    db = SessionLocal()
    try:
        count = db.query(LLMCallLog).filter(LLMCallLog.created_at < cutoff).delete(
            synchronize_session=False)
        db.commit()
        if count:
            print(f"[db] 清理 {days} 天前的 LLM 调用日志 {count} 条")
        return count
    except Exception as e:
        db.rollback()
        print(f"[db] 清理 LLM 调用日志失败: {e}")
        return 0
    finally:
        db.close()


def init_db():
    # schema 由 alembic 迁移驱动（旧库会在 migrate_database 内一次性纳管）
    migrate_database()
    purge_old_llm_logs(days=settings.LLM_LOG_RETENTION_DAYS)

    import bcrypt

    def _hash(password):
        return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

    db = SessionLocal()
    try:
        existing_admin = db.query(User).filter(User.username == "admin").first()
        if not existing_admin:
            hashed_password = _hash("admin123")
            admin = User(
                username="admin",
                email="admin@example.com",
                password_hash=hashed_password,
                role="admin",
                full_name="系统管理员",
                department="人力资源部"
            )
            db.add(admin)
            
            hr_user = User(
                username="hr001",
                email="lihong@example.com",
                password_hash=_hash("hr123456"),
                role="hr",
                full_name="李红",
                department="人事部"
            )
            db.add(hr_user)

            interviewer = User(
                username="tech001",
                email="wanggong@example.com",
                password_hash=_hash("tech123456"),
                role="interviewer",
                full_name="王工",
                department="技术部"
            )
            db.add(interviewer)

            viewer = User(
                username="view001",
                email="zhang@example.com",
                password_hash=_hash("view123456"),
                role="viewer",
                full_name="张经理",
                department="市场部"
            )
            db.add(viewer)
            
            from datetime import datetime, timedelta
            import random
            
            candidates_data = [
                {"name": "张明", "email": "zhangming@example.com", "phone": "13800138001", "position": "高级后端工程师", "source": "BOSS直聘", "status": "interviewing"},
                {"name": "李华", "email": "lihua@example.com", "phone": "13800138002", "position": "产品经理", "source": "猎聘", "status": "pending"},
                {"name": "王芳", "email": "wangfang@example.com", "phone": "13800138003", "position": "UI设计师", "source": "拉勾网", "status": "hired"},
                {"name": "刘伟", "email": "liuwei@example.com", "phone": "13800138004", "position": "前端开发工程师", "source": "智联招聘", "status": "interviewing"},
                {"name": "陈静", "email": "chenjing@example.com", "phone": "13800138005", "position": "数据分析师", "source": "BOSS直聘", "status": "pending"},
                {"name": "赵强", "email": "zhaoqiang@example.com", "phone": "13800138006", "position": "运维工程师", "source": "51job", "status": "rejected"},
                {"name": "孙丽", "email": "sunli@example.com", "phone": "13800138007", "position": "测试工程师", "source": "内推", "status": "interviewing"},
                {"name": "周杰", "email": "zhoujie@example.com", "phone": "13800138008", "position": "架构师", "source": "猎聘", "status": "pending"},
                {"name": "吴敏", "email": "wumin@example.com", "phone": "13800138009", "position": "HRBP", "source": "拉勾网", "status": "hired"},
                {"name": "郑浩", "email": "zhenghao@example.com", "phone": "13800138010", "position": "全栈工程师", "source": "BOSS直聘", "status": "interviewing"},
                {"name": "黄晓", "email": "huangxiao@example.com", "phone": "13800138011", "position": "运营经理", "source": "智联招聘", "status": "pending"},
                {"name": "林峰", "email": "linfeng@example.com", "phone": "13800138012", "position": "安全工程师", "source": "51job", "status": "rejected"},
            ]
            
            for i, cdata in enumerate(candidates_data):
                created_at = datetime.utcnow() - timedelta(days=random.randint(0, 30))
                candidate = Candidate(
                    name=cdata["name"],
                    email=cdata["email"],
                    phone=cdata["phone"],
                    position=cdata["position"],
                    source=cdata["source"],
                    status=cdata["status"],
                    created_at=created_at,
                    updated_at=created_at
                )
                db.add(candidate)
                db.flush()
                
                resume = Resume(
                    candidate_id=candidate.id,
                    file_name=f"{cdata['name']}_简历.pdf",
                    file_path=f"/resumes/{candidate.id}.pdf",
                    skills=[f"技能{j+1}" for j in range(random.randint(5, 10))],
                    experience=f"{random.randint(3, 10)}年{random.choice(['互联网', '金融', '教育', '医疗'])}行业经验",
                    education=f"{random.choice(['本科', '硕士', '博士'])} - {random.choice(['计算机科学', '软件工程', '信息管理', '数学'])}专业"
                )
                db.add(resume)
                
                if cdata["status"] in ["interviewing", "hired", "rejected"]:
                    interview_count = random.randint(1, 3) if cdata["status"] == "interviewing" else (3 if cdata["status"] == "hired" else random.randint(1, 2))
                    for r in range(interview_count):
                        scheduled = created_at + timedelta(days=random.randint(1, 10), hours=random.randint(9, 18))
                        interview = Interview(
                            candidate_id=candidate.id,
                            position=cdata["position"],
                            round=r + 1,
                            status="completed" if cdata["status"] != "interviewing" or r < interview_count - 1 else "scheduled",
                            interviewer_id=random.choice([2, 3]),
                            scheduled_at=scheduled,
                            completed_at=scheduled + timedelta(hours=1) if cdata["status"] != "interviewing" or r < interview_count - 1 else None,
                            score=random.randint(60, 95) if cdata["status"] != "interviewing" or r < interview_count - 1 else None,
                            feedback=f"第{r+1}轮面试反馈：候选人表现{random.choice(['优秀', '良好', '一般'])}" if cdata["status"] != "interviewing" or r < interview_count - 1 else None,
                            notes=f"面试笔记：技术能力{random.choice(['扎实', '较好', '一般'])}，沟通能力{random.choice(['强', '良好', '一般'])}"
                        )
                        db.add(interview)
                
                if cdata["status"] == "hired":
                    for dim in ["技术能力", "沟通能力", "团队协作", "文化契合"]:
                        eval_item = Evaluation(
                            candidate_id=candidate.id,
                            evaluator_id=random.choice([2, 3]),
                            dimension=dim,
                            score=random.randint(80, 95),
                            comment=f"{dim}评估：表现优秀"
                        )
                        db.add(eval_item)
                
                if cdata["status"] in ["hired", "interviewing"]:
                    tp = TalentPool(
                        candidate_id=candidate.id,
                        status="active" if cdata["status"] == "hired" else "pending",
                        tags=[random.choice(["高潜力", "技术大牛", "管理潜力", "经验丰富"]), random.choice(["本科", "硕士", "博士"])],
                        notes=f"人才池备注：{cdata['name']} - {cdata['position']}",
                        last_contact=created_at + timedelta(days=random.randint(5, 20))
                    )
                    db.add(tp)
            
            from src.models.database import Questionnaire
            q1 = Questionnaire(
                name="Python后端工程师笔试题",
                type="technical",
                questions=[
                    {"id": 1, "question": "请简述Python的GIL及其影响", "type": "essay", "score": 20},
                    {"id": 2, "question": "什么是RESTful API？它有哪些特点？", "type": "essay", "score": 20},
                    {"id": 3, "question": "请解释数据库索引的工作原理", "type": "essay", "score": 20},
                    {"id": 4, "question": "什么是微服务架构？与单体架构相比有什么优缺点？", "type": "essay", "score": 20},
                    {"id": 5, "question": "请简述TCP三次握手的过程", "type": "essay", "score": 20}
                ],
                created_by=1
            )
            db.add(q1)
            
            q2 = Questionnaire(
                name="前端开发工程师笔试题",
                type="technical",
                questions=[
                    {"id": 1, "question": "请解释React的虚拟DOM及其工作原理", "type": "essay", "score": 25},
                    {"id": 2, "question": "什么是闭包？请举例说明", "type": "essay", "score": 25},
                    {"id": 3, "question": "请简述CSS盒模型", "type": "essay", "score": 25},
                    {"id": 4, "question": "什么是跨域？如何解决？", "type": "essay", "score": 25}
                ],
                created_by=1
            )
            db.add(q2)
            
            q3 = Questionnaire(
                name="产品经理综合能力测试",
                type="comprehensive",
                questions=[
                    {"id": 1, "question": "请描述一个你主导的成功产品项目", "type": "essay", "score": 30},
                    {"id": 2, "question": "如何进行用户需求分析？", "type": "essay", "score": 35},
                    {"id": 3, "question": "什么是MVP？如何验证？", "type": "essay", "score": 35}
                ],
                created_by=2
            )
            db.add(q3)
            
            db.commit()
    except Exception as e:
        db.rollback()
        print(f"Seed data error: {e}")
    finally:
        db.close()
