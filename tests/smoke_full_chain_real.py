"""真实简历 × 真实岗位 JD 的全链路验证。

绑定「AI 应用开发实习生（Agent 方向）」的已启用 JD，走完：
  建候选人(绑 JD) → 上传简历(自动触发) → 问卷挂起 → 提交作答
  → 三轮面试录入 → 招聘决策

答案按题目内容"命中关键词最多"的类别选取，避免多题复用同一套话术
（评测模型会精准识别模板化作答并压分）。

用法: python tests/smoke_full_chain_real.py <pdf路径> [岗位标题关键字]
"""
import json
import sys
import time

import httpx

BASE = "http://127.0.0.1:8000"
PDF = sys.argv[1] if len(sys.argv) > 1 else None
POSITION_KEYWORD = sys.argv[2] if len(sys.argv) > 2 else "AI 应用开发实习生"

if not PDF:
    print("用法: python tests/smoke_full_chain_real.py <pdf路径> [岗位关键字]")
    sys.exit(2)

# ---------------------------------------------------------------- 作答库
# 按岗位实际考察点准备差异化答案，逐题命中关键词最多的类别
ANSWERS = {
    "backend": (
        "我用 FastAPI 独立完成后端接口设计。选它主要因为原生 async 能直接对接大模型的异步接口，"
        "不需要为每次模型调用单独起线程。接口层我按「入参校验用 Pydantic、业务逻辑下沉 service、"
        "统一响应结构与全局异常处理」三层拆，错误码可追溯。分页、限流、超时这些也做了兜底："
        "模型调用统一 45 秒超时 + 指数退避重试 2 次，避免单个慢请求拖垮整个 worker。"
        "联调时我用 Postman 和 pytest 各写一遍契约测试，保证前端拿到的字段结构和文档一致。"
    ),
    "llm_api": (
        "我封装了一层大模型 API 调用，屏蔽各家差异。核心做法是统一入参出参 + 模型名到供应商的路由表，"
        "切换模型只改配置不改业务代码。降本上做了三层：语义缓存（相似问题命中缓存直接返回，实测省了约六成调用）、"
        "按场景选模型（意图分类用小模型、复杂生成才走大模型）、流式输出降低首字延迟。"
        "效果上我建了一组固定评测样本，每次改 prompt 都跑一遍对比准确率和 token 消耗，避免凭感觉调优。"
    ),
    "agent": (
        "我在智能客服项目里用 LangGraph 做过完整 Agent 链路。4 个智能体 A2A 协作："
        "Orchestrator 负责意图识别与路由，客服/性能/安全三个专家各管一段。"
        "工具调用统一用 Function Calling，把 PostgreSQL 查询、飞书通知、GitHub 操作都封成带描述和参数 schema 的工具，"
        "让模型自己决定何时调、调什么。多轮对话最容易断的是上下文，我做了三层记忆："
        "Redis 存短期会话、PostgreSQL 存长期偏好、每轮拼装上下文注入。意图置信度低于阈值就转人工，不硬答。"
        "ReAct 模式用在需要多步推理的排查类问题上，推理和动作交替，直到拿到答案或触发兜底。"
    ),
    "rag": (
        "RAG 这块我踩过不少坑。最初用纯向量检索，Top-K 准确率只有 70% 左右，"
        "问题出在对专有名词和精确数字召回很差——问「工龄三年」会召回讲薪资的文档。"
        "后来改成稠密向量 + BM25 稀疏检索混合召回，用 RRF 融合排序，再叠一层 BGE-reranker 重排把候选收敛到 top-5，"
        "Top-K 准确率提到 95% 以上。工程上：bge-m3 输出 1024 维，维度必须和检索侧严格对齐否则直接报错；"
        "文档先清洗分块保证切片语义完整；向量库用 Chroma 开发期、数据量大后换 Milvus；原始文件放 MinIO 只存 URI 不进库。"
    ),
    "data": (
        "非结构化数据处理我做过多类。企业文档主要是 PDF、Excel、图片三类："
        "文本型 PDF 用 PyPDF2 直接抽，扫描件走 OCR（集成过 PaddleOCR 和 Tesseract），"
        "Excel 按表头语义识别字段类型并做校验，图片先 OCR 再做结构化抽取。"
        "清洗上重点是去噪、统一编码和格式、补全缺失值，然后才入库。"
        "我会先建标注集做效果验收，再上线——否则很难判断清洗规则改动到底是变好还是变坏。"
    ),
    "frontend": (
        "前端我独立做过 React 版本，主要是多轮对话界面、流式响应展示和会话管理。"
        "流式这块用 SSE，前端用 fetch + ReadableStream 逐块读取并渲染打字机效果，"
        "同时要处理断连重连和错误降级。会话列表做本地缓存加服务端分页，"
        "长对话做了虚拟滚动。跨端上做过移动端 H5 适配，用的是响应式加 rem 方案。"
        "和后端约定好接口规范后联调比较顺，我习惯先用 Mock 把交互跑通再接真接口。"
    ),
    "infra": (
        "联调和测试这块，我会在开发阶段用 pytest 写单测覆盖核心业务逻辑，Mock 掉外部依赖；"
        "接口层做契约测试；联调用 Postman 集合，配合 Mock 服务让前后端并行开发。"
        "Bug 修复我习惯先补一个能复现的失败用例，再改代码，防止回归。部署侧用 Docker 容器化，"
        "GitHub Actions 做三阶段 CI 流水线（前端构建、后端测试、容器构建），全绿才允许合并，"
        "镜像版本可追溯、可回滚。数据库迁移单独走脚本，和应用发布解耦。"
    ),
    "aicoding": (
        "我日常用 Cursor、Copilot 这类 AI Coding 工具，但我的原则是「可以生成，必须验证」。"
        "具体做法：先让工具给出方案而不是直接写码，我确认设计后再让它落地；"
        "生成的代码一律走我自己的 review，重点看异常处理、边界条件和事务一致性；"
        "然后用单测和实际运行结果验证，不看代码「像不像对的」。"
        "在招聘中台项目里我让 AI Coding 辅助写了 14 项后端测试用例和 CI 配置，效率确实提升明显，"
        "但最终的断言设计和边界 case 还是我自己定的——工具能提效，不能替代对业务的理解。"
    ),
}

KEYWORDS = [
    (("接口", "后端", "FastAPI", "Flask", "服务端", "API 设计", "参数", "返回值"), "backend"),
    (("大模型", "API 调用", "DeepSeek", "OpenAI", "Claude", "Qwen", "成本", "token", "降本", "模型选型"), "llm_api"),
    (("Agent", "智能体", "LangGraph", "ReAct", "MCP", "Function Calling", "Workflow", "工具调用", "多轮"), "agent"),
    (("RAG", "向量", "检索", "BM25", "RRF", "rerank", "Chroma", "Milvus", "Embedding", "Top-K", "知识库"), "rag"),
    (("数据", "PDF", "Excel", "文档", "图片", "OCR", "清洗", "结构化", "采集"), "data"),
    (("前端", "React", "Vue", "页面", "交互", "流式", "会话", "H5", "跨端"), "frontend"),
    (("测试", "联调", "部署", "Bug", "调试", "CI", "Docker", "流水线"), "infra"),
    (("Cursor", "Claude Code", "Copilot", "AI Coding", "代码质量", "生成代码"), "aicoding"),
]


def answer_for(question: str) -> str:
    scores = {}
    for kws, key in KEYWORDS:
        hits = sum(1 for k in kws if k in question)
        if hits:
            scores[key] = scores.get(key, 0) + hits
    if not scores:
        return ANSWERS["backend"]
    return ANSWERS[max(scores, key=scores.get)]


def wait_for(c, cid, predicate, timeout=1200, label=""):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        r = c.get(f"/candidates/{cid}/workflow")
        if r.status_code == 200:
            run = r.json()
            intr = (run.get("results") or {}).get("interrupt") or {}
            cur = (run.get("status"), run.get("current_step"), run.get("progress"),
                   intr.get("type"), intr.get("round"))
            if cur != last:
                print(f"    [{label}] {cur[0]:<14}{str(cur[1]):<24}{str(cur[2]):>3}%"
                      + (f"  挂起:{cur[3]}" + (f" 第{cur[4]}轮" if cur[4] else "") if cur[3] else ""))
                last = cur
            if run.get("status") == "failed":
                print(f"    !! 失败 {json.dumps(run.get('results'), ensure_ascii=False)[:300]}")
                return run
            if predicate(run, intr):
                return run
        time.sleep(3)
    print(f"    !! 超时，最后状态 {last}")
    return None


def main():
    stamp = int(time.time())
    with httpx.Client(base_url=BASE, timeout=1800.0) as c:
        # 用种子 HR 账号登录（权限已收紧，HR 才能建候选人和绑 JD）
        r = c.post("/auth/login", data={"username": "hr001", "password": "hr123456"})
        if r.status_code != 200:
            print(f"HR 登录失败 {r.status_code}: {r.text[:200]}")
            return 1
        c.headers.update({"Authorization": f"Bearer {r.json()['access_token']}"})

        # 1. 定位目标岗位 JD
        print("=" * 72)
        print(f"步骤 1：绑定岗位 JD（关键字「{POSITION_KEYWORD}」）")
        jds = c.get("/job_descriptions/", params={"status": "active"}).json()
        target = next((j for j in jds if POSITION_KEYWORD in j["title"]), None)
        if not target:
            print("  未找到匹配的已启用 JD，现有：")
            for j in jds:
                print(f"    · {j['title']}")
            return 1
        prof = target["parsed_data"]
        print(f"  命中：{target['title']}（JD id={target['id']}）")
        print(f"  硬技能 {len(prof['required_skills'])} 项 / 加分 {len(prof['preferred_skills'])} 项"
              f" / 价值观 {len(prof['culture_values'])} 项")
        print(f"  学历要求：{prof['basic'].get('education_required') or '（未设）'}"
              f"  年限要求：{prof['basic'].get('experience_years_required') or '（未设）'}")

        # 2. 建候选人并绑定 JD
        print("\n步骤 2：创建候选人并绑定 JD")
        cands = c.get("/candidates/", params={"search": f"赛博威_{stamp}"})
        r = c.post("/candidates/", json={
            "name": "王林海",
            "email": f"wlh_{stamp}@163.com",
            "phone": "17307179854",
            "source": "简历投递",
            "position": "AI 应用全栈开发工程师",
            "job_description_id": target["id"],
        })
        print(f"  HTTP {r.status_code} 候选人 id={r.json().get('id')}")
        cid = r.json()["id"]

        # 3. 上传简历（自动触发工作流）
        print("\n步骤 3：上传简历（自动触发基于 JD 的匹配）")
        with open(PDF, "rb") as f:
            r = c.post(f"/candidates/{cid}/upload-resume",
                       files={"file": ("resume.pdf", f, "application/pdf")})
        print(f"  HTTP {r.status_code}  提取 {r.json().get('text_length')} 字"
              f"  来源 {r.json().get('source_type')}")

        # 4. 等问卷挂起
        print("\n步骤 4：等待人岗匹配完成 + 问卷挂起")
        run = wait_for(c, cid, lambda ru, i: ru.get("status") in ("waiting_human", "completed"),
                       label="匹配")
        if not run or run.get("status") != "waiting_human":
            return 1

        evs = {e["dimension"]: e for e in c.get("/evaluations/", params={"candidate_id": cid}).json()}
        print("\n  四维评分（依据该 JD 逐条核对）：")
        for dim in ("技能匹配", "经验匹配", "教育匹配", "文化契合"):
            e = evs.get(dim)
            if e:
                print(f"    {dim}: {e['score']} 分")
                print(f"       {str(e.get('comment'))[:180]}")

        intr = (run.get("results") or {}).get("interrupt") or {}
        qid = intr.get("questionnaire_id")
        print(f"\n步骤 5：AI 问卷（id={qid}，{intr.get('question_count')} 题）")
        qs = c.get(f"/questionnaires/{qid}").json().get("questions", [])
        payload = {}
        for i, q in enumerate(qs, 1):
            text = str(q.get("question", ""))
            print(f"    Q{i}. {text[:88]}")
            payload[str(i)] = answer_for(text)

        r = c.post(f"/questionnaires/{qid}/responses",
                   json={"candidate_id": cid, "responses": payload})
        print(f"  提交 {r.status_code}")

        run = wait_for(
            c, cid,
            lambda ru, i: ru.get("status") == "completed"
            or (ru.get("status") == "waiting_human" and i.get("type") == "await_interview"),
            label="问卷评分后")
        if not run:
            return 1

        q = next((e for e in c.get("/evaluations/", params={"candidate_id": cid}).json()
                  if e["dimension"] == "问卷测评"), None)
        if q:
            verdict = "进入面试" if q["score"] >= 60 else "淘汰"
            print(f"\n  问卷得分 {q['score']}（阈值 60）→ {verdict}")
            print(f"  AI 评语: {str(q['comment'])[:200]}")

        # 6. 逐轮面试
        rounds = 0
        while run and run.get("status") == "waiting_human":
            intr = (run.get("results") or {}).get("interrupt") or {}
            if intr.get("type") != "await_interview":
                break
            rno = intr.get("round")
            print(f"\n步骤 6.{rno}：第 {rno} 轮面试")
            ivs = c.get("/interviews/", params={"candidate_id": cid}).json()
            pend = [i for i in ivs if i.get("round") == rno and i.get("status") != "completed"]
            if not pend:
                print("  未找到待录入面试记录")
                break
            iv = pend[0]
            print(f"  面试 id={iv['id']} 排期 {iv.get('scheduled_at')} 面试官 id={iv.get('interviewer_id')}")
            for q2 in (iv.get("questions") or [])[:3]:
                print(f"    - {str(q2.get('question'))[:86]}")

            score = {1: 86, 2: 88, 3: 90}.get(rno, 86)
            fb = {
                1: "技术理解到位，FastAPI 异步与大模型 API 封装讲得清取舍，RAG 混合检索+重排的踩坑经历很实在。扣分点是无生产环境经历。86 分，建议进入下一轮。",
                2: "回答切题且有案例，工程取舍讲得清楚（为什么不用纯向量检索、为什么加语义缓存），协作与主动学习有具体证据。88 分，建议进入 HR 面。",
                3: "求职动机清晰，对岗位的技术方向有持续跟进意愿，学习能力与动手能力有项目佐证，稳定性好。90 分，建议录用。",
            }.get(rno, "表现良好，建议进入下一轮。")
            c.post(f"/interviews/{iv['id']}/complete",
                   params={"score": score, "feedback": fb})
            print(f"  录入 {score} 分")

            prev = rno
            run = wait_for(
                c, cid,
                lambda ru, i: ru.get("status") == "completed"
                or (ru.get("status") == "waiting_human" and i.get("round") != prev),
                label=f"第{rno}轮后")
            rounds += 1
            if rounds > 4:
                break

        # 7. 终局
        print("\n" + "=" * 72)
        print("步骤 7：招聘决策")
        final = c.get(f"/candidates/{cid}/workflow").json()
        res = final.get("results") or {}
        print(f"  终局: {final.get('status')} / progress={final.get('progress')}%")
        if final.get("status") == "completed":
            print(f"  决策    : {res.get('final_decision')}")
            print(f"  综合分  : {res.get('overall_score')}")
            print(f"  技能/经验/教育/文化/问卷: "
                  f"{res.get('skill_match_score')}/{res.get('experience_match_score')}/"
                  f"{res.get('education_match_score')}/{res.get('culture_match_score')}/"
                  f"{res.get('questionnaire_score')}")
            if res.get("unconstrained_dimensions"):
                print(f"  未设限维度: {res['unconstrained_dimensions']}")
            ivs = res.get("interview_scores") or []
            if ivs:
                print(f"  面试    : " + ", ".join(f"R{i['round']}={i['score']}" for i in ivs))
            rec = (res.get("analysis") or {}).get("final_recommendation") or {}
            for r2 in (rec.get("reasons") or [])[:3]:
                print(f"  理由    : {str(r2)[:140]}")
            for r2 in (rec.get("risks") or [])[:2]:
                print(f"  风险    : {str(r2)[:140]}")
        print(f"  候选人状态: {c.get(f'/candidates/{cid}').json().get('status')}")
        pool = [t for t in c.get("/talent-pool/").json() if t["candidate_id"] == cid]
        if pool:
            print(f"  人才库  : tags={pool[0]['tags']}")

        c.delete(f"/candidates/{cid}")
        print("\n已清理测试候选人（岗位与 JD 保留）")
    return 0


if __name__ == "__main__":
    sys.exit(main())