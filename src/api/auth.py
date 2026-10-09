from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
import bcrypt
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from src.models.database import get_db, User
from src.models.schemas import UserCreate, UserResponse, LoginRequest, TokenResponse
from src.config import settings
from src.services import login_guard

router = APIRouter(prefix="/auth", tags=["auth"])

# 合法角色集合，与 require_all_authenticated 的口径一致
VALID_ROLES = {"admin", "hr", "interviewer", "viewer"}

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")

SECRET_KEY = settings.SECRET_KEY
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30


def verify_password(plain_password, hashed_password):
    return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))


def get_password_hash(password):
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')


def create_access_token(data: dict, expires_delta: timedelta | None = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


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

    login_guard.record_success(form_data.username, ip)
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.username}, expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer"}


@router.get("/users/me", response_model=UserResponse)
def read_users_me(current_user: User = Depends(get_current_active_user)):
    return current_user
