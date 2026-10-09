# AI 招聘系统 (AI Recruitment System)

[![CI](https://github.com/addhai/ai-recruitment-system/actions/workflows/ci.yml/badge.svg)](https://github.com/addhai/ai-recruitment-system/actions/workflows/ci.yml)

基于 AI 的智能招聘管理系统。后端使用 **FastAPI + LangGraph** 驱动多节点招聘工作流（简历解析 → 人岗匹配 → 面试安排 → 综合评估），通过 **SSE** 实时推送工作流进度，并提供完整的候选人管理、面试、问卷、评估、人才库与知识库（RAG）能力。前端为 **React + TypeScript + Vite** 单页应用。

## 技术栈

| 层 | 技术 |
| --- | --- |
| 前端 | React 18、TypeScript、Vite、Tailwind CSS、Axios、SSE |
| 后端 | FastAPI、LangGraph、SQLAlchemy、Pydantic、Pydantic-Settings |
| 实时 | Server-Sent Events（工作流进度推送） |
| 向量 / RAG | Chroma（默认）/ Milvus + MinIO 对象存储 |
| 数据库 | SQLite（本地默认）/ PostgreSQL（Docker） |
| 中间件 | Redis、RabbitMQ（Docker 可选项） |
| 部署 | Docker、docker-compose、Nginx 反代 |
| CI | GitHub Actions（前端构建 / 后端测试 / 容器构建） |

## 功能模块

- **岗位 JD** — 录入 JD（粘贴文本或上传文件）→ AI 解析为结构化画像 → 人工核对原文依据 → 启用
- **仪表盘 Dashboard** — 招聘概览与核心指标
- **候选人 Candidates** — 候选人列表、简历解析、详情页
- **AI 工作流评估** — 候选人详情页触发 LangGraph 多节点评估，SSE 实时展示进度与结果
- **待人工复核** — 文化契合落在待复核区间的候选人由 HR 事后批量裁决
- **面试 Interviews** — 面试安排与完成记录
- **智能问卷 Questionnaires** — AI 生成测评问卷与结果管理
- **评估中心 Evaluations** — 维度化评估统计与明细
- **人才库 TalentPool** — 人才入库、联系记录与盘活
- **知识库 KnowledgeBase** — 基于 RAG 的招聘知识检索
- **系统设置 Settings** — 当前用户、主题、通知、皮肤等

## 人岗匹配机制

匹配**强制依赖岗位 JD**：没有 `active` 且解析成功的 JD 就无法启动 AI 评估。
此前匹配用的是候选人 `position` 这个岗位名字符串，等于拿简历自述的职责去匹配简历
自己（自我印证），现在改为对着结构化 JD 逐条核对。

流程：`录入 JD → AI 解析（每项技能带原文依据）→ 人工核对 → 启用 → 绑定候选人 → 简历上传自动触发`

- **技能匹配** 逐条核对 JD 的 `required_skills`，产出 `missing_skills`
- **经验/教育匹配** 以 JD 写明的年限与学历要求为准（应届生按项目深度评估）
- **文化契合** 以 JD 原文的 `culture_values` 为准，不再使用与岗位无关的通用价值观
- **问卷出题** 以 JD 的硬技能与岗位职责为依据

### 评分口径

- 综合分由**单一公式**加权计算（面试 35% / 技能 20% / 经验 15% / 问卷 15% / 教育 10% / 文化 5%），
  未测评维度按剩余权重归一化，不填默认值
- 「未测评」用 `None` 表达，与「测了 0 分」严格区分
- 文化契合三段判定：`≥60` 通过 / `[50,60)` 打**待复核**标记后继续跑完流程 /
  `<50` 淘汰。终局若带待复核标记则锁档「待人工复核」，由 HR 在复核队列裁决，
  **裁决不重跑 AI**，直接改写终局状态
- 阈值可在 `.env` 调整：`CULTURE_PASS_THRESHOLD` / `CULTURE_REVIEW_THRESHOLD`

## 快速开始

### 方式一：本地开发

**后端**（项目根目录）：
```bash
python -m venv venv
.\venv\Scripts\activate        # Windows；Linux/macOS 用 source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env           # 按需填写 LLM_API_KEY 等配置
uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload
```

**前端**（另开一个终端，进入 frontend 目录）：
```bash
cd frontend
npm install
npm run dev
```

- 前端默认地址：<http://localhost:5173>
- 前端 API 统一走相对路径 `/api`，本地由 Vite 代理转发到后端 `http://localhost:8000`；Docker 下由 Nginx 反代，**始终同源**，无需处理跨域。

### 方式二：Docker 一键部署

```bash
docker compose up --build
```

- 访问 <http://localhost:3000>（Nginx 将 `/api`、`/sse` 反代到 `api-service:8000`）
- 编排内含 api-service、PostgreSQL、前端三个服务（`docker-compose.yml`）；Redis / RabbitMQ / Milvus / MinIO 属可选组件，配置项已预留但未默认编排

构建时的 apt / pip / npm 源由 build-arg 控制，**默认是国内镜像源**（内地构建最快）。
在海外网络下构建请覆盖成上游源，否则每次都要跨境下载数百 MB 依赖：

```bash
# 海外/CI 构建
APT_MIRROR=deb.debian.org \
PIP_INDEX=https://pypi.org/simple/ \
PIP_HOST=pypi.org \
NPM_REGISTRY=https://registry.npmjs.org \
docker compose build
```

CI 里的 `docker-build` job 就是按上面这组值覆盖的——它此前把国内源写死在 Dockerfile 里，
单次构建要 30~45 分钟（见「CI」一节）。

### 方式三：运行测试

**后端单元测试**（使用临时 SQLite，绝不污染 `recruitment.db`）：
```bash
pip install -r requirements.txt
pytest -q
```

**前端构建校验**（含 TypeScript 类型检查）：
```bash
cd frontend
npm run build
```

**端到端冒烟**（可选，需先启动后端且 `.env` 配好真实 LLM / embedding 凭据）：

```bash
python tests/smoke_e2e_check.py       # 全模块 API 冒烟：认证/候选人/面试/问卷/评估/人才库/仪表盘/知识库/SSE/鉴权边界
python tests/smoke_workflow_chain.py   # LangGraph 全链路：驱动问卷与三轮面试挂起点走到底，验证 AI 决策与 SSE 进度推送
python tests/smoke_real_resume.py 简历.pdf   # 真实简历 PDF：验证解析、PII 脱敏、安全护栏与 AI 评估
python tests/smoke_jd_flow.py 简历.pdf      # JD 驱动链路：录入 JD → AI 解析 → 启用 → 绑定 → 上传 → 基于 JD 的匹配
```

三者都不属于 pytest 套件（文件名无 `test_` 前缀，不会被收集），需要真实后端在 `127.0.0.1:8000` 运行。

## 环境变量

完整清单见 [`.env.example`](.env.example)。关键变量：

| 变量 | 说明 |
| --- | --- |
| `LLM_API_KEY` / `LLM_API_BASE` / `LLM_MODEL` | LLM 配置（默认 DeepSeek） |
| `EMBEDDING_MODEL` / `EMBEDDING_DIMENSIONS` | 向量化模型（**需与 embedding 提供方维度一致**） |
| `DATABASE_URL` | 数据库连接；本地默认 SQLite，Docker 下为 PostgreSQL |
| `SECRET_KEY` / `JWT_SECRET_KEY` | 安全密钥（**生产务必修改**） |
| `CORS_ORIGINS` | 前端白名单（已加固，非通配符） |

## 数据库迁移（alembic）

表结构由 `migrations/` 下的迁移脚本定义，**不再用 `create_all`**。
应用启动时自动执行到最新版本（见 `src/models/database.py` 的 `migrate_database`），
所以首启与升级都只需重启服务，不需要手工跑迁移命令。

手工操作（排查或离线环境）：

```bash
alembic upgrade head        # 升级到最新
alembic current             # 看当前版本
alembic history             # 看版本历史
alembic downgrade -1        # 回滚一个版本（create_all 时代做不到）
alembic check               # 校验"库结构"与"模型定义"是否有差异
```

新增一处结构变更：

```bash
alembic revision --autogenerate -m "add xxx column"   # 生成脚本后务必人工过一遍
alembic upgrade head
```

`--autogenerate` 出来的脚本**必须人工检查**：它识别不出数据回填、重命名会被当成
「删列 + 加列」（会丢数据），这类变更要手写成 `op.rename_table` / `op.execute`。

**旧库纳管**：alembic 之前建的库（有业务表但没有 `alembic_version`）会在首次启动时
自动执行一次历史列变更（`_ensure_columns`）后标记为最新版本，数据不动。
注意 `alembic.ini` 必须保持**纯 ASCII**——它由 configparser 以系统 locale 编码读取，
在 zh-CN Windows（GBK）下写入中文会直接让 alembic 崩掉。

## 工作流挂起状态（checkpointer）

LangGraph 的挂起状态（等待问卷/面试的 interrupt）存在哪，由 `DATABASE_URL` 决定：

| `DATABASE_URL` | checkpointer | 多副本 |
| --- | --- | --- |
| `postgresql://...` | `AsyncPostgresSaver`（与应用同库） | **可横向扩副本**，挂起状态共享 |
| `sqlite:///...` | `AsyncSqliteSaver`（`data/workflow_checkpoints.db`） | 单进程 |

这里**不做**「Postgres 连不上就悄悄退回本地文件」的降级：多副本下每个容器各写各的
本地文件，A 副本启动的流程在 B 副本 resume 会找不到状态，表现为「进行中的流程莫名
卡住」且日志没有明显错误。postgres 模式连不上就直接报错，让问题在部署阶段暴露。

一个平台限制：**psycopg3 的异步连接不支持 Windows 的 ProactorEventLoop**，而 uvicorn
在 Windows 上默认就用它。所以 Postgres checkpointer 需要在 Linux/容器内运行
（docker-compose 即为此环境）；Windows 本地开发把 `DATABASE_URL` 设为 sqlite 即可。
命中该组合时启动会立刻报出明确原因，而不是等 30 秒连接超时。

## 项目结构

```
.
├── src/                  # 后端（FastAPI + LangGraph）
│   ├── api/              # 路由：auth/candidates/interviews/questionnaires/evaluations/talent_pool/dashboard/sse/knowledge_base
│   ├── workflow/         # 招聘工作流图（LangGraph）
│   ├── rag/              # 知识库与向量检索
│   ├── models/           # SQLAlchemy 模型与 Pydantic Schema
│   ├── evaluation/       # 评估追踪器
│   └── main.py           # 应用入口
├── frontend/            # 前端（React + TS + Vite）
│   ├── src/pages/        # 页面组件
│   ├── src/services/     # API 封装（统一走 /api）
│   ├── Dockerfile        # 多阶段构建 → Nginx
│   └── nginx.conf        # SPA fallback + /api、/sse 反代
├── docker/              # 后端 Dockerfile 与初始化脚本
├── tests/               # 后端 pytest 套件（conftest 注入临时 SQLite）
├── .github/workflows/   # CI：frontend build / backend pytest / docker build
└── docker-compose.yml   # 全栈编排
```

## CI/CD

GitHub Actions（`.github/workflows/ci.yml`）在每次 `push` / `pull_request` 触发，三阶段门禁：

1. **frontend** — `npm ci` + `npm test`（vitest）+ `npm run build`（tsc 类型检查 + Vite 构建）
2. **backend** — 安装依赖 + `pytest -q --timeout=120`
3. **docker-build** — `docker compose build` 真实验证 api-service 与 frontend 镜像可构建

> docker-build 这一步**必须覆盖镜像源为上游源**（见「方式二」）：Dockerfile 里默认写的是
> 国内镜像源，而 Runner 在海外。之前没覆盖时这一步要跑 30~45 分钟——比另两个 job 加起来
> 还长一个数量级，其中绝大部分时间花在跨境下载依赖上，并不是在验证什么。

### 前端单测

```bash
cd frontend
npm test          # vitest run
npm run test:watch
```

覆盖 `src/lib/` 下的纯逻辑：`format.ts`（币种符号/金额格式）、`sse.ts`（通知解析）、
`authErrors.ts`（登录失败分流）。这三块都是"错了只会静默给出误导性界面"的地方——
币种猜错等于谎报金额、SSE 握手被当业务通知会弹出原始 JSON、登录报错误判会把排查
方向带到账号上。它们此前内联在组件里，只能靠人工看界面发现。

组件现在从 `src/lib/` 引用这些逻辑，测试覆盖的就是真实运行的代码而不是副本。
组件本身的渲染测试暂未引入（那需要 jsdom + testing-library），有需求时再加。

## 权限模型

角色共四个：`admin` / `hr` / `interviewer` / `viewer`。后端**每个业务端点都有角色校验**
（`require_roles` 守卫，位于 `src/api/auth.py`）；前端 Sidebar 的菜单过滤只是体验优化，
不承担安全职责——直接调 API 同样会被拦。

| 资源 | 读 | 写 |
| --- | --- | --- |
| 岗位 / 岗位 JD | admin、hr | admin、hr |
| 人工复核 | admin、hr | admin、hr（裁决不重跑 AI） |
| 候选人 | admin、hr、interviewer | admin、hr |
| 面试 | admin、hr、interviewer | 排期/删除 admin、hr；录入结果 admin、hr、interviewer |
| 评估 | admin、hr、interviewer | admin、hr |
| 人才池 / 问卷 | admin、hr | admin、hr |
| 知识库 / 仪表盘 | 全部角色 | 全部角色 |
| SSE 通知 | 全部登录用户（按 user id 分流） | — |

自注册**已关闭**：`POST /auth/register` 需要管理员权限（`require_admin`），
管理员可以指派任意合法角色（含 `admin`），非法角色直接 400 而不是悄悄降级。

> 历史背景：该端点此前完全无鉴权，任何人都能自助注册成 `hr`——而 `hr` 能读候选人
> 简历（PII）、做招聘裁决、看成本数据。原先只做了"禁止自注册 admin"的角色白名单，
> 堵了提权却没堵住"要不要开放"。它之所以长期公开，是**测试便利泄漏成了攻击面**：
> 权限测试用一个公开注册的辅助函数给每种角色批量造账号，于是"公开注册是预期行为"
> 被断言进了用例。现在测试改为直接写库造账号（`tests/helpers.py`）。

前端从未有过注册入口，建号是管理员职责，设置页的「用户管理」面板即为此而设。

### 用户管理

三个管理员专属端点，前端对应设置页「用户管理」面板：

| 端点 | 作用 |
| --- | --- |
| `GET /auth/users` | 账号列表（含已停用），分页 |
| `PUT /auth/users/{user_id}` | 改邮箱 / 姓名 / 部门 / 角色 / 启用状态 |
| `POST /auth/users/{user_id}/password` | 为用户重置密码 |

**停用代替删除**。账号不做硬删除：`evaluations` 等表的外键指向 `users` 但未声明
`ondelete`，硬删在 SQLite 上因为默认不强制外键而"看起来能用"，到 PostgreSQL 会直接
违反外键约束；就算删得掉，历史评估也会指向一个不存在的人。所以新增 `users.is_active`
（迁移 `830983e1bfd8`）走停用，且列表接口**照旧返回已停用账号**——否则管理员看不到
自己刚停用的人，会以为操作没生效而重复建号。

边界都显式挡住并返回 400，而不是静默忽略：

| 被拒绝的操作 | 原因 |
| --- | --- |
| 停用当前登录账号 | 会把自己锁在门外 |
| 停用 / 降级最后一个可用管理员 | 系统将没人能管模型配置与用户，且无法自救 |
| 指定非法角色 | 与自注册同一套 `VALID_ROLES` 校验，失败即 400 |
| 改成已被占用的邮箱 | 唯一约束提前报错，而不是等数据库抛异常 |

用户名不可改：它是 JWT 的 `sub`、也是各表的关联依据，改名会让已签发令牌与历史记录
对不上；要换用户名请新建账号并停用旧的。

停用与重置密码都会把 `token_version +1`（见下节），所以**旧令牌当场失效**，不必等它
自然过期。另外，被停用的人再登录时，`/auth/login` 返回的文案与"密码错误"完全一致
（都是 `Incorrect username or password`），不泄漏"账号存在但被停用"这一信息；带旧令牌
访问业务接口则统一走 `get_current_user` 的 `Could not validate credentials`，同样不区分
原因。

> 这一块此前完全缺失：后端只有公开注册，界面上也没有建号入口，管理员要加人只能去
> 调接口。角色人数还是前端写死的假数据（1/2/5/3），现在改成读取真实列表。

### 登录失败限流

`/auth/login` 是唯一无需凭据即可调用的写路径，而种子口令是弱口令——没有失败计数时，
端口可达就等于可被持续爆破。现在按**用户名**与**来源 IP** 两个维度计数：

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `LOGIN_MAX_FAILED_PER_USER` | 5 | 单账号失败上限，触发后返回 429 + `Retry-After` |
| `LOGIN_MAX_FAILED_PER_IP` | 20 | 放宽，避免办公室共用出口 IP 被个别人锁住 |
| `LOGIN_FAILURE_WINDOW_SECONDS` | 900 | 失败计数窗口 |
| `LOGIN_LOCKOUT_SECONDS` | 900 | 触发后锁定时间 |

只按用户名记是不够的：**撞库**攻击对每个账号只试 4 次就永远碰不到阈值，
所以必须同时按 IP 记（见 `src/services/login_guard.py`）。

两个刻意的设计：

- **失败一律计数，不管用户名是否存在**。若"存在才计数"，攻击者撞几次就能靠
  "是否被锁"判断账号是否存在，限流本身变成用户枚举探针。
- **成功登录只清用户名的计数，不清 IP**。否则攻击者中间夹一次自己账号的成功登录
  就能把 IP 维度的记录洗白。

已知局限：计数状态在**进程内存**里，多副本部署时各副本独立计数（实际阈值 ≈ 副本数 ×
配置值），进程重启即清零。要严格生效需接共享存储（Redis 等），本项目当前未引入；
单副本是默认部署形态，所以选择"明显提高爆破成本、不给运维加依赖"的折中，
而不是假装它严密。

### 会话有效期与令牌撤销

| 配置项 | 说明 |
| --- | --- |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | 令牌有效期，**现在真的生效了**（此前 auth.py 硬编码 30 分钟，配了也没用） |
| `ENABLE_API_DOCS` | 是否开放 `/docs` `/redoc` `/openapi.json`，公网建议 false |

`POST /auth/users/me/password` 修改自己的密码，并**立即失效此前签发的全部令牌**。

为什么需要它：JWT 是无状态的、签出去就收不回来。没有改密接口时，密码一旦泄漏或账号
被盗，唯一止血手段是更换 `SECRET_KEY`——那会把所有人踢下线。

实现用**令牌版本号**（`users.token_version` + 令牌里的 `tv`）而不是时间戳比对：

- 时间戳方案踩过两个坑——`naive datetime.timestamp()` 会被按本地时区解释（UTC+8 偏
  8 小时），导致改密后新令牌被立即判为失效；改成把撤销点截断到秒虽能救回新令牌，
  却让**同一秒内签发的旧令牌**逃过撤销。
- 版本号不含时钟，判定精确，也不受时区/时钟回拨影响。改密 = 版本 +1，
  旧令牌（带旧版本）即刻失效，**撤销是 per-user 的，不会影响其他人**。
- 缺 `tv` 的老令牌按版本 0 处理：本次上线不会把所有人踢下线，而用户一旦改密它们立刻失效。

改密接口会返回一个新令牌让当前会话继续可用（其他会话失效）——否则用户改完密码就得
立刻重新登录，体验上会诱导人不去改密码。

### 列表分页

所有列表接口统一支持 `skip` / `limit`，`limit` 默认 100、服务端强制上限 1000
（见 `src/api/pagination.py`）。

> 行为变更：`/positions` `/questionnaires` `/evaluations` `/talent-pool` `/reviews`
> 此前是 `query.all()` 全量返回，现在与 `/candidates` `/interviews` 口径一致。
> 本项目当前数据量下无可见差异；若某环境超过 100 条，界面只会显示前 100 条，需要翻页。
> 上界是必须的——只给默认值不加界，调用方仍可传 `limit=10_000_000`，等于没分页。

## AI 可观测与成本管控

每次 LLM 调用都会记录 **调用环节、token 用量、耗时、成本、成功/降级状态**，
写入 `llm_call_logs` 表并输出 JSON 结构化日志。前端「AI 成本」页可按环节查看花费构成。

全部 LLM 调用收敛到单一接入层 `src/services/llm_invoke.py`——此前有 4 处各自
构造客户端（主流程 / JD 解析 / RAG 问答 / 一处死代码），任何埋点或换模型都要改 4 遍。

**降级与预算必须区分**：`degraded=true` 表示调用失败走了规则兜底分（此时评分不可信），
`budget_blocked` 表示预算耗尽根本没发起调用。两者在成本页与日志里分列显示。

### 调用链归集（thread_id）

一次简历上传会触发 4~8 次 LLM 调用。`llm_call_logs.thread_id` 记录这些调用属于哪一次
工作流运行，于是"这次运行到底哪一步花了钱、哪一步降级了"可以直接下钻，
而不是靠时间戳猜。

实现见 `src/services/trace.py`：一个 ContextVar + `trace_thread()`，
由 `runner._drive()` 在驱动图时设置，埋点写库时读取。用 ContextVar 而不是给函数
加参数，是因为埋点入口的调用点散布在 10 个图节点里，逐个加参数既侵入又容易漏；
且 ContextVar 按任务隔离，多候选人并发不会串号。

非工作流链路（知识库问答、JD 解析）本来就没有 thread_id，该列**留空**，不塞假值。

成本页明细里点「运行」即可只看该次运行的全部调用（服务端过滤，不是只筛本地几十条）：

```
GET /llm-stats/calls?thread_id=wf-xxxxxxxx
```

### 成本预算

模型与单价**优先在界面配置**（系统设置 → 模型配置，仅管理员），下列 `.env` 项作为兜底默认值：

```bash
LLM_MODEL=deepseek-flash            # 兜底模型名；界面配置优先
LLM_INPUT_PRICE_PER_MILLION=0       # 单位：LLM_PRICE_CURRENCY/百万 token，按供应商报价填
LLM_OUTPUT_PRICE_PER_MILLION=0
LLM_PRICE_CURRENCY=CNY              # 计价币种，预算上限必须与它同币种
LLM_PEAK_MULTIPLIER=1.0             # 高峰价倍数；DeepSeek 官方为 2
LLM_BUDGET_ENABLED=true
LLM_BUDGET_PERIOD=daily             # daily | monthly
LLM_BUDGET_AMOUNT=5.0               # 预算上限，币种同 LLM_PRICE_CURRENCY
LLM_BUDGET_ACTION=halt              # halt=停止调用并转人工 / warn=仅告警
LLM_LOG_RETENTION_DAYS=90
```

**币种口径必须一致**：成本由「token × 单价」折算，单价是什么币种，预算上限和界面展示
就必须是同一币种。DeepSeek 报价为人民币，故默认 `LLM_PRICE_CURRENCY=CNY`。
旧字段名 `LLM_BUDGET_USD` 仍被识别（显式设置时优先），仅为兼容既有部署。

**跨币种金额绝不相加**：汇总与预算窗口都只累加与当前币种一致的行；被排除的金额
（其它币种、以及多币种改造前 `currency` 为 NULL 的历史行）在成本页单独报出为
`excluded_cost`，不静默丢弃。静默少算比不保护更危险——用户以为有上限，实际会超支。

**模型版本会被记录**：`llm_call_logs.model_served` 存供应商实际返回的模型版本
（如请求 `deepseek-flash` 时 DeepSeek 可能回报具体版本号）。只记请求名的话，
同一份简历换了底层版本后分数对不上，无从追溯。

**并发闸门**：`LLM_MAX_CONCURRENCY` 限制同时在途的调用数（默认 8，0 = 不限）。
批量筛简历时 N 个候选人 × 每人 4~8 次调用会瞬间放大请求量，撞上供应商并发上限后
虽能靠重试兜住，但延迟与失败率都会被推高。限的是我们自己的发散度。

### 熔断

供应商整体故障（宕机、网络不通、持续 5xx）时，每个调用仍要耗完自己的重试预算才降级；
一次简历评估 4~8 次调用串起来就是几分钟纯等待。`src/services/circuit_breaker.py` 让它快速失败：

```
closed --连续失败达阈值--> open
open --冷却到期--> half_open（只放一个探针）
half_open --探针成功--> closed ；--探针失败--> open（重新计时）
```

| 配置项 | 默认 |
| --- | --- |
| `LLM_CIRCUIT_BREAKER_ENABLED` | true |
| `LLM_CIRCUIT_FAILURE_THRESHOLD` | 5（连续失败次数） |
| `LLM_CIRCUIT_OPEN_SECONDS` | 60（冷却时长） |

**「什么才算失败」比熔断本身更容易做错**，所以单独说明：

- **只计请求层面的失败**（连不上、超时、5xx、429）。
- **JSON 解析失败不计**：模型答了，只是答得不是合法 JSON。这是输出质量问题，
  调温度/改 prompt 才是解法。算作供应商故障会让"模型偶尔不听话"直接熔断，
  反而把可用性做低。
- **预算耗尽不计**：根本没发请求。
- 熔断时的埋点状态是 `circuit_open`，与 `failed` 分开记录——前者是"我们主动不发"，
  后者是"供应商返回了错误"，混在一起读会把成本页的失败率看错。
- half_open 只放一个探针，避免冷却刚到期就把积压请求一起打过去——那正是刚恢复的
  供应商最扛不住的时刻。

当前熔断状态可在 `/llm-stats/summary` 的 `circuit` 字段查看。

超限行为（`halt`）：停止后续 LLM 调用，候选人转入「待人工评估」（`pending_manual`），
**不产出任何招聘决策**；中断前已算出的评分保留不回滚——预算耗尽不是数据错误，
已花钱得到的结论应当留下。`budget_halted` 不算活跃运行，提高预算后可直接重跑。

**单价未配置时成本恒为 0，预算保护会自动跳过**（宁可不做保护，也不能因为忘配
价格就把业务卡死）。成本页与模型配置页都会显式提示这一点。

## LLM 质量回归评测

`tests/eval/` 下有一套防退化评测（22 条用例，覆盖 2 份真实简历 × 6 份真实 JD
及边界/对抗场景）。**只断言硬性不变量**（字段存在性、结构类型、是否引用 JD 原文价值观、
未设限维度是否被误写成 0 分），分数/耗时/token 只记录不判定——实测同一份简历
连跑 4 次文化契合给出 72/72/58/58，锁死数值只会频繁误报。

```bash
python tests/eval/run_eval.py                    # 全部用例
python tests/eval/run_eval.py --repeat 3         # 观察数值波动
python tests/eval/run_eval.py --cases 01 07      # 指定用例
```

需要真实 LLM 凭据，会产生 API 成本，故不进日常 CI；通过 GitHub Actions 的
`workflow_dispatch` 手动触发（勾选 `run_evals`）。详见 `tests/eval/README.md`。

## 注意事项

- 本地默认 SQLite 即可运行；Docker 全栈含 PostgreSQL，可按需裁剪。
- 生产部署务必修改 `SECRET_KEY` / `JWT_SECRET_KEY`，并配置真实 LLM 与 embedding 凭据。
- `EMBEDDING_MODEL` 必须与 embedding 提供方的模型及维度一致（如 bge-m3 对应 1024 维），否则向量检索会报错。
- **端口占用**：本机 8000 端口若被 Docker 容器或其他服务占用，Windows 下 `localhost` 会优先解析到 IPv6，
  导致前端代理把请求打到别的服务（表现为接口 404/405 而后端日志无记录）。
  Vite 代理已固定使用 `127.0.0.1:8000`，启动前请先确认该端口未被占用。
- API 路由不带 `/api` 前缀；`/api` 只是前端约定，由 Vite（开发）或 Nginx（生产）转发时剥离。
