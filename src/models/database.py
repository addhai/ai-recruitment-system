from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, ForeignKey, JSON, Boolean, Float
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime
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

    interviews = relationship("Interview", back_populates="interviewer")
    questionnaires = relationship("Questionnaire", back_populates="created_by_user")
    evaluations = relationship("Evaluation", back_populates="evaluator")


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
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    resume = relationship("Resume", back_populates="candidate", uselist=False)
    interviews = relationship("Interview", back_populates="candidate")
    questionnaire_responses = relationship("QuestionnaireResponse", back_populates="candidate")
    evaluations = relationship("Evaluation", back_populates="candidate")
    talent_pool = relationship("TalentPool", back_populates="candidate", uselist=False)
    workflow_runs = relationship("WorkflowRun", back_populates="candidate")


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


def init_db():
    Base.metadata.create_all(bind=engine)
    
    from passlib.context import CryptContext
    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
    
    db = SessionLocal()
    try:
        existing_admin = db.query(User).filter(User.username == "admin").first()
        if not existing_admin:
            hashed_password = pwd_context.hash("admin123")
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
                username="hr",
                email="hr@example.com",
                password_hash=pwd_context.hash("hr123"),
                role="hr",
                full_name="李经理",
                department="人力资源部"
            )
            db.add(hr_user)
            
            interviewer = User(
                username="interviewer",
                email="interviewer@example.com",
                password_hash=pwd_context.hash("int123"),
                role="interviewer",
                full_name="王面试官",
                department="技术部"
            )
            db.add(interviewer)
            
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
