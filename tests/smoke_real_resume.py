"""真实简历 PDF 验证脚本（人工验证用）。

用一份真实简历走完整链路，重点检验：
1. PDF 文本提取（该 PDF 中文间带空格的排版，考察清洗能力）
2. PII 脱敏（简历含手机号/邮箱，验证返回结果是否过滤）
3. 安全护栏 InputGuard
4. OCR 兜底路径是否被误触发
5. AI 结构化解析 + 全链路工作流

用法: python tests/smoke_real_resume.py <pdf路径>
"""
import json
import sys
import time

import httpx

BASE = "http://127.0.0.1:8000"
PDF = sys.argv[1] if len(sys.argv) > 1 else None
if not PDF:
    print("用法: python tests/smoke_real_resume.py <pdf路径>")
    sys.exit(2)

STAMP = str(int(time.time()))
USERNAME = f"rr_{STAMP}"

sse_events = []


def listen_sse(token):
    try:
        with httpx.stream("GET", f"{BASE}/sse/notifications",
                          headers={"Authorization": f"Bearer {token}"},
                          timeout=1800.0) as resp:
            for line in resp.iter_lines():
                if line.startswith("data:"):
                    try:
                        sse_events.append(json.loads(line[5:].strip()))
                    except Exception:
                        pass
    except Exception:
        pass


def wait_for(c, cid, predicate, timeout=900, label=""):
    """轮询工作流直到 predicate(run) 为真"""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        r = c.get(f"/candidates/{cid}/workflow")
        if r.status_code == 200:
            run = r.json()
            intr = (run.get("results") or {}).get("interrupt") or {}
            cur = (run.get("status"), run.get("current_step"), run.get("progress"), intr.get("type"))
            if cur != last:
                print(f"    [{label}] {cur[0]} / {cur[1]} / {cur[2]}" + (f" / {cur[3]}" if cur[3] else ""))
                last = cur
            if run.get("status") == "failed":
                print(f"    !! 失败: {json.dumps(run.get('results'), ensure_ascii=False)[:300]}")
                return run
            if predicate(run, intr):
                return run
        time.sleep(3)
    print(f"    !! 超时，最后状态 {last}")
    return None


def main():
    import threading

    with httpx.Client(base_url=BASE, timeout=1800.0) as c:
        c.post("/auth/register", json={
            "username": USERNAME, "email": f"{USERNAME}@example.com",
            "password": "Smoke@12345", "full_name": "真实简历验证", "role": "hr",
        })
        token = c.post("/auth/login",
                       data={"username": USERNAME, "password": "Smoke@12345"}).json()["access_token"]
        c.headers.update({"Authorization": f"Bearer {token}"})
        threading.Thread(target=listen_sse, args=(token,), daemon=True).start()
        time.sleep(2)

        # 1. 建候选人（岗位对齐简历求职意向）
        print("[1] 创建候选人")
        r = c.post("/candidates/", json={
            "name": "王林海", "email": "candidate@example.com", "phone": "17307179854",
            "source": "简历投递", "position": "信息科技岗（应用系统开发/数据分析建模/运维/信息安全）",
        })
        print(f"    {r.status_code} id={r.json().get('id')}")
        cid = r.json()["id"]

        # 2. 上传真实 PDF（后台自动触发全链路工作流）
        print("\n[2] 上传真实简历 PDF（自动触发工作流）")
        with open(PDF, "rb") as f:
            files = {"file": ("简历（广发银行信息科技岗）.pdf", f, "application/pdf")}
            r = c.post(f"/candidates/{cid}/upload-resume", files=files)
        print(f"    HTTP {r.status_code}")
        if r.status_code != 200:
            print(f"    失败: {r.text[:500]}")
            return 1
        data = r.json()
        print(f"    提取方式 source_type = {data.get('source_type')}  （txt=直接解析, image=走了 OCR 兜底）")
        print(f"    是否触发 OCR used_ocr = {data.get('used_ocr')}")
        print(f"    提取文本长度 = {data.get('text_length')}")
        print(f"    parsed_data 字段 = {list((data.get('parsed_data') or {}).keys())}")

        # 3. PII 脱敏检查
        print("\n[3] PII 脱敏检查（简历含手机号/邮箱）")
        blob = json.dumps(data, ensure_ascii=False)
        print(f"    返回体是否含手机号 17307179854 : {'是（未脱敏）' if '17307179854' in blob else '否（已脱敏）'}")
        print(f"    返回体是否含邮箱 @163.com      : {'是（未脱敏）' if '163.com' in blob else '否（已脱敏）'}")
        print("    解析文本片段（前 200 字）:")
        print(f"      {str(data.get('parsed_text'))[:200]}")

        # 4. 等待工作流挂起
        print("\n[4] 等待工作流推进")
        run = wait_for(
            c, cid,
            lambda run, intr: run.get("status") in ("waiting_human", "completed"),
            timeout=900, label="解析阶段")
        if not run:
            return 1

        if run.get("status") == "waiting_human":
            intr = (run.get("results") or {}).get("interrupt") or {}
            qid = intr.get("questionnaire_id")
            print(f"\n[5] AI 生成问卷 id={qid} 题数={intr.get('question_count')}")
            qs = c.get(f"/questionnaires/{qid}").json().get("questions", [])
            for i, q in enumerate(qs, 1):
                print(f"    Q{i}. {str(q.get('question'))[:90]}")

        # 5. 结构化解析结果与 AI 评估
        print("\n[6] AI 结构化解析结果")
        cd = c.get(f"/candidates/{cid}").json()
        print(f"    候选人状态 = {cd.get('status')}")

        print("\n[7] AI 多维度评估")
        for e in c.get("/evaluations/", params={"candidate_id": cid}).json():
            print(f"    {e['dimension']}: {e['score']}")
            print(f"       {str(e.get('comment'))[:150]}")

        print("\n[8] SSE 事件")
        kinds = {}
        for e in sse_events:
            kinds[e.get("type", "?")] = kinds.get(e.get("type", "?"), 0) + 1
        print(f"    共 {len(sse_events)} 条: {kinds}")

        # 清理
        c.delete(f"/candidates/{cid}")
        print("\n已清理测试候选人")
    return 0


if __name__ == "__main__":
    sys.exit(main())