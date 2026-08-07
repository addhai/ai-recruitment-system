# AI 招聘系统 (AI Recruitment System)

[![CI](https://github.com/your-username/your-repo/actions/workflows/ci.yml/badge.svg)](https://github.com/your-username/your-repo/actions/workflows/ci.yml)
<!-- 将上面的 your-username/your-repo 替换为你的 GitHub 仓库地址，push 后徽章即生效 -->

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

- **仪表盘 Dashboard** — 招聘概览与核心指标
- **候选人 Candidates** — 候选人列表、简历解析、详情页
- **AI 工作流评估** — 候选人详情页触发 LangGraph 多节点评估，SSE 实时展示进度与结果
- **面试 Interviews** — 面试安排与完成记录
- **智能问卷 Questionnaires** — AI 生成测评问卷与结果管理
- **评估中心 Evaluations** — 维度化评估统计与明细
- **人才库 TalentPool** — 人才入库、联系记录与盘活
- **知识库 KnowledgeBase** — 基于 RAG 的招聘知识检索
- **系统设置 Settings** — 当前用户、主题、通知、皮肤等

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
- 全栈包含 api-service、PostgreSQL、Redis、RabbitMQ、前端（按需可扩展 Milvus/MinIO）

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

## 环境变量

完整清单见 [`.env.example`](.env.example)。关键变量：

| 变量 | 说明 |
| --- | --- |
| `LLM_API_KEY` / `LLM_API_BASE` / `LLM_MODEL` | LLM 配置（默认 DeepSeek） |
| `EMBEDDING_MODEL` / `EMBEDDING_DIMENSIONS` | 向量化模型（**需与 embedding 提供方维度一致**） |
| `DATABASE_URL` | 数据库连接；本地默认 SQLite，Docker 下为 PostgreSQL |
| `SECRET_KEY` / `JWT_SECRET_KEY` | 安全密钥（**生产务必修改**） |
| `CORS_ORIGINS` | 前端白名单（已加固，非通配符） |

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
2. **backend** — 安装依赖 + `pytest -q`（14 项测试）
3. **docker-build** — `docker compose build` 真实验证 api-service 与 frontend 镜像可构建

## 注意事项

- 本地默认 SQLite 即可运行；Docker 全栈含 PostgreSQL / Redis / RabbitMQ 等，可按需裁剪。
- 生产部署务必修改 `SECRET_KEY` / `JWT_SECRET_KEY`，并配置真实 LLM 与 embedding 凭据。
- `EMBEDDING_MODEL` 必须与 embedding 提供方的模型及维度一致（如 bge-m3 对应 1024 维），否则向量检索会报错。
