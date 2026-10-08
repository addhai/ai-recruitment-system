"""JD 驱动的人岗匹配端到端验证（人工验收用）。

验证改造后的真实链路：
  录入 JD → AI 解析（带 evidence）→ 启用 → 绑定候选人 → 上传简历
  → 自动触发工作流 → 四维评分基于结构化 JD

用法: python tests/smoke_jd_flow.py <pdf路径>
"""
import json
import sys
import time

import httpx

BASE = "http://127.0.0.1:8000"
PDF = sys.argv[1] if len(sys.argv) > 1 else None
if not PDF:
    print("用法: python tests/smoke_jd_flow.py <pdf路径>")
    sys.exit(2)

JD_TEXT = """招聘岗位：金融科技岗（信息科技部）

所属部门：信息科技部
招聘人数：若干

岗位职责：
1. 负责行内信息系统的应用系统开发与演进，保障系统稳定运行
2. 负责数据采集、清洗与建模，支撑数据分析与报表需求
3. 负责系统运维，包括发布、监控、故障排查与应急处置
4. 负责信息安全管理，包括密钥管理、接口鉴权与权限边界设计

任职要求：
1. 精通 Python 编程，熟悉 FastAPI、Flask 等 Web 框架
2. 熟练掌握 PostgreSQL、MySQL，具备数据库设计与性能优化经验
3. 熟悉 Redis 缓存、RabbitMQ 消息队列，具备高并发场景实践经验
4. 熟悉 Linux、Docker、Kubernetes，具备生产环境部署与运维能力
5. 理解信息安全规范，具备密钥管理与配置分离的实践意识

加分项：
1. 有金融行业信息系统开发经验
2. 有 LLM 应用、RAG 检索增强生成相关项目经验
3. 有开源项目贡献或技术分享经历

我们看重：
1. 严谨负责，对系统稳定性有敬畏心
2. 主动学习，持续跟进新技术并在工作中落地
3. 良好沟通与协作能力，能推动跨部门问题解决
"""

STAMP = str(int(time.time()))
USERNAME = f"jdflow_{STAMP}"


def main():
    with httpx.Client(base_url=BASE, timeout=900.0) as c:
        # 登录
        c.post("/auth/register", json={
            "username": USERNAME, "email": f"{USERNAME}@example.com",
            "password": "Smoke@12345", "full_name": "JD验证", "role": "hr"})
        token = c.post("/auth/login",
                       data={"username": USERNAME, "password": "Smoke@12345"}).json()["access_token"]
        c.headers.update({"Authorization": f"Bearer {token}"})

        # 1. 录入 JD
        print("=" * 70)
        print("步骤 1：录入岗位 JD")
        r = c.post("/job_descriptions/", data={"title": "金融科技岗", "department": "信息科技部",
                                               "raw_text": JD_TEXT})
        print(f"  HTTP {r.status_code}")
        if r.status_code != 200:
            print(f"  失败：{r.text[:300]}")
            return 1
        jd = r.json()
        print(f"  JD id={jd['id']} 状态={jd['status']} 解析={jd['parse_status']}")

        # 2. AI 解析
        print("\n步骤 2：AI 解析 JD（真实调用大模型）")
        r = c.post(f"/job_descriptions/{jd['id']}/parse")
        print(f"  HTTP {r.status_code}")
        if r.status_code != 200:
            print(f"  解析失败：{r.text[:300]}")
            return 1
        jd = r.json()
        pd_ = jd["parsed_data"]
        print(f"  解析状态：{jd['parse_status']}")
        print(f"  硬技能 {len(pd_['required_skills'])} 项（每项带原文依据）：")
        for s in pd_["required_skills"]:
            print(f"    - {s['skill']:<18} ← 「{s['evidence'][:40]}」")
        print(f"  加分技能：{[s['skill'] for s in pd_['preferred_skills']]}")
        print(f"  岗位价值观：{pd_['culture_values']}")
        print(f"  学历要求：{pd_['basic']['education_required']}")
        print(f"  年限要求：{pd_['basic']['experience_years_required']}")
        print(f"  岗位职责 {len(pd_['responsibilities'])} 条")

        # 3. 校验未启用时无法启动匹配
        print("\n步骤 3：未启用 JD 时应拒绝启动匹配")
        cid = c.post("/candidates/", json={
            "name": "测试候选人", "email": f"cand_{STAMP}@example.com",
            "position": "金融科技岗", "job_description_id": jd["id"]}).json()["id"]
        r = c.post(f"/candidates/{cid}/run-workflow", json={})
        print(f"  HTTP {r.status_code} → {r.json().get('detail', '')[:60]}")
        assert r.status_code == 400, "未启用的 JD 不应放行"

        # 4. 启用
        print("\n步骤 4：人工核对后启用 JD")
        r = c.post(f"/job_descriptions/{jd['id']}/activate")
        print(f"  HTTP {r.status_code} → status={r.json().get('status')}")
        assert r.json()["status"] == "active"

        # 5. 上传简历 → 自动触发
        print("\n步骤 5：上传简历（应自动触发基于 JD 的工作流）")
        with open(PDF, "rb") as f:
            r = c.post(f"/candidates/{cid}/upload-resume",
                       files={"file": ("resume.pdf", f, "application/pdf")})
        print(f"  上传 HTTP {r.status_code}，提取 {r.json().get('text_length')} 字")

        print("\n步骤 6：等待工作流推进到第一个挂起点")
        deadline = time.time() + 900
        run = None
        while time.time() < deadline:
            r = c.get(f"/candidates/{cid}/workflow")
            if r.status_code == 200:
                run = r.json()
                intr = (run.get("results") or {}).get("interrupt") or {}
                st, step, prog = run.get("status"), run.get("current_step"), run.get("progress")
                print(f"    {st:<14} {str(step):<24} {prog}%")
                if run.get("status") in ("waiting_human", "completed", "failed"):
                    break
            time.sleep(4)

        if not run or run.get("status") != "waiting_human":
            print(f"  !! 未进入问卷挂起：{json.dumps(run, ensure_ascii=False)[:300]}")
            c.delete(f"/candidates/{cid}")
            return 1

        # 6. 关键验证：评分是否真的基于结构化 JD
        print("\n步骤 7：验证评分依据来自结构化 JD（而非岗位名）")
        evs = {e["dimension"]: e for e in c.get("/evaluations/", params={"candidate_id": cid}).json()}
        for dim in ("技能匹配", "经验匹配", "教育匹配", "文化契合"):
            e = evs.get(dim)
            if e:
                print(f"  {dim}: {e['score']} 分")
                print(f"     {str(e.get('comment'))[:170]}")
        if "技能匹配" in evs:
            missing = evs["技能匹配"]["comment"]
            if "缺失" in missing:
                print("  ✓ 技能评语含「缺失」项，说明逐条核对了 JD 硬技能")

        c.delete(f"/candidates/{cid}")
        c.delete(f"/job_descriptions/{jd['id']}")
        print("\n已清理测试数据")
    return 0


if __name__ == "__main__":
    sys.exit(main())