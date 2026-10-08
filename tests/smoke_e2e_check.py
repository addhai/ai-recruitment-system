"""端到端冒烟脚本（人工验证用，不属于 pytest 套件）。

针对已启动的本地后端 127.0.0.1:8000，按**真实路由契约**跑一遍核心业务链路：
注册 -> 登录 -> 候选人 CRUD -> 简历上传 -> 工作流 -> 面试 -> 问卷 -> 评估
-> 人才库 -> 仪表盘 -> 知识库 -> SSE -> 鉴权边界

用法: python tests/smoke_e2e_check.py
"""

import sys
import time

import httpx

BASE = "http://127.0.0.1:8000"
STAMP = str(int(time.time()))
USERNAME = f"smoke_{STAMP}"

passed = []
failed = []


def record(name, ok, detail=""):
    (passed if ok else failed).append((name, detail))
    flag = "PASS" if ok else "FAIL"
    print(f"[{flag}] {name}" + (f" -> {detail}" if detail else ""))


def main():
    with httpx.Client(base_url=BASE, timeout=180.0) as c:
        # ---------- 认证 ----------
        r = c.post("/auth/register", json={
            "username": USERNAME, "email": f"{USERNAME}@example.com",
            "password": "Smoke@12345", "full_name": "冒烟测试员", "role": "hr",
        })
        record("POST /auth/register", r.status_code == 200, f"{r.status_code}")
        if r.status_code != 200:
            print("注册失败，终止")
            return 1

        r = c.post("/auth/login", data={"username": USERNAME, "password": "Smoke@12345"})
        if r.status_code != 200 or "access_token" not in r.text:
            record("POST /auth/login", False, f"{r.status_code} {r.text[:200]}")
            return 1
        record("POST /auth/login", True, f"{r.status_code}")
        token = r.json()["access_token"]
        auth = {"Authorization": f"Bearer {token}"}
        c.headers.update(auth)

        r = c.get("/auth/users/me")
        record("GET /auth/users/me", r.status_code == 200, f"{r.status_code}")

        # ---------- 候选人 CRUD ----------
        r = c.post("/candidates/", json={
            "name": "张三", "email": f"zhangsan_{STAMP}@example.com",
            "phone": "13800000000", "source": "boss直聘", "position": "高级后端工程师",
        })
        ok_create = r.status_code == 200
        record("POST /candidates/", ok_create, f"{r.status_code} {r.text[:180]}")
        if not ok_create:
            return 1
        cid = r.json()["id"]

        r = c.get("/candidates/", params={"limit": 5})
        record("GET /candidates/ (列表)", r.status_code == 200, f"{r.status_code} 共{len(r.json())}条")

        r = c.get("/candidates/", params={"search": "张三"})
        record("GET /candidates/ (搜索过滤)", r.status_code == 200 and len(r.json()) >= 1,
               f"{r.status_code} 命中{len(r.json()) if r.status_code == 200 else 0}条")

        r = c.get(f"/candidates/{cid}")
        record("GET /candidates/{id}", r.status_code == 200, f"{r.status_code}")

        r = c.put(f"/candidates/{cid}", json={"name": "张三丰", "status": "screening"})
        record("PUT /candidates/{id}", r.status_code == 200 and r.json().get("name") == "张三丰",
               f"{r.status_code}")

        # ---------- 简历上传（真实文件解析链路） ----------
        resume_text = (
            "个人简历\n姓名：张三\n"
            "工作经历：某科技公司高级后端工程师 5 年。\n"
            "技术栈：Python、FastAPI、LangGraph、SQLAlchemy、Redis、PostgreSQL、Docker、K8s。\n"
            "负责 LLM 应用平台后端开发，使用检索增强生成构建企业内部知识库问答系统，"
            "有高并发服务治理与数据库性能优化经验。\n"
            "教育背景：某985 高校 计算机科学与技术 硕士。"
        )
        r = c.post(
            f"/candidates/{cid}/upload-resume",
            files={"file": (f"resume_{STAMP}.txt", resume_text.encode("utf-8"), "text/plain")},
        )
        ok_upload = r.status_code == 200
        record("POST /candidates/{id}/upload-resume (简历上传+解析)", ok_upload,
               f"{r.status_code} " + (f"source_type={r.json().get('source_type')} len={r.json().get('text_length')}" if ok_upload else r.text[:200]))
        if ok_upload:
            r = c.get(f"/candidates/{cid}/resume")
            record("GET /candidates/{id}/resume", r.status_code == 200, f"{r.status_code}")

        # ---------- AI 工作流（依赖 LLM 凭据） ----------
        try:
            r = c.post(f"/candidates/{cid}/run-workflow", json={
                "position_requirements": "高级后端工程师，要求 Python/FastAPI、异步编程、数据库设计，有 LLM/RAG 经验优先。",
            })
            wf = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            record("POST /candidates/{id}/run-workflow (LangGraph 全链路)",
                   r.status_code == 200,
                   f"{r.status_code} status={wf.get('status')} current_step={wf.get('current_step')}")
        except Exception as exc:  # noqa: BLE001
            record("POST /candidates/{id}/run-workflow (LangGraph 全链路)", False, f"异常 {exc}")

        r = c.get(f"/candidates/{cid}/workflow")
        record("GET /candidates/{id}/workflow (运行记录)", r.status_code == 200,
               f"{r.status_code} status={r.json().get('status') if r.status_code == 200 else '-'} progress={r.json().get('progress') if r.status_code == 200 else '-'}")

        # ---------- 面试 ----------
        r = c.post("/interviews/", json={
            "candidate_id": cid, "position": "高级后端工程师", "round": 1,
            "scheduled_at": "2026-11-01T10:00:00",
        })
        ok_iv = r.status_code == 200
        record("POST /interviews/", ok_iv, f"{r.status_code} {r.text[:180]}")
        iv_id = r.json()["id"] if ok_iv else None

        r = c.get("/interviews/", params={"candidate_id": cid})
        record("GET /interviews/ (按候选人过滤)", r.status_code == 200, f"{r.status_code} 共{len(r.json()) if r.status_code==200 else 0}条")

        if iv_id:
            r = c.post(f"/interviews/{iv_id}/complete", params={"score": 85, "feedback": "技术功底扎实，编码规范，工程化能力强。"})
            record("POST /interviews/{id}/complete (录入+自动恢复工作流)", r.status_code == 200, f"{r.status_code}")

        # ---------- 问卷 ----------
        r = c.post("/questionnaires/", json={
            "name": f"技术测评问卷_{STAMP}", "type": "technical",
            "questions": [{"question": "如何设计一个高并发秒杀系统？", "answer": "从缓存、限流、削峰、幂等四个角度展开。"}],
        })
        ok_q = r.status_code == 200
        record("POST /questionnaires/", ok_q, f"{r.status_code} {r.text[:180]}")
        qid = r.json()["id"] if ok_q else None

        r = c.get("/questionnaires/")
        record("GET /questionnaires/", r.status_code == 200, f"{r.status_code} 共{len(r.json()) if r.status_code==200 else 0}条")

        if qid:
            r = c.post(f"/questionnaires/{qid}/responses", json={
                "candidate_id": cid, "responses": {"0": "使用 Redis 预扣减、令牌桶限流、消息队列削峰、数据库唯一索引保证幂等。"},
            })
            record("POST /questionnaires/{id}/responses (提交+自动恢复工作流)", r.status_code == 200,
                   f"{r.status_code} {r.text[:180]}")

        # ---------- 评估 ----------
        r = c.post("/evaluations/", json={
            "candidate_id": cid, "dimension": "technical", "score": 88, "comment": "技术面表现优秀",
        })
        ok_ev = r.status_code == 200
        record("POST /evaluations/", ok_ev, f"{r.status_code} {r.text[:180]}")
        if ok_ev:
            eid = r.json()["id"]
            r = c.put(f"/evaluations/{eid}", params={"score": 92})
            record("PUT /evaluations/{id}", r.status_code == 200, f"{r.status_code}")

        r = c.get("/evaluations/", params={"candidate_id": cid})
        record("GET /evaluations/ (按候选人过滤)", r.status_code == 200, f"{r.status_code}")

        r = c.get("/evaluations/stats")
        record("GET /evaluations/stats (工作流评估统计)", r.status_code == 200, f"{r.status_code} {r.text[:150]}")

        r = c.get(f"/evaluations/candidate/{cid}/summary")
        record("GET /evaluations/candidate/{id}/summary", r.status_code == 200, f"{r.status_code} {r.text[:150]}")

        # ---------- 人才库 ----------
        r = c.post("/talent-pool/", json={"candidate_id": cid, "tags": ["高潜", "Python"], "notes": "冒烟入库"})
        ok_tp = r.status_code == 200
        record("POST /talent-pool/", ok_tp, f"{r.status_code} {r.text[:180]}")
        if ok_tp:
            pid = r.json()["id"]
            r = c.post(f"/talent-pool/{pid}/contact", params={"notes": "已电话沟通"})
            record("POST /talent-pool/{id}/contact", r.status_code == 200, f"{r.status_code}")
            r = c.put(f"/talent-pool/{pid}", params={"status": "contacted"})
            record("PUT /talent-pool/{id}", r.status_code == 200, f"{r.status_code}")

        r = c.get("/talent-pool/")
        record("GET /talent-pool/", r.status_code == 200, f"{r.status_code} 共{len(r.json()) if r.status_code==200 else 0}条")

        # ---------- 仪表盘 ----------
        for path in ("/dashboard/stats", "/dashboard/recent-candidates",
                     "/dashboard/interview-stats", "/dashboard/weekly-trend"):
            r = c.get(path)
            record(f"GET {path}", r.status_code == 200, f"{r.status_code} {r.text[:130]}")

        # ---------- 知识库 RAG（依赖 embedding 凭据） ----------
        r = c.post("/knowledge-base/documents", json={
            "title": f"年假制度_{STAMP}",
            "content": "公司实行带薪年休假制度。员工入职满1年享受5天年假，满3年享受10天，满5年享受15天。年假需提前3个工作日在系统申请。",
        })
        record("POST /knowledge-base/documents (向量写入)", r.status_code == 200, f"{r.status_code} {r.text[:160]}")

        r = c.get("/knowledge-base/documents")
        record("GET /knowledge-base/documents", r.status_code == 200, f"{r.status_code} {r.text[:160]}")

        try:
            r = c.post("/knowledge-base/query", json={"query": "入职满3年有多少天年假？"})
            record("POST /knowledge-base/query (RAG 检索问答)", r.status_code == 200,
                   f"{r.status_code} {r.text[:220]}")
        except Exception as exc:  # noqa: BLE001
            record("POST /knowledge-base/query (RAG 检索问答)", False, f"异常 {exc}")

        # ---------- SSE ----------
        try:
            with httpx.stream("GET", f"{BASE}/sse/notifications", headers=auth, timeout=20.0) as resp:
                got = None
                for line in resp.iter_lines():
                    if line and line.startswith("data:"):
                        got = line
                        break
                record("GET /sse/notifications (首帧)", got is not None,
                       f"status={resp.status_code} first={got}")
        except Exception as exc:  # noqa: BLE001
            record("GET /sse/notifications (首帧)", False, f"异常 {exc}")

        # ---------- 鉴权边界 ----------
        with httpx.Client(base_url=BASE, timeout=30.0) as anon:
            r = anon.get("/candidates/")
            record("未授权访问 /candidates/ 被拒 (期望401)", r.status_code == 401, f"实际{r.status_code}")
            r = anon.get("/dashboard/stats")
            record("未授权访问 /dashboard/stats 被拒 (期望401)", r.status_code == 401, f"实际{r.status_code}")
            r = anon.get("/sse/notifications")
            record("未授权访问 /sse 被拒 (期望401)", r.status_code == 401, f"实际{r.status_code}")

        # 无效 token
        with httpx.Client(base_url=BASE, timeout=30.0, headers={"Authorization": "Bearer invalid-token"}) as bad:
            r = bad.get("/candidates/")
            record("无效 token 被拒 (期望401)", r.status_code == 401, f"实际{r.status_code}")

        # ---------- 清理 ----------
        r = c.delete(f"/candidates/{cid}")
        record("DELETE /candidates/{id} (级联清理)", r.status_code == 200, f"{r.status_code} {r.text[:140]}")

    print("\n" + "=" * 64)
    print(f"通过 {len(passed)} / {len(passed) + len(failed)}")
    if failed:
        print("失败项：")
        for name, detail in failed:
            print(f"  - {name}  ({detail})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())