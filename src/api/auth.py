from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
import bcrypt
import calendar
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from typing import List
from src.models.database import get_db, User
from src.models.schemas import (UserCreate, UserResponse, LoginRequest,
                                PasswordChange, TokenResponse,
                                UserUpdate, AdminPasswordReset)
from src.config import settings
from src.services import login_guard
from src.api.pagination import SkipParam, LimitParam

router = APIRouter(prefix="/auth", tags=["auth"])

# 合法角色集合，与 require_all_authenticated 的口径一致
VALID_ROLES = {"admin", "hr", "interviewer", "viewer"}

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")

SECRET_KEY = settings.SECRET_KEY
ALGORITHM = "HS256"


def verify_password(plain_password, hashed_password):
    return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))


def get_password_hash(password):
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')


def create_access_token(data: dict, expires_delta: timedelta | None = None,
                        token_version: int = 0):
    """签发 JWT。

    带 `tv`（token_version）：令牌撤销靠"令牌里的版本 vs 库里的版本"比对。
    没有这个字段就无法判断令牌是不是在改密之前签发的。

    有效期默认取自 settings.ACCESS_TOKEN_EXPIRE_MINUTES——此前这里硬编码 30 分钟，
    导致 .env 里的同名配置**静默失效**（配了 1440 实际仍只活 30 分钟）。
    """
    to_encode = data.copy()
    now = datetime.utcnow()
    expire = now + (expires_delta or timedelta(
        minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire, "iat": calendar.timegm(now.utctimetuple()),
                      "tv": token_version})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def _token_revoked(payload: dict, user: User) -> bool:
    """令牌是否已被撤销（令牌里的版本与库中不一致）。

    缺 `tv` 的老令牌按版本 0 处理：这样本次上线不会把所有人踢下线，
    而用户一旦改密（版本 +1）它们立刻失效。
    """
    return payload.get("tv", 0) != (user.token_version or 0)


async def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    user = db.query(User).filter(User.username == username).first()
    if user is None:
        raise credentials_exception
    if _token_revoked(payload, user):
        raise credentials_exception
    # 停用检查放在这个唯一入口：所有鉴权路径（含 require_roles 守卫）
    # 都经过这里，不必在每个路由重复判断。
    if not user.is_active:
        raise credentials_exception
    return user


async def get_current_active_user(current_user: User = Depends(get_current_user)):
    return current_user


# ---------------------------------------------------------------- 权限守卫
# 说明：本项目此前所有业务路由都只挂了 get_current_user，角色仅存在于
# User.role 与前端菜单的 roles 过滤里——那是 UI 隐藏，不是安全边界。
# 任何登录用户（含 viewer）直接调 API 都能改删数据。这里提供统一守卫，
# 让权限口径集中在一处，新增路由不要再各自发明判断逻辑。

def require_roles(*allowed: str):
    """生成一个校验角色的 FastAPI 依赖。

    用法：current_user=Depends(require_roles("hr", "admin"))
    """
    allowed_set = set(allowed)

    async def _dep(current_user: User = Depends(get_current_user)):
        if current_user.role not in allowed_set:
            raise HTTPException(
                status_code=403,
                detail=f"权限不足：需要 {'/'.join(sorted(allowed_set))} 角色之一",
            )
        return current_user
    return _dep


# 常用权限组合，口径与前端 Sidebar 的菜单 roles 保持一致
require_admin = require_roles("admin")
require_hr_admin = require_roles("hr", "admin")
require_hr_admin_interviewer = require_roles("hr", "admin", "interviewer")
require_all_authenticated = require_roles("admin", "hr", "interviewer", "viewer")


@router.post("/register", response_model=UserResponse)
def register(user: UserCreate, db: Session = Depends(get_db),
             current_user: User = Depends(require_admin)):
    """创建用户。**仅管理员**。

    此前这个端点完全无鉴权：任何人都能自助注册。原先只做了"禁止自注册 admin"
    的角色白名单（见下方历史注记），但白名单里保留了 `hr`——而 `hr` 能读候选人
    简历（PII）、做招聘裁决、看成本数据。所以未鉴权的访问者拿到的是 PII 访问权，
    不只是"一个只读账号"。

    这个端点之所以一直是公开的，大概率是**测试便利泄漏成了攻击面**：
    tests/test_permissions_all_routes.py 的 _login() 辅助函数靠它给每种角色
    批量造账号，于是"公开可注册"被当成预期行为断言进了用例。
    前端从未有过注册入口（frontend/src 里搜不到 register），产品意图一直是
    管理员建号（见设置页「用户管理」的说明）。现在口径统一为需管理员。
    """
    db_user = db.query(User).filter(User.username == user.username).first()
    if db_user:
        raise HTTPException(status_code=400, detail="Username already registered")

    db_email = db.query(User).filter(User.email == user.email).first()
    if db_email:
        raise HTTPException(status_code=400, detail="Email already registered")

    # 管理员可以指派任意合法角色（含 admin）；非法值直接拒绝而不是悄悄降级，
    # 否则"我明明填了 admin 却没生效"会变成一个难查的静默行为
    if user.role not in VALID_ROLES:
        raise HTTPException(
            status_code=400,
            detail=f"非法角色 {user.role!r}，可选: {', '.join(sorted(VALID_ROLES))}",
        )

    hashed_password = get_password_hash(user.password)
    new_user = User(
        username=user.username,
        email=user.email,
        password_hash=hashed_password,
        full_name=user.full_name,
        department=user.department,
        role=user.role,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return new_user


@router.post("/login", response_model=TokenResponse)
def login(request: Request,
          form_data: OAuth2PasswordRequestForm = Depends(),
          db: Session = Depends(get_db)):
    ip = request.client.host if request.client else None

    # 限流前置检查：命中窗口直接 429，不再去比对密码（也顺带省掉一次 bcrypt 开销）
    try:
        login_guard.check_allowed(form_data.username, ip)
    except login_guard.LoginThrottled as e:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="尝试次数过多，请稍后再试",
            headers={"Retry-After": str(e.retry_after)},
        )

    user = db.query(User).filter(User.username == form_data.username).first()
    if not user or not verify_password(form_data.password, user.password_hash):
        # 失败一律计数，**不管用户名是否存在**——否则"存在才计数"就成了用户枚举探针
        login_guard.record_failure(form_data.username, ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        # 与"密码错"用同样的 401 文案：否则被停用的人能通过错误信息差异
        # 判断出"账号存在但被封"，也是一种账号枚举
        login_guard.record_failure(form_data.username, ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    login_guard.record_success(form_data.username, ip)
    # 有效期由 create_access_token 从 settings 取（不再硬编码）
    access_token = create_access_token(
        data={"sub": user.username}, token_version=user.token_version or 0)
    return {"access_token": access_token, "token_type": "bearer"}


@router.post("/users/me/password", response_model=TokenResponse)
def change_own_password(body: PasswordChange,
                        db: Session = Depends(get_db),
                        current_user: User = Depends(get_current_active_user)):
    """修改自己的密码，并**立即失效此前签发的全部令牌**。

    为什么需要这个端点：JWT 是无状态的、签出去就收不回来。没有改密接口时，
    一旦密码泄漏或账号被盗，唯一"止血"手段是更换 SECRET_KEY——那会把所有人踢下线。

    现在改密会把 token_version 加一，旧令牌（带旧版本号）即刻失效，
    含攻击者手上那一份；撤销是 per-user 的，不会影响其他人。

    返回一个新令牌（带新版本号）让当前会话继续可用——否则用户改完密码就得立刻
    重新登录，体验上会诱导人不去改密码。
    """
    if not verify_password(body.old_password, current_user.password_hash):
        raise HTTPException(status_code=400, detail="当前密码不正确")
    if body.new_password == body.old_password:
        raise HTTPException(status_code=400, detail="新密码不能与当前密码相同")

    current_user.password_hash = get_password_hash(body.new_password)
    current_user.token_version = (current_user.token_version or 0) + 1
    db.add(current_user)
    db.commit()
    db.refresh(current_user)

    token = create_access_token(
        data={"sub": current_user.username},
        token_version=current_user.token_version)
    return {"access_token": token, "token_type": "bearer"}


@router.get("/users/me", response_model=UserResponse)
def read_users_me(current_user: User = Depends(get_current_active_user)):
    return current_user


# ---------------------------------------------------------------- 用户管理
# 全部 require_admin：建号/改角色/停用都是管理动作。
# 注意这里**不提供硬删除**，只提供停用——原因见 User.is_active 的注释。

def _other_active_admins(db: Session, exclude_id: int) -> int:
    return (db.query(User)
            .filter(User.role == "admin", User.is_active.is_(True),
                    User.id != exclude_id)
            .count())


def _load_user(db: Session, user_id: int) -> User:
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    return user


@router.get("/users", response_model=List[UserResponse])
def list_users(skip: SkipParam = 0, limit: LimitParam = 100,
               db: Session = Depends(get_db),
               current_user: User = Depends(require_admin)):
    """用户列表。**包含已停用账号**——否则管理员看不到自己刚停用的人，
    会以为操作没生效而重复建号。状态由 is_active 字段区分。"""
    return (db.query(User).order_by(User.id)
            .offset(skip).limit(limit).all())


@router.put("/users/{user_id}", response_model=UserResponse)
def update_user(user_id: int, body: UserUpdate,
                db: Session = Depends(get_db),
                current_user: User = Depends(require_admin)):
    """修改用户资料 / 角色 / 启用状态。"""
    user = _load_user(db, user_id)

    if body.role is not None:
        if body.role not in VALID_ROLES:
            raise HTTPException(
                status_code=400,
                detail=f"非法角色 {body.role!r}，可选: {', '.join(sorted(VALID_ROLES))}")
        if user.role == "admin" and body.role != "admin" and \
                _other_active_admins(db, user.id) == 0:
            # 否则系统会变成没有人能管模型配置与用户，且无法自救
            raise HTTPException(status_code=400, detail="不能移除最后一个可用管理员的管理员角色")
        user.role = body.role

    if body.is_active is not None and body.is_active != user.is_active:
        if not body.is_active:
            if user.id == current_user.id:
                raise HTTPException(status_code=400, detail="不能停用当前登录的账号")
            if user.role == "admin" and _other_active_admins(db, user.id) == 0:
                raise HTTPException(status_code=400, detail="不能停用最后一个可用管理员")
            # 停用要立即生效：令牌版本 +1，已签发的令牌当场作废。
            # 否则被停用的人还能拿旧令牌继续用到过期为止。
            user.token_version = (user.token_version or 0) + 1
        user.is_active = body.is_active

    if body.email is not None:
        clash = (db.query(User)
                 .filter(User.email == body.email, User.id != user.id).first())
        if clash:
            raise HTTPException(status_code=400, detail="Email already registered")
        user.email = body.email
    if body.full_name is not None:
        user.full_name = body.full_name
    if body.department is not None:
        user.department = body.department

    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.post("/users/{user_id}/password")
def reset_user_password(user_id: int, body: AdminPasswordReset,
                        db: Session = Depends(get_db),
                        current_user: User = Depends(require_admin)):
    """管理员为用户重置密码。用户忘记密码时的唯一出路——没有它，
    账号只能弃用重开，历史评估也就跟着断了。

    同样把令牌版本 +1：重置密码意味着此前所有会话失效。
    """
    user = _load_user(db, user_id)
    user.password_hash = get_password_hash(body.new_password)
    user.token_version = (user.token_version or 0) + 1
    db.add(user)
    db.commit()
    return {"message": f"已重置 {user.username} 的密码，该用户此前的登录状态已全部失效"}
