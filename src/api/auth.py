from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
import bcrypt
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from src.models.database import get_db, User
from src.models.schemas import UserCreate, UserResponse, LoginRequest, TokenResponse
from src.config import settings

router = APIRouter(prefix="/auth", tags=["auth"])

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
def register(user: UserCreate, db: Session = Depends(get_db)):
    db_user = db.query(User).filter(User.username == user.username).first()
    if db_user:
        raise HTTPException(status_code=400, detail="Username already registered")
    
    db_email = db.query(User).filter(User.email == user.email).first()
    if db_email:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    # 自注册禁止分配 admin 等特权角色，防止权限提升
    ALLOWED_SELF_REGISTER_ROLES = {"hr", "interviewer", "viewer"}
    role = user.role if user.role in ALLOWED_SELF_REGISTER_ROLES else "viewer"

    hashed_password = get_password_hash(user.password)
    new_user = User(
        username=user.username,
        email=user.email,
        password_hash=hashed_password,
        full_name=user.full_name,
        department=user.department,
        role=role
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return new_user


@router.post("/login", response_model=TokenResponse)
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == form_data.username).first()
    if not user or not verify_password(form_data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.username}, expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer"}


@router.get("/users/me", response_model=UserResponse)
def read_users_me(current_user: User = Depends(get_current_active_user)):
    return current_user
