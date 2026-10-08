"""全链路工作流验证脚本（人工验证用）。

驱动 LangGraph 招聘工作流的 4 个人机挂起点走到底：
  简历上传(自动触发) -> 等待问卷 -> 提交问卷 -> 等待第1轮面试 -> 录入面试
  -> 等待第2轮 -> 录入 -> 等待第3轮 -> 录入 -> 终局 completed/淘汰

同时监听 SSE，验证进度事件是否真实推送。
用法: python tests/smoke_workflow_chain.py
"""

import json
import sys
import threading
import time

import httpx

BASE = "http://127.0.0.1:8000"
STAMP = str(int(time.time()))
USERNAME = f"wf_{STAMP}"

ANSWERS = {
    "1": "异步网关侧：招聘平台日均 200 万次 API 调用，网关是统一入口。"
         "我用 FastAPI 的 async 端点承接请求，瓶颈原本不在 CPU 而在同步等待——"
         "链路追踪显示 70% 时间耗在同步调用 LLM 供应商和向量检索上，线程池大量排队。"
         "改成 asyncio 并发聚合，用 Semaphore 按下游实际承载力限流，避免打垮供应商；"
         "同时用 orjson 替换标准 json 做序列化。"
         "LangGraph 编排侧：工作流包含简历解析→技能提取→技能匹配→经验评估→文化契合→"
         "问卷挂起→三轮面试→最终决策这些节点。"
         "节点之间用 TypedDict 统一承载状态，节点只做「读 state 产出增量」，"
         "避免节点间隐式耦合；挂起点用 interrupt() 实现 human-in-the-loop，"
         "问卷作答和面试录算是真实人工事件，配合 AsyncSqliteSaver 持久化 checkpoint，"
         "进程重启能从原挂起点续跑。"
         "这里有个关键坑：因为 resume 时节点会整体重跑，所有写库动作都必须幂等，"
         "我把它们统一收敛到 db_actions 层做「先查后写」，保证重放不产生重复数据。"
         "结果：网关 P95 稳定在 180ms，支撑住大促峰值零超时。",
    "2": "检索链路我设计成五段式：查询改写 → 多路召回 → 融合去重 → 重排 → 上下文组装。"
         "查询改写：用户口语化提问先用 LLM 改写成检索友好的形式，并做同义词扩展，"
         "比如「年假几天」会补上「带薪年休假 法定假期」这类同义表述，解决字面不匹配。"
         "多路召回：向量召回保证语义相似，BM25 召回保证专有名词和数字的精确命中"
         "（这对年假天数、工龄这类必须精确的字段很关键，纯向量容易召回语义近但数字错的文档），"
         "两路各取 top-50。"
         "融合去重：用倒数排名融合把两路结果合并，同一文档在两路都出现时排名会被提升，"
         "再按文档 id 去重。"
         "重排：用 cross-encoder 对候选的 query-文档对逐一打分重排，"
         "收敛到 top-5。这一步是收益最大的，因为召回阶段追求不漏，重排阶段追求精准。"
         "上下文组装：只把重排后的文档塞进上下文，并明确告诉模型只依据给定资料作答，"
         "资料不足时要求它回答『资料未提及』而不是编造。"
         "存储层用 Milvus 按租户哈希分片，单分片控制在 500 万条以内，"
         "查询并发打散到各分片后归并；Embedding 结果按内容哈希缓存进 Redis，命中率 72%。"
         "效果是检索 P99 从 1.2s 降到 320ms，且人事制度类问题的回答准确率明显提升。",
    "3": "这 800ms 我是分块拆出来的，用链路追踪逐段打点：LLM 供应商调用 420ms、"
         "向量检索 310ms、序列化与网络 70ms。确定主因后再逐项处理。"
         "LLM 调用 420ms：供应商本身 RT 比较稳定，所以优化的不是模型而是调用方式。"
         "把串行改成 asyncio 并发聚合，一次请求里多个候选人的简历解析并行发起，"
         "单请求墙钟时间从 3 次串行的 1260ms 降到 420ms；同时对非实时场景加 Redis 缓存，"
         "命中直接返回。语义缓存命中率约 34%。"
         "向量检索 310ms：这是我从 1.2s 降下来最狠的一段。"
         "第一步发现 72% 的查询在 1 小时内重复，属于纯重复计算，"
         "上 Redis 缓存后直接省掉 860ms；第二步把召回链路从纯向量改成向量+BM25 混合，"
         "减少了因语义漂移导致的多轮重试检索。"
         "序列化 70ms：orjson 替换标准 json，并避免对大对象做重复序列化。"
         "最终 P95 800ms→180ms，检索 P99 1.2s→320ms。"
         "我把这几个指标接进了监控面板，任何发布导致的劣化 5 分钟内告警，"
         "避免优化成果悄悄回退。",
    "4": "读写分离：写走主库，查询走只读副本，用 SQLAlchemy 的 Session 路由按意图分发，"
         "避免同一次业务里读写互相阻塞。关键是不能靠猜，我用 SQLAlchemy 事件钩子统计了每个"
         "Repository 方法的真实读写比例，只有确认是读多写少的查询才落到副本上。"
         "高并发写入：所有写操作收敛到一个写入入口，用 PostgreSQL 的 "
         "INSERT ... ON CONFLICT 做幂等，配合唯一索引兜住重复提交，"
         "这样应用层不需要分布式锁就能保证一致性。"
         "批量与攒批：明细写入走批量攒批，500 条或 100ms 触发一次批量提交，把单条 insert 变成批量 insert，"
         "数据库 IO 压力下降一个数量级。"
         "低延迟读取：Redis 做缓存层，用 cache-aside，热点数据按业务维度预热；"
         "缓存穿透用布隆过滤器拦截不存在的 key，雪崩用 TTL 加随机抖动打散。"
         "连接池：按压测结果把连接池上限调到 60，配合 PgBouncer 做事务级池化，"
         "避免应用侧连接数打满数据库。"
         "结果：写入峰值 8000 TPS 时主库 CPU 稳定在 60% 以内，读取 P99 控制在 30ms 以内。",
    "5": "具体案例是关于向量检索服务要不要直接上 Milvus 的技术分歧。"
         "情境：大促前两周，检索 P99 已经到 1.2s，业务方要求必须压到 500ms 内，"
         "而 Chroma 在千万级向量下召回延迟明显上升。"
         "任务：我需要在三周内交付，且不能影响现有业务。"
         "行动：我没有在会上争论技术优劣，而是先做了一件事——拉一周的真实流量数据，"
         "统计出 72% 的查询在 1 小时内重复出现，说明问题主要不是检索引擎慢，而是重复计算。"
         "于是我提出分两步：第一步先上 Redis 缓存把重复查询挡掉，成本几乎为零、风险极低；"
         "第二步等缓存命中率稳定后再评估是否迁移 Milvus。"
         "同事的顾虑是'缓存会掩盖真正的容量问题'，我同意这个风险，"
         "所以同时做了分片预案并用压测证明迁移后能达标，只是把它排在缓存之后。"
         "结果：第一步一周上线，P99 降到 320ms，满足了大促要求；"
         "第二步按预案在流量峰值前完成，没有返工。"
         "反思：这次让我确认了一件事——技术分歧大多不是立场问题而是信息不对称问题，"
         "先补数据再决策，比说服对方有效得多。",
}

FEEDBACKS = {
    1: {
        "score": 88,
        "text": "技术功底扎实，对 FastAPI 异步、LangGraph 编排、RAG 检索链路理解到位，"
                "能讲清取舍依据与量化结果，项目数据可信、可追问。不足是 K8s 集群级运维经验偏浅。"
                "综合 88 分，建议进入下一轮。",
    },
    2: {
        "score": 90,
        "text": "跨团队协作案例真实具体，能在资源不足下推动交付，冲突处理方式成熟且有结果验证。"
                "STAR 要素完整，反思深度好。90 分，建议进入 HR 面。",
    },
    3: {
        "score": 92,
        "text": "求职动机清晰，对业务理解和长期规划有具体思考，薪资预期在范围内，稳定性好，"
                "文化价值观与岗位匹配。92 分，建议录用。",
    },
}

sse_events = []
stop_flag = {"stop": False}


def listen_sse(token, user_id):
    """后台线程持续订阅 SSE，记录收到的事件"""
    try:
        with httpx.stream("GET", f"{BASE}/sse/notifications",
                          headers={"Authorization": f"Bearer {token}"},
                          timeout=600.0) as resp:
            buf = ""
            for line in resp.iter_lines():
                if stop_flag["stop"]:
                    return
                if line.startswith("data:"):
                    try:
                        sse_events.append(json.loads(line[5:].strip()))
                    except Exception:
                        sse_events.append({"raw": line})
                buf = line
    except Exception as exc:  # noqa: BLE001
        sse_events.append({"error": str(exc)})


def wait_for_status(c, cid, targets, timeout=600, label=""):
    """轮询工作流状态直到进入 targets 之一"""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        r = c.get(f"/candidates/{cid}/workflow")
        if r.status_code == 200:
            run = r.json()
            cur = (run.get("status"), run.get("current_step"), run.get("progress"))
            if cur != last:
                print(f"    [{label}] status={cur[0]} step={cur[1]} progress={cur[2]}")
                last = cur
            if run.get("status") in targets:
                return run
            if run.get("status") == "failed":
                print(f"    !! 工作流失败: {json.dumps(run.get('results'), ensure_ascii=False)[:400]}")
                return run
        time.sleep(3)
    print(f"    !! 超时({timeout}s)，最后状态={last}")
    return None


def wait_for_interview(c, cid, timeout=900, label=""):
    """等待工作流推进到面试挂起点（或终局）。

    submit_response/complete 接口把 resume 放进 BackgroundTasks 后立即返回，
    客户端马上查询读到的仍是提交前的快照，因此这里必须等待 interrupt 类型
    真正从 await_questionnaire 切走，而不是看到 waiting_human 就返回。
    """
    deadline = time.time() + timeout
    last = None
    seen_running = False
    while time.time() < deadline:
        r = c.get(f"/candidates/{cid}/workflow")
        if r.status_code != 200:
            time.sleep(3)
            continue
        run = r.json()
        intr = (run.get("results") or {}).get("interrupt") or {}
        cur = (run.get("status"), run.get("current_step"), run.get("progress"), intr.get("type"))
        if cur != last:
            print(f"    [{label}] status={cur[0]} step={cur[1]} progress={cur[2]} interrupt={cur[3]}")
            last = cur
        if run.get("status") == "running":
            seen_running = True
        if run.get("status") == "failed":
            print(f"    !! 工作流失败: {json.dumps(run.get('results'), ensure_ascii=False)[:400]}")
            return run
        if run.get("status") == "completed":
            return run
        if run.get("status") == "waiting_human" and intr.get("type") == "await_interview":
            return run
        # 提交前的老快照：await_questionnaire 仍在挂起且尚未真正开始驱动
        if (not seen_running and run.get("status") == "waiting_human"
                and intr.get("type") == "await_questionnaire"):
            time.sleep(3)
            continue
        time.sleep(3)
    print(f"    !! 超时({timeout}s)，最后状态={last}")
    return None


def wait_for_next(c, cid, prev_round, timeout=900, label=""):
    """等待工作流的面试挂起点轮次推进（prev_round -> prev_round+1）或到达终局。

    complete 接口把 resume 放进 BackgroundTasks 后立即返回，
    立刻查询读到的仍是上一轮的 waiting_human 快照，
    必须等 interrupt.round 真正变化才能确认推进成功。
    """
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        r = c.get(f"/candidates/{cid}/workflow")
        if r.status_code != 200:
            time.sleep(3)
            continue
        run = r.json()
        intr = (run.get("results") or {}).get("interrupt") or {}
        cur = (run.get("status"), run.get("current_step"), run.get("progress"), intr.get("round"))
        if cur != last:
            print(f"    [{label}] status={cur[0]} step={cur[1]} progress={cur[2]} interrupt_round={cur[3]}")
            last = cur
        if run.get("status") == "failed":
            print(f"    !! 工作流失败: {json.dumps(run.get('results'), ensure_ascii=False)[:400]}")
            return run
        if run.get("status") == "completed":
            return run
        if run.get("status") == "waiting_human" and intr.get("type") == "await_interview" \
                and intr.get("round") != prev_round:
            return run
        time.sleep(3)
    print(f"    !! 超时({timeout}s)，最后状态={last}")
    return None


def build_answer(question: str) -> str:
    """按题目内容匹配差异化深度答案。

    AI 问卷题目在同一份简历下是稳定的，这里为每类题目准备了独立的高质量答案，
    刻意避免多题复用同一套话术（模板化会被评分模型直接压分）。
    """
    q = question or ""
    # 按题干关键词归类；匹配不到时用通用工程深挖答案
    if ("数据层" in q or ("SQLAlchemy" in q and "高并发" in q)):
        return ANSWERS["4"]
    if ("向量" in q or "检索" in q or "RAG" in q or "Chroma" in q or "Milvus" in q):
        return ANSWERS["2"]
    if ("延迟" in q or "P95" in q or "P99" in q or "性能" in q or "优化" in q):
        return ANSWERS["3"]
    if ("分歧" in q or "协作" in q or "跨部门" in q or "冲突" in q):
        return ANSWERS["5"]
    if ("工作流" in q or "LangGraph" in q or "节点" in q or "异步网关" in q or "FastAPI" in q):
        return ANSWERS["1"]
    return ANSWERS["1"]


def main():
    with httpx.Client(base_url=BASE, timeout=600.0) as c:
        c.post("/auth/register", json={
            "username": USERNAME, "email": f"{USERNAME}@example.com",
            "password": "Smoke@12345", "full_name": "工作流验证", "role": "hr",
        })
        r = c.post("/auth/login", data={"username": USERNAME, "password": "Smoke@12345"})
        token = r.json()["access_token"]
        c.headers.update({"Authorization": f"Bearer {token}"})

        me = c.get("/auth/users/me").json()
        threading.Thread(target=listen_sse, args=(token, me["id"]), daemon=True).start()
        time.sleep(2)

        # 1. 建候选人
        r = c.post("/candidates/", json={
            "name": "李workflow", "email": f"li_{STAMP}@example.com",
            "phone": "13900000000", "source": "内推", "position": "高级后端工程师",
        })
        cid = r.json()["id"]
        print(f"候选人 id={cid}")

        # 2. 上传简历 -> 自动触发全链路工作流
        resume = (
            "李workflow\n高级后端工程师，7年经验。\n"
            "技能：Python、FastAPI、LangGraph、LangChain、SQLAlchemy、Redis、PostgreSQL、"
            "Chroma、Milvus、Docker、Kubernetes、Prometheus。\n"
            "项目一：某科技公司 LLM 招聘平台后端负责人，服务日均200万次API调用，"
            "用 FastAPI 异步网关 + LangGraph 编排多节点工作流，接入 RAG 内部知识库问答，"
            "P95 延迟从 800ms 优化至 180ms。\n"
            "项目二：向量检索服务重构，引入 Redis 缓存与 Milvus 分片，P99 从 1.2s 降至 320ms。\n"
            "教育：某985高校 计算机科学与技术 硕士。\n"
            "开源与技术分享：在公司主导 12 场技术分享，开源维护两个 Python 工具库。\n"
            "协作经历：跨部门与产品、设计、运维团队推动项目交付，处理过多次技术方案分歧。\n"
        )
        print("\n[1] 上传简历（后台自动触发工作流）")
        r = c.post(f"/candidates/{cid}/upload-resume",
                   files={"file": (f"resume_{STAMP}.txt", resume.encode("utf-8"), "text/plain")})
        print(f"    上传 {r.status_code}")

        # 3. 等待问卷挂起
        print("\n[2] 等待挂起点1：AI 问卷")
        run = wait_for_status(c, cid, {"waiting_human", "completed"}, timeout=600, label="阶段")
        if not run or run.get("status") not in ("waiting_human", "completed"):
            return report_failure(run)

        if run.get("status") == "waiting_human":
            intr = (run.get("results") or {}).get("interrupt") or {}
            print(f"    挂起类型={intr.get('type')} 问卷id={intr.get('questionnaire_id')} 题数={intr.get('question_count')}")

            # 4. 查看真实生成的题目并提交答案
            qid = intr.get("questionnaire_id")
            qr = c.get(f"/questionnaires/{qid}")
            qs = qr.json().get("questions", []) if qr.status_code == 200 else []
            print(f"    AI 生成题目数={len(qs)}")
            for q in qs[:5]:
                print(f"      - {str(q.get('question'))[:70]}")

            print("\n[3] 提交问卷答案（后台自动恢复工作流）")
            resp_payload = {
                str(i + 1): build_answer(str(q.get("question", "")))
                for i, q in enumerate(qs)
            }
            r = c.post(f"/questionnaires/{qid}/responses",
                       json={"candidate_id": cid, "responses": resp_payload})
            print(f"    提交 {r.status_code}")

            # 5. 等待第 1 轮面试挂起。
            # 注意：submit_response 把恢复放进 BackgroundTasks，接口立即返回，
            # 此时读到的仍是旧的 waiting_human/await_questionnaire 快照，
            # 必须显式等待 interrupt 类型切换，否则会误判为"没恢复"。
            print("\n[4] 等待挂起点2：第1轮面试自动排期")
            run = wait_for_interview(c, cid, timeout=900, label="问卷恢复后")
            if not run:
                return report_failure(run)

        # 6. 逐轮录入面试结果，直到终局
        rounds_done = 0
        while run and run.get("status") == "waiting_human":
            intr = (run.get("results") or {}).get("interrupt") or {}
            wtype = intr.get("type")
            if wtype != "await_interview":
                print(f"    意外挂起类型 {wtype}，停止")
                break
            rno = intr.get("round")
            print(f"\n[面试轮次 {rno}] 自动排期完成，录入结果")
            ivs = c.get("/interviews/", params={"candidate_id": cid}).json()
            pending = [i for i in ivs if i.get("round") == rno and i.get("status") != "completed"]
            if not pending:
                print("    !! 未找到待录入的面试记录")
                break
            iv = pending[0]
            print(f"    面试id={iv['id']} 题目数={len(iv.get('questions') or [])}")
            for q in (iv.get("questions") or [])[:3]:
                print(f"      - {str(q.get('question'))[:70]}")
            fb = FEEDBACKS.get(rno, {"score": 85, "text": "表现良好，建议进入下一轮。"})
            r = c.post(f"/interviews/{iv['id']}/complete",
                       params={"score": fb["score"], "feedback": fb["text"]})
            print(f"    录入 {r.status_code}  分数={fb['score']}")
            run = wait_for_next(c, cid, rno, timeout=900, label=f"R{rno}录入后")
            rounds_done += 1
            if rounds_done > 4:
                break

        # 7. 终局
        print("\n" + "=" * 64)
        final = c.get(f"/candidates/{cid}/workflow")
        if final.status_code == 200:
            fr = final.json()
            res = fr.get("results") or {}
            print(f"最终状态: {fr.get('status')} / step={fr.get('current_step')} / progress={fr.get('progress')}")
            if fr.get("status") == "completed":
                print(f"  招聘决策 : {res.get('final_decision')}")
                print(f"  综合评分 : {res.get('overall_score')}")
                print(f"  技能匹配 : {res.get('skill_match_score')}")
                print(f"  经验匹配 : {res.get('experience_match_score')}")
                print(f"  教育匹配 : {res.get('education_match_score')}")
                print(f"  文化契合 : {res.get('culture_match_score')}")
                print(f"  问卷得分 : {res.get('questionnaire_score')}")
                print(f"  面试评分 : {json.dumps(res.get('interview_scores'), ensure_ascii=False)[:300]}")
                analysis = res.get("analysis") or {}
                print(f"  推荐建议 : {json.dumps(analysis.get('final_recommendation'), ensure_ascii=False)[:300]}")
            else:
                print(f"  结果体: {json.dumps(res, ensure_ascii=False)[:400]}")
        else:
            print(f"查询最终状态失败 {final.status_code}")

        cd = c.get(f"/candidates/{cid}").json()
        print(f"\n候选人状态: {cd.get('status')}")

        ev = c.get("/evaluations/", params={"candidate_id": cid}).json()
        print(f"AI 评估记录 {len(ev)} 条:")
        for e in ev:
            print(f"  - {e['dimension']}: {e['score']}  {str(e.get('comment'))[:60]}")

        qs = c.get("/questionnaires/").json()
        mine = [q for q in qs if str(cid) in str(q.get("name", ""))]
        print(f"AI 问卷: {[q['name'] for q in mine]}")

        ivs = c.get("/interviews/", params={"candidate_id": cid}).json()
        print(f"面试记录 {len(ivs)} 条: " + ", ".join(f"R{i['round']}={i['status']}/{i.get('score')}" for i in ivs))

        tp = c.get("/talent-pool/").json()
        print(f"人才库入库: {[t for t in tp if t['candidate_id'] == cid]}")

        # SSE 事件汇总
        print(f"\nSSE 收到事件 {len(sse_events)} 条:")
        kinds = {}
        for e in sse_events:
            k = e.get("type", "?")
            kinds[k] = kinds.get(k, 0) + 1
        print(f"  事件类型分布: {kinds}")
        prog = [e for e in sse_events if e.get("type") == "workflow_progress"]
        if prog:
            steps = [(e.get("data") or {}).get("current_step") or e.get("current_step") for e in prog]
            print(f"  工作流节点推送序列: {steps}")

        stop_flag["stop"] = True
        c.delete(f"/candidates/{cid}")
        return 0


def report_failure(run):
    print("\n!! 工作流未能推进到挂起状态")
    if run:
        print(json.dumps(run, ensure_ascii=False)[:800])
    return 1


if __name__ == "__main__":
    sys.exit(main())