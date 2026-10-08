"""把没写价值观的 JD 标为「不限制」。

6 份真实 JD 中有 3 份原文没写"我们看重什么"（AI Agent 开发工程师 /
电商方向 / 智能内控方向）。这些岗位不该拿一份没写价值观的 JD 去要求
候选人体现企业文化——标为不限制后该维度不评分、不卡人。
学历/年限同样：有值的保留，没值的标不限制。
"""
import sys
import time

import httpx

BASE = "http://127.0.0.1:8000"

# 原文未写价值观的岗位（按标题前缀匹配）
NO_CULTURE = [
    "AI Agent 开发工程师",
    "AI 应用开发工程师（电商方向）",
    "AI Agent 应用开发工程师（智能内控方向）",
]


def main():
    stamp = int(time.time())
    u = f"unconst_{stamp}"
    with httpx.Client(base_url=BASE, timeout=600.0) as c:
        c.post("/auth/register", json={
            "username": u, "email": f"{u}@example.com",
            "password": "Smoke@12345", "full_name": "标记不限制", "role": "hr"})
        tok = c.post("/auth/login", data={"username": u, "password": "Smoke@12345"}).json()["access_token"]
        c.headers.update({"Authorization": f"Bearer {tok}"})

        jds = c.get("/job_descriptions/", params={"status": "active"}).json()
        print(f"当前启用 JD：{len(jds)} 份\n")

        changed = 0
        for jd in jds:
            profile = jd.get("parsed_data") or {}
            values = profile.get("culture_values") or []
            basic = profile.get("basic") or {}

            updates = {}
            if any(title in jd["title"] for title in NO_CULTURE):
                if values:
                    updates["culture_values"] = ["不限制"]
            # 学历/年限：原文没写的标为不限制，不再退回"通用本科"锚点
            if not (basic.get("education_required") or "").strip():
                updates.setdefault("basic", {})["education_required"] = "不限制"
            if not (basic.get("experience_years_required") or "").strip():
                updates.setdefault("basic", {})["experience_years_required"] = "不限制"

            if "basic" in updates:
                basic = {**basic, **updates.pop("basic")}
                updates["basic"] = basic

            if not updates:
                print(f"  · {jd['title']}：无需调整")
                continue

            # 走归一化保存（服务端会剔除无原文依据的技能项）
            new_profile = {**profile, **updates}
            r = c.put(f"/job_descriptions/{jd['id']}", json={"parsed_data": new_profile})
            if r.status_code != 200:
                print(f"  ! {jd['title']} 保存失败：{r.text[:120]}")
                continue
            changed += 1
            notes = []
            if updates.get("culture_values"):
                notes.append("价值观→不限制")
            b = updates.get("basic", {})
            if b.get("education_required") == "不限制":
                notes.append("学历→不限制")
            if b.get("experience_years_required") == "不限制":
                notes.append("年限→不限制")
            print(f"  ✓ {jd['title']}：{'、'.join(notes)}")

        print(f"\n共调整 {changed} 份 JD")
    return 0


if __name__ == "__main__":
    sys.exit(main())