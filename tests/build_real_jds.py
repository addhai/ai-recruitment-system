"""从真实招聘截图构建岗位 + 岗位 JD。

只保留人岗匹配必需的信息：岗位职责、任职资格/要求、加分项、我们看重什么。
刻意剔除：公司名称、薪资、工作地点、招聘人数、福利、投递方式等——
这些不影响技能/经验/教育/文化的匹配判断，录入只会引入虚构风险。

每份 JD 以岗位 + JD 两层落库，随后调用真实 LLM 解析出带原文依据的结构化画像。
"""
import asyncio
import sys
import time

sys.path.insert(0, ".")

import httpx

import smoke_common

BASE = "http://127.0.0.1:8000"

# ---------------------------------------------------------------- 岗位 JD 内容

JDS = [
    {
        "position": {"title": "AI Agent 工程师（校招）", "department": "技术部", "headcount": None},
        "raw_text": """岗位名称：AI Agent 工程师

岗位职责：
1. 参与 AI Agent 系统的设计与研发，包括任务规划、工具调用、记忆管理、知识检索和多智能体协作等能力
2. 基于大语言模型构建 Agent 工作流，完成 Prompt 设计、上下文管理、模型调用和效果优化
3. 参与 RAG、向量检索、知识库及结构化数据查询能力建设
4. 对接内部系统、第三方 API 及各类业务工具，提升 Agent 完成复杂任务的能力
5. 建设 Agent 评测体系，分析准确率、任务完成率、响应时延、Token 成本及稳定性
6. 跟进大模型和 Agent 领域的新技术，完成技术验证并推动业务落地
7. 与产品、算法及业务团队协作，持续优化 Agent 的用户体验和实际效果

任职资格：
1. 计算机、软件工程、人工智能、数学或相关专业，本科及以上学历
2. 熟练使用 Python，具备良好的数据结构、算法和软件工程基础
3. 了解大语言模型基本原理，熟悉 Prompt、Function Calling、Tool Use、RAG 等常用技术
4. 了解至少一种 Agent 或大模型应用开发框架，如 LangChain、LangGraph、LlamaIndex、AutoGen、Semantic Kernel 等
5. 熟悉 HTTP、REST API、数据库及前后端开发方式
6. 具备较强的问题分析、技术学习和独立实践能力
7. 热爱游戏，对市面游戏有了解，对游戏发行、互联网广告或 AI 技术有浓厚兴趣
8. 对 AI Agent 方向有持续兴趣，能够主动跟进论文、开源项目和行业进展

加分项：
1. 有大模型、Agent、RAG 或 AI 应用相关实习及项目经验
2. 使用过主流大模型 API 或开源模型，具备模型部署、微调或推理优化经验
3. 熟悉向量数据库、Embedding、重排序或知识图谱技术
4. 了解 ReAct、Reflection、Planning、Memory 或 Multi-Agent 等方法
5. 熟悉 Docker、Linux、Git 及云服务部署流程
6. 在 GitHub 有相关开源项目，或在算法竞赛、人工智能竞赛中取得成绩""",
    },
    {
        "position": {"title": "AI 应用开发实习生（Agent 方向）", "department": "技术部", "headcount": None},
        "raw_text": """岗位名称：AI 应用开发实习生（Agent 方向）

岗位职责：
1. 参与公司企业级 AI 应用项目的开发与交付，包括 AI Agent、RAG 知识库、智能客服、企业内部管理系统及自动化工作流等
2. 使用 Python 参与 AI 应用及后端功能开发
3. 调用 DeepSeek、OpenAI、Gemini、Claude、Qwen 等大模型 API
4. 参与 AI Agent、RAG、Function Calling、Workflow 等功能开发与调试
5. 使用 FastAPI 等框架完成简单 API 接口及业务逻辑
6. 参与 PDF、Excel、图片、企业文档等数据处理与结构化提取
7. 根据项目需要与简单的 React / Vue 前端页面开发
8. 参与数据库、接口联调、功能测试、Bug 修复及基础部署
9. 使用 Codex、Claude Code、Cursor 等 AI Coding 工具辅助开发，提高研发效率

任职要求：
1. 在校生或应届生，计算机、软件工程、人工智能等相关专业优先，专业不作为硬性限制
2. 有 Python 基础，能够看懂、修改和调试 Python 代码
3. 了解 HTTP、API、JSON 等基础开发概念
4. 对大模型、AI Agent、RAG 等 AI 应用开发有兴趣
5. 会使用 Git 或愿意快速学习
6. 能够使用 AI Coding 工具辅助开发，但需要能理解、修改和 Debug AI 生成的代码
7. 学习能力和动手能力强，遇到陌生技术愿意主动查阅资料并解决问题

加分项：
1. 使用过 FastAPI / Flask
2. 调用过大模型 API
3. 做过 AI Agent / RAG 项目
4. 会 MySQL / PostgreSQL
5. 会 React / Vue
6. 使用过 Docker / Linux
7. 熟悉 Codex、Claude Code、Cursor 等 AI Coding 工具
8. 有 GitHub / Gitee 项目
9. 有自己独立完成的 Demo、课程项目、比赛项目或个人产品

我们看重什么：
相比工作经验和学习经历，我们更关注动手能力、学习速度、AI Coding 能力和真实项目能力。
不要求有大模型训练、模型微调或算法研究经验。
如果你喜欢折腾 AI，有自己的项目，或者经常利用 AI 把想法真正做成可以运行的软件，非常欢迎投递。""",
    },
    {
        "position": {"title": "AI Agent 开发工程师", "department": "技术部", "headcount": None},
        "raw_text": """岗位名称：AI Agent 开发工程师

核心方向：企业级 Agent 业务自动化解决方案，涉及智能体开发、流程自动化、AI 搜索与知识管理等多个业务场景。

岗位职责：
1. 研发能力：开发优化 Agent 核心能力，包括任务规划、长期记忆、工具调用、多轮交互设计
2. 场景落地：基于 LangChain / LlamaIndex 等框架做二次开发，适配国产私有化大模型部署；LangGraph 源码级二开
3. 开发者工具链：开发业务工具集，打造数据接口，实现流程自动化闭环
4. 优化 RAG、Prompt 技术、降低模型幻觉，搭建 Agent 式交互体验
5. 搭建 Agent 化评测体系，跟进前沿技术，落地行业定制化 Agent 方案

任职要求：
1. 计算机相关专业，1 年及以上 Agent 落地研发经验
2. 精通 Python，熟悉 Agent 框架与向量数据库，掌握 RAG、Prompt 工程
3. 熟练使用主流模型接口、开源模型，具备微调或本地化部署经验

加分项：
1. 向量数据库、多模态、数字员工项目经验
2. 国企 OA / ERP / 流程自动化、知识图谱、系统集成项目经验
3. GLoRA 微调、PyTorch 训练、模型评测与调优经验
4. 可量化提升 Agent 任务效率的实际成果""",
    },
    {
        "position": {"title": "AI Agent 研发工程师（AI Native 创新方向）", "department": "技术部", "headcount": None},
        "raw_text": """岗位名称：AI Agent 研发工程师（AI Native 创新方向）

职位描述：
负责 Agent 从需求设计、开发、上线的全周期建设，将 Agent 能力落地到真实业务场景，并保障其稳定运行与持续迭代。

必要能力：
1. 独立实现 Agent 架构方案设计，不局限使用开源框架；熟悉状态机工作流（LangGraph）实现复杂任务编排
2. 优化通用工具集标准；熟练使用 Function Calling、MCP 工具调用协议
3. 搭建长短期记忆的 Agent 分层架构，实现多智能体通信、任务协同与调度
4. 创新 Agent 自反馈闭环能力
5. 搭建 Agent 化评测体系，跟进前沿技术，落地行业定制化 Agent 方案

任职要求：
1. Agent 设计与开发：多 Agent 协作的设计与实现
2. 可控性与工程化：习惯性判断 Agent 任务稳定性，不只关注能力表现；关注工具执行成功率与模型可靠性
3. 评测：搭建自动化离线测试能力，建立评估与回归测试能力
4. 工具能力与生态：熟悉主流评估工具与 Agent 框架；关注工具生态适配性、可编程性、稳定性与兼容性
5. 方案落地与运营：对线上质量、稳定性与成本负责，建立监控、告警、兜底与人工接管机制；设计资源使用预算、限流与熔断

加分项：
熟悉 Trace 路由追踪、任务控制机制、故障注入、记忆策略；关注 Agent 可控性、可编程性与任务完成效率。""",
    },
    {
        "position": {"title": "AI 应用开发工程师（电商方向）", "department": "技术部", "headcount": None},
        "raw_text": """岗位名称：AI 应用开发工程师（电商方向）

必要能力：
1. 独立编写业务 Prompt，设计 A/B 测试验证效果，沉淀迭代版本文档
2. 封装电商专属工具：商品查询、订单检索、美妆营销文案生成等
3. 统一工具注册、调用标准，搭建团队可复用工具库
4. 开发多智能体任务分发、跨 Agent 通信、结果汇总逻辑
5. 联调商品查询、客服答疑、营销生成等子智能体自动流转
6. 排查多 Agent 调用阻塞、数据同步异常、超时等待线上故障
7. 对接各类 LLM 厂商 API，封装统一调用 SDK
8. 通过 Prompt、温度、上下文窗口调优，解决美妆专业术语幻觉、回答不精准问题
9. 压缩模型响应耗时，提升电商咨询体验
10. 基于 FastAPI / Django 搭建 Agent 后端接口服务，完成单元测试与联调测试

岗位职责：
1. 拆解电商业务需求，独立设计智能体 Workflow 工作流
2. 使用 Python 完成 Agent 业务逻辑编码、流程编排；优化接口调用链路、减少等待耗时，适配电商咨询并发场景
3. 负责 Prompt 的日常设计、迭代与效果验证，做好版本记录；完成业务工具（Tool）与函数（Function）的封装、注册及调用适配
4. 执行主流大语言模型（LLM）的接入、适配与集成工作，配合开展 Prompt 优化、简单推理调优，提升模型输出效果与响应效率
5. 负责 AI 应用后端服务的日常开发、测试、部署及运维支持，保障服务稳定运行
6. 按照团队方案，完成多 Agent 协作相关的代码开发、联调测试

任职要求：
1. 计算机、软件工程、人工智能相关专业，1 年以上后端开发经验
2. 熟练使用 Python，具备良好的数据结构、算法基础
3. 熟悉 HTTP、REST API、数据库
4. 具备较强的数据分析、技术学习和独立实践能力
5. 熟悉 Docker、Linux、Git 及云服务部署流程""",
    },
    {
        "position": {"title": "AI Agent 应用开发工程师（智能内控方向）", "department": "技术部", "headcount": None},
        "raw_text": """岗位名称：AI Agent 应用开发工程师（智能内控方向）

职位职责：
1. 负责内控平台的技术系统设计与核心功能开发，运用大语言模型（LLM）、机器学习等 AI 技术，系统化防控资金、业务流程、合规等复杂风险
2. 深度参与下一代智能内控引擎的研发，将 AI 模型与实时策略深度融合，打造高并发、低延迟的决策系统，保障双十一等大促场景下的极致稳定性与精准性
3. 针对内控风控这一高专业性场景，探索并落地大模型的领域后训练（Post-training）、指令微调（SFT）、检索增强生成（RAG）等前沿方案，持续提升模型在风险识别、归因分析、合规判断等任务上的准确率与可解释性
4. 主导企业级 AI 应用的开发与落地，通过构建 AI Agent、智能工作流（Workflow）和知识库引擎，重塑风险的主动发现、智能分析与自动化治理方案
5. 持续跟踪大模型（LLM）、多模态、Agent 框架、模型对齐与安全等前沿技术，并推动其在内控领域的创新应用与业务价值转化

任职要求：
1. 硬性要求至少 2 年 AI 应用开发，有风控 / 金融行业经验大幅加分
2. 结合 LLM 构建风险识别模型，覆盖资金作弊、违规交易、流程不合规等电商风控场景
3. 具备业务风险建模能力，打通交易、资金、用户多维度数据做风险判定
4. 企业级风控 / 内控平台后端架构设计、全流程功能开发
5. 实时分布式决策引擎开发，融合传统规则策略 + LLM 大模型推理

必要能力：
1. 企业级风控 / 内控平台后端架构设计、全流程功能开发
2. 熟悉 LLM 微调实战：SFT 指令微调、领域后训练、Post-training，构建风控垂直大模型
3. 风控知识库 RAG 全链路落地，基于业务规则、历史风险案例做检索增强
4. 风控归因、合规研判、模型可解释性方案设计

加分项：
熟悉 Docker、Linux、Git 及云服务部署流程；Hive/Spark、分布式实时引擎、大促海量流量优化经验。""",
    },
]


def main():
    stamp = int(time.time())
    username = f"jdbuild_{stamp}"

    with httpx.Client(base_url=BASE, timeout=1800.0) as c:
        c.post("/auth/register", json={
            "username": username, "email": f"{username}@example.com",
            "password": "Smoke@12345", "full_name": "JD构建", "role": "hr"})
        token = c.post("/auth/login",
                       data={"username": username, "password": "Smoke@12345"}).json()["access_token"]
        c.headers.update({"Authorization": f"Bearer {token}"})

        print("=" * 74)
        print(f"从真实招聘截图构建 {len(JDS)} 个岗位 + JD")
        print("=" * 74)

        ok, fail = 0, 0
        for i, item in enumerate(JDS, 1):
            pos_info = item["position"]
            print(f"\n【{i}/{len(JDS)}】{pos_info['title']}")

            r = c.post("/positions/", json={
                "title": pos_info["title"],
                "department": pos_info.get("department"),
                "headcount": pos_info.get("headcount"),
            }, headers=smoke_common.admin_headers(c))
            if r.status_code != 200:
                print(f"   岗位创建失败 {r.status_code}: {r.text[:120]}")
                fail += 1
                continue
            pid = r.json()["id"]

            r = c.post("/job_descriptions/", data={
                "position_id": str(pid),
                "title": pos_info["title"],
                "raw_text": item["raw_text"],
            })
            if r.status_code != 200:
                print(f"   JD 创建失败 {r.status_code}: {r.text[:120]}")
                fail += 1
                continue
            jd = r.json()
            jid = jd["id"]

            r = c.post(f"/job_descriptions/{jid}/parse")
            if r.status_code != 200:
                print(f"   AI 解析失败: {r.text[:160]}")
                fail += 1
                continue
            jd = r.json()
            p = jd["parsed_data"]

            print(f"   硬技能 {len(p['required_skills'])} 项 / 加分 {len(p['preferred_skills'])} 项"
                  f" / 职责 {len(p['responsibilities'])} 条 / 价值观 {len(p['culture_values'])} 项")
            req = [s['skill'] for s in p['required_skills']]
            pref = [s['skill'] for s in p['preferred_skills']]
            print(f"   硬技能: {req}")
            if pref:
                print(f"   加分项: {pref}")
            if p['culture_values']:
                print(f"   价值观: {p['culture_values']}")

            r = c.post(f"/job_descriptions/{jid}/activate")
            if r.status_code != 200:
                print(f"   启用失败: {r.text[:160]}")
                fail += 1
                continue
            c.put(f"/positions/{pid}", json={"status": "active"})
            print(f"   ✓ 已启用（岗位 id={pid}, JD id={jid}）")
            ok += 1

        print("\n" + "=" * 74)
        print(f"成功 {ok} / {ok + fail}")
        r = c.get("/positions/", headers=c.headers)
        print(f"\n当前岗位清单：")
        for p in r.json():
            print(f"  · {p['title']}（JD {p['jd_count']} 份，启用 {p['active_jd_count']}）")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())