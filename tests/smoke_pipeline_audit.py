"""真实简历全链路体检脚本（人工验证用）。

针对指定简历走完「人岗匹配 → 问卷 → 三轮面试 → 招聘决策」，
并逐步输出每个环节的实测表现（进度节点、评分、AI 生成质量、拦截/沉淀）。

用法: python tests/smoke_pipeline_audit.py <pdf路径> <岗位>
"""
import json
import sys
import threading
import time

import httpx

import smoke_common

BASE = "http://127.0.0.1:8000"
PDF = sys.argv[1] if len(sys.argv) > 1 else None
POSITION = sys.argv[2] if len(sys.argv) > 2 else "信息科技岗（应用系统开发/数据分析建模/运维/信息安全管理）"
if not PDF:
    print("用法: python tests/smoke_pipeline_audit.py <pdf路径> <岗位>")
    sys.exit(2)

STAMP = str(int(time.time()))
USERNAME = f"audit_{STAMP}"
sse_events = []


def listen_sse(token):
    try:
        with httpx.stream("GET", f"{BASE}/sse/notifications",
                          headers={"Authorization": f"Bearer {token}"},
                          timeout=3600.0) as resp:
            for line in resp.iter_lines():
                if line.startswith("data:"):
                    try:
                        sse_events.append(json.loads(line[5:].strip()))
                    except Exception:
                        pass
    except Exception:
        pass


# 针对该简历（智能客服 Agent 平台 / 智能招聘中台）按题���关键词准备差异化答案
ANSWERS = {
    "agent": (
        "我用 LangGraph 把客服场景拆成 4 个智能体 A2A 协作：Orchestrator 负责意图识别与路由，"
        "客服智能体负责问答，性能智能体负责瓶颈定位，安全智能体负责输入输出合规。"
        "8 节点状态机是接入→意图理解→知识检索→自主推理→人工兜底→服务闭环的完整链路，"
        "节点之间用统一 state 传递，节点只做读 state 产出增量。"
        "挂起点用 interrupt() 做 human-in-the-loop，意图置信度低于阈值时转人工，"
        "配合 AsyncSqliteSaver 持久化 checkpoint，进程重启能从原挂起点续跑。"
        "因为 resume 时节点会整体重跑，我把所有写库动作收敛到统一层做先查后写，保证幂等。"
        "结果是 FAQ 首次响应压到 1 秒以内，技术问题排查平均 2-5 秒，LLM 调用减少 60%-80%。"
    ),
    "rag": (
        "选型上我评估过纯稠密向量、纯 BM25 和混合三套方案。纯向量对同义表达召回好，"
        "但对专有名词和精确数字（比如工龄、天数、版本号）容易召回语义相近但数值错误的文档；"
        "纯 BM25 正好相反。所以最终选稠密向量 + BM25 稀疏检索，用 RRF 做融合排序。"
        "向量侧用 bge-m3，输出 1024 维，和检索侧维度严格对齐——这点必须一致，"
        "否则检索会直接报错。融合后用 BGE-reranker 重排，把候选收敛到 top-5 再交给模型。"
        "文档侧做清洗、分块和结构化切片，保证切片语义完整。"
        "最终检索 Top-K 准确率提升到 95% 以上。"
    ),
    "ops": (
        "前端 React + 后端 FastAPI 分离，Vite 构建产物走 Nginx 托管，API 与 SSE 同源反代，"
        "不处理跨域。容器化用 Docker，K8s 上用 Helm 做模板、ArgoCD 做 GitOps 声明式发布，"
        "网关层用 APISIX 做路由、鉴权和限流。"
        "CI 用 GitHub Actions 三阶段门禁：前端构建、后端测试、容器构建，全绿才允许合并，"
        "镜像可复现发布。监控上把关键指标接进面板，劣化 5 分钟内告警。"
        "我强调的是可复现：依赖锁版本、构建参数固定、配置与代码分离，避免环境漂移。"
    ),
    "security": (
        "密钥管理与配置分离：所有密钥走环境变量或密钥管理服务注入，代码和镜像里不落明文，"
        "不同环境用不同凭据，泄露时可以只轮换而不动代码。"
        "接口鉴权用 JWT，做无状态校验，同时按角色区分权限边界，普通角色不能碰高危接口。"
        "网关层在 APISIX 上做流量管控与限流，按接口维度设置 QPS 和并发上限，"
        "配合输入侧拦截恶意内容，避免把非法流量透传到业务层。"
        "我在智能客服项目里也做了安全智能体，负责识别越权请求和提示注入，"
        "对用户输入先过滤再进模型，模型输出也要过一遍脱敏再返回。"
    ),
    "concurrency": (
        "分层设计：Redis 放热点和短期状态，PostgreSQL/MySQL 作为持久化真源，"
        "高频写用攒批和批量提交把单条 insert 合并，显著降低 IO 压力。"
        "读侧走缓存加布隆过滤防穿透，TTL 加随机抖动防雪崩。"
        "RabbitMQ 做异步解耦和削峰，高峰期请求先入队再异步落库，保证不丢数据也不阻塞。"
        "幂等用唯一索引加 ON CONFLICT 兜住重复提交，应用层不依赖分布式锁也能保证一致性。"
        "连接池按压测结果调整上限，必要时配合连接池中间件做事务级池化，避免打满数据库连接。"
        "这样高峰期服务不丢数、不阻塞。"
    ),
    "challenge": (
        "挑战最大的是客服 Agent 在高峰期同时出现响应慢和答案不准，两者看起来无关，"
        "但根因是同一个：检索召回质量不稳。"
        "现象是 FAQ 首次响应超过 5 秒、技术问题答非所问，人工兜底量暴涨。"
        "我的排查思路是先分层定位，不急着改代码：先用链路追踪把耗时拆开，"
        "确认慢主要在检索而非模型推理；再抽样人工标注一批 query 的召回结果，"
        "统计 Top-K 命中率，发现纯向量检索对专有业务术语和精确数字召回很差。"
        "两个现象就合并成了一个根因。"
        "动作上我做了三件事：改用稠密向量加 BM25 的混合召回并用 RRF 融合，"
        "再叠加 BGE-reranker 重排把候选收敛到 top-5；同时给低置信度场景加转人工兜底；"
        "并且把标注集固化下来，每次改动都跑一次回归，防止优化来回震荡。"
        "取舍上我放弃了单纯换更大模型的方案，因为瓶颈在检索不在生成，"
        "那样只会推高成本而治标不治本。"
        "结果 FAQ 首次响应压到 1 秒以内，技术问题排查平均 2-5 秒，LLM 调用减少 60%-80%。"
        "复盘下来我记住的是：先定位再优化，且要用可量化的标注集验证，而不是凭感觉调参。"
    ),
}

KEYWORDS = [
    # 顺序敏感：security / concurrency 必须排在 ops 前面。
    # "网关""全栈"在安全题、高并发表里也会出现，放前面会把安全题误判成运维题。
    (("挑战", "最难", "排查", "问题背景", "现象", "故障", "瓶颈"), "challenge"),
    (("安全", "密钥", "鉴权", "权限", "越权", "限流", "防护", "合规"), "security"),
    (("高并发", "缓存", "消息队列", "RabbitMQ", "数据读写", "削峰", "幂等"), "concurrency"),
    (("智能体", "Orchestrator", "状态机", "A2A", "节点", "LangGraph", "中断", "checkpoint"), "agent"),
    (("向量", "检索", "RRF", "rerank", "BM25", "bge", "Chroma", "Milvus", "建模", "数据分析"), "rag"),
    (("部署", "运维", "CI/CD", "CI ", "Docker", "K8s", "Kubernetes", "APISIX", "Helm", "ArgoCD", "全栈", "前端", "React"), "ops"),
]


def answer_for(question: str) -> str:
    """按题目内容选答案。

    用「命中关键词数量最多」的类别，而不是首个命中：
    早先按顺序匹配时，"安全专家智能体"里的"安全"会把 LangGraph 编排题
    误判成安全题，导致多题拿到同一份答案——AI 评分模型两次都精准指出了这点。
    评测工具本身不可信，测出来的分数就没有意义。
    """
    scores = {}
    for kws, key in KEYWORDS:
        hits = sum(1 for k in kws if k in question)
        if hits:
            scores[key] = scores.get(key, 0) + hits
    if not scores:
        return ANSWERS["challenge"]
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
                print(f"      [{label}] {cur[0]:<14} {str(cur[1]):<26} {str(cur[2]):>3}%"
                      + (f"  挂起:{cur[3]}" + (f" 第{cur[4]}轮" if cur[4] else "") if cur[3] else ""))
                last = cur
            if run.get("status") == "failed":
                print(f"      !! 失败 {json.dumps(run.get('results'), ensure_ascii=False)[:300]}")
                return run
            if predicate(run, intr):
                return run
        time.sleep(3)
    print(f"      !! 超时，最后 {last}")
    return None


def main():
    with httpx.Client(base_url=BASE, timeout=3600.0) as c:
        c.post("/auth/register", json={
            "username": USERNAME, "email": f"{USERNAME}@example.com",
            "password": "Smoke@12345", "full_name": "链路体检", "role": "hr",
        }, headers=smoke_common.admin_headers(c))
        token = c.post("/auth/login",
                       data={"username": USERNAME, "password": "Smoke@12345"}).json()["access_token"]
        c.headers.update({"Authorization": f"Bearer {token}"})
        threading.Thread(target=listen_sse, args=(token,), daemon=True).start()
        time.sleep(2)

        print("=" * 78)
        print("环节一：简历上传 + 人岗匹配")
        print("=" * 78)
        # 邮箱唯一约束：用随机 11 位手机号前缀构造，保留「手机号前缀邮箱」形态，
        # 既能验证 PII 处理，又不会因重复邮箱被 400
        import random
        phone_prefix = "17" + "".join(random.choice("0123456789") for _ in range(9))
        r = c.post("/candidates/", json={
            "name": "候选人A", "email": f"{phone_prefix}@163.com",
            "phone": phone_prefix, "source": "简历投递", "position": POSITION,
        })
        if r.status_code != 200:
            print(f"  创建候选人失败 {r.status_code}: {r.text[:200]}")
            return 1
        cid = r.json()["id"]
        print(f"  候选人 id={cid}  岗位={POSITION[:30]}…")
        print(f"  投递邮箱={phone_prefix}@163.com（手机号前缀邮箱）")

        with open(PDF, "rb") as f:
            r = c.post(f"/candidates/{cid}/upload-resume",
                       files={"file": ("resume.pdf", f, "application/pdf")})
        d = r.json()
        print(f"  解析: {d.get('source_type')} / OCR={d.get('used_ocr')} / {d.get('text_length')} 字")
        head = str(d.get("parsed_text"))[:120].replace("\n", " ")
        print(f"  邮箱脱敏检查: {'[手机号已隐藏]@163.com  ← 旧行为' if '[手机号已隐藏]@' in str(d.get('parsed_text')) else '邮箱完整保留  ← 新行为'}")

        run = wait_for(c, cid, lambda r, i: r.get("status") in ("waiting_human", "completed"),
                       label="人岗匹配")
        if not run:
            return 1

        print("\n  四维评分：")
        for e in c.get("/evaluations/", params={"candidate_id": cid}).json():
            print(f"    {e['dimension']:<8} {e['score']:>3} 分")

        if run.get("status") == "completed":
            print("\n  流程在文化契合环节即终止（淘汰分支）")
            report_final(c, cid)
            c.delete(f"/candidates/{cid}")
            return 0

        intr = (run.get("results") or {}).get("interrupt") or {}
        qid = intr.get("questionnaire_id")
        print("\n" + "=" * 78)
        print(f"环节二：AI 问卷（id={qid}，{intr.get('question_count')} 题，阈值 60 分）")
        print("=" * 78)
        qs = c.get(f"/questionnaires/{qid}").json().get("questions", [])
        for i, q in enumerate(qs, 1):
            print(f"  Q{i}. {str(q.get('question'))[:100]}")

        payloads = {str(i): answer_for(str(q.get("question", ""))) for i, q in enumerate(qs, 1)}
        sub = c.post(f"/questionnaires/{qid}/responses",
                     json={"candidate_id": cid, "responses": payloads})
        if sub.status_code != 200:
            # 必须显式检查：静默忽略会让脚本在轮询里死等，看不出真实卡点
            print(f"\n  !! 问卷提交失败 {sub.status_code}: {sub.text[:200]}")
            return 1
        print("  问卷已提交，等待 AI 评分…")

        run = wait_for(c, cid,
                       lambda r, i: r.get("status") == "completed"
                       or (r.get("status") == "waiting_human" and i.get("type") == "await_interview"),
                       label="问卷评分")
        if not run:
            return 1

        sc = [e for e in c.get("/evaluations/", params={"candidate_id": cid}).json()
              if e["dimension"] == "问卷测评"]
        if sc:
            print(f"\n  问卷得分 {sc[0]['score']}（阈值 60）→ "
                  f"{'进入面试环节' if sc[0]['score'] >= 60 else '淘汰'}")
            print(f"  AI 评语: {str(sc[0]['comment'])[:160]}")

        rounds = 0
        while run and run.get("status") == "waiting_human":
            intr = (run.get("results") or {}).get("interrupt") or {}
            if intr.get("type") != "await_interview":
                break
            rno = intr.get("round")
            print("\n" + "=" * 78)
            print(f"环节三：第 {rno} 轮面试（人工录入后由 AI 判定是否晋级）")
            print("=" * 78)
            ivs = c.get("/interviews/", params={"candidate_id": cid}).json()
            pend = [i for i in ivs if i.get("round") == rno and i.get("status") != "completed"]
            if not pend:
                print("  未找到待录入面试记录")
                break
            iv = pend[0]
            print(f"  面试 id={iv['id']}  排期 {iv.get('scheduled_at')}  "
                  f"面试官 id={iv.get('interviewer_id')}")
            print(f"  AI 生成面试题 {len(iv.get('questions') or [])} 道：")
            for q in (iv.get("questions") or [])[:3]:
                print(f"    - {str(q.get('question'))[:95]}")

            score = {1: 85, 2: 88, 3: 90}.get(rno, 86)
            fb = {
                1: "技术理解到位，LangGraph 编排与 RAG 检索选型讲得清取舍，量化数据可信。不足是 K8s 生产运维经验尚浅。85 分，建议进入下一轮。",
                2: "STAR 要素完整，能讲清项目背景、取舍与结果，反思到位，表达清晰。88 分，建议进入 HR 面。",
                3: "求职动机清晰，对金融科技业务理解有思考，稳定性好，薪资预期合理。90 分，建议录用。",
            }.get(rno, "表现良好，建议进入下一轮。")
            c.post(f"/interviews/{iv['id']}/complete", params={"score": score, "feedback": fb})
            print(f"  录入 {score} 分 → 等待 AI 判定晋级…")

            prev = rno
            run = wait_for(c, cid,
                           lambda r, i: r.get("status") == "completed"
                           or (r.get("status") == "waiting_human" and i.get("round") != prev),
                           label=f"第{rno}轮后")
            rounds += 1
            if rounds > 4:
                break

        print("\n" + "=" * 78)
        print("环节四：招聘决策")
        print("=" * 78)
        report_final(c, cid)
        c.delete(f"/candidates/{cid}")
        return 0


def report_final(c, cid):
    final = c.get(f"/candidates/{cid}/workflow")
    if final.status_code != 200:
        print("  查询失败")
        return
    fr = final.json()
    res = fr.get("results") or {}
    print(f"  终局状态: {fr.get('status')} / progress={fr.get('progress')}%")
    if fr.get("status") == "completed":
        print(f"  决策    : {res.get('final_decision')}")
        print(f"  综合分  : {res.get('overall_score')}")
        print(f"  分维度  : 技能{res.get('skill_match_score')} 经验{res.get('experience_match_score')} "
              f"教育{res.get('education_match_score')} 文化{res.get('culture_match_score')} "
              f"问卷{res.get('questionnaire_score')}")
        ivs = res.get("interview_scores") or []
        if ivs:
            print(f"  面试    : " + ", ".join(f"R{i['round']}={i['score']}" for i in ivs))
        rec = (res.get("analysis") or {}).get("final_recommendation") or {}
        for r in (rec.get("reasons") or [])[:2]:
            print(f"  理由    : {str(r)[:130]}")
        for r in (rec.get("risks") or [])[:2]:
            print(f"  风险    : {str(r)[:130]}")

    print(f"  候选人状态: {c.get(f'/candidates/{cid}').json().get('status')}")
    tp = [t for t in c.get("/talent-pool/").json() if t["candidate_id"] == cid]
    if tp:
        print(f"  人才库  : tags={tp[0]['tags']}")
    ivs = c.get("/interviews/", params={"candidate_id": cid}).json()
    if ivs:
        print(f"  面试记录: " + ", ".join(f"R{i['round']}={i['status']}/{i.get('score')}" for i in ivs))
    kinds = {}
    for e in sse_events:
        kinds[e.get("type", "?")] = kinds.get(e.get("type", "?"), 0) + 1
    print(f"  SSE 推送: {len(sse_events)} 条 {kinds}")


if __name__ == "__main__":
    sys.exit(main())