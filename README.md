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

1. **frontend** — `npm ci` + `npm run build`（tsc 类型检查 + Vite 构建）
2. **backend** — 安装依赖 + `pytest -q --timeout=120`
3. **docker-build** — `docker compose build` 真实验证 api-service 与 frontend 镜像可构建

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

自注册只允许 `hr` / `interviewer` / `viewer`，申请 `admin` 会被降级为 `viewer`。

## AI 可观测与成本管控

每次 LLM 调用都会记录 **调用环节、token 用量、耗时、成本、成功/降级状态**，
写入 `llm_call_logs` 表并输出 JSON 结构化日志。前端「AI 成本」页可按环节查看花费构成。

全部 LLM 调用收敛到单一接入层 `src/services/llm_invoke.py`——此前有 4 处各自
构造客户端（主流程 / JD 解析 / RAG 问答 / 一处死代码），任何埋点或换模型都要改 4 遍。

**降级与预算必须区分**：`degraded=true` 表示调用失败走了规则兜底分（此时评分不可信），
`budget_blocked` 表示预算耗尽根本没发起调用。两者在成本页与日志里分列显示。

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
