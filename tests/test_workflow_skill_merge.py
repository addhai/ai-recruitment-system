# -*- coding: utf-8 -*-
"""技能分类合并的守门测试。

## 为什么要动这里

`parse_resume` 已经解析出技能清单了，`extract_skills` 又把这份解析结果
读一遍、再让模型把同一份技能分类一次——不是两次提取，是同一件事做两遍。
实测每次任务固定多花一次调用（¥0.0061、约 4 秒、682 输出 tokens）。

## 为什么不是「让 parse_resume 一起吐出来」

中间试过那一版，**更贵**：实测 `parse_resume` 输出 tokens 1284 → 3618、
均成本 ¥0.01054 → ¥0.02935（+178%），每任务净亏 ¥0.0127。
要模型多吐两千多个 token，而多吐的内容是同一份技能清单的第二份拷贝。

所以现在的做法是**本地派生**：`skills_technical` 直接从扁平 `skills` 来。

## 要钉住的四件事

1. `extract_skills` **不再调 LLM**（否则省下的那次调用又花回去了）；
2. `parse_resume` 的提示词**不许**再要求分类视图——那是花钱买重复内容；
3. `skills_technical` 要落到 `parsed_resume` 里——技能匹配节点读的就是它；
4. 老数据（合并前解析的简历）重跑时，`skills_technical` 缺失要回退到扁平
   `skills`，不能变成"没有技能"。
"""
import asyncio

import pytest

from src.workflow import recruitment_graph as rg


class TestExtractSkillsNoLongerCallsLLM:
    def test_extract_skills_makes_no_llm_call(self, monkeypatch):
        """合并的收益就是省一次调用——这里必须真的不调"""
        calls = []

        async def fake_llm_json(prompt, variables, default, call_site="unknown", **kw):
            calls.append(call_site)
            return default

        monkeypatch.setattr(rg, "_llm_json", fake_llm_json)
        state = {
            "candidate_id": 1,
            "parsed_resume": {
                "basic_info": {}, "skills": ["Python", "FastAPI"],
                "skills_technical": ["Python", "FastAPI"],
                "experience": [], "education": [],
            },
        }
        out = asyncio.run(rg.extract_skills(state))

        assert calls == [], f"extract_skills 不该再调 LLM，实际调了 {calls}"
        assert out["parsed_resume"]["skills_technical"] == ["Python", "FastAPI"]

    def test_falls_back_to_flat_skills_for_legacy_data(self, monkeypatch):
        """老数据没有 skills_technical 时回退到扁平 skills，不能变成没有技能"""
        monkeypatch.setattr(rg.db, "save_resume_parsed", lambda *a, **k: None)
        state = {
            "candidate_id": 1,
            "parsed_resume": {
                "basic_info": {}, "skills": ["Java", "Oracle"],
                "experience": [], "education": [],
            },
        }
        out = asyncio.run(rg.extract_skills(state))
        assert out["parsed_resume"]["skills_technical"] == ["Java", "Oracle"]

    def test_derives_skills_technical_from_flat_skills(self, monkeypatch):
        """没有分类字段时，skills_technical 必须由扁平 skills 派生出来"""
        monkeypatch.setattr(rg.db, "save_resume_parsed", lambda *a, **k: None)
        state = {
            "candidate_id": 1,
            "parsed_resume": {
                "basic_info": {}, "skills": ["Go", "Kubernetes"],
                "experience": [], "education": [],
            },
        }
        out = asyncio.run(rg.extract_skills(state))
        assert out["parsed_resume"]["skills_technical"] == ["Go", "Kubernetes"]
        # 扁平清单本身也要保住（技能匹配节点的兜底路径依赖它）
        assert out["parsed_resume"]["skills"] == ["Go", "Kubernetes"]


class TestParseResumeStaysLean:
    def test_parse_resume_does_not_ask_for_categorized_views(self, monkeypatch):
        """提示词不许再要分类视图。

        实测那一版会让 parse_resume 的输出 tokens 从 1284 涨到 3618、
        均成本从 ¥0.01054 涨到 ¥0.02935（+178%），
        比它省下的那次调用贵三倍——是净亏的改动。
        这条断言就是防止有人"顺手加回来"。
        """
        seen = {}

        async def fake_llm_json(prompt, variables, default, call_site="unknown", **kw):
            seen["prompt"] = prompt
            seen["call_site"] = call_site
            return {
                "basic_info": {"name": "候选人"},
                "skills": ["Python", "FastAPI"],
                "experience": [], "education": [],
            }

        monkeypatch.setattr(rg, "_llm_json", fake_llm_json)
        monkeypatch.setattr(rg.db, "get_resume_parsed", lambda cid: None)
        monkeypatch.setattr(rg.db, "save_resume_parsed", lambda *a, **k: None)

        state = {"candidate_id": 1, "candidate_name": "候选人",
                 "resume_text": "5 年 Python 经验", "workflow_progress": 0}
        asyncio.run(rg.parse_resume(state))

        assert seen["call_site"] == "parse_resume"
        for banned in ("skills_technical", "skills_soft", "domain_knowledge"):
            assert banned not in seen["prompt"], \
                f"提示词里不该再要求 {banned}（花钱买重复内容）"
        assert "skills" in seen["prompt"], "扁平 skills 仍然必须要求"

    def test_parse_resume_saves_flat_skills(self, monkeypatch):
        """解析结果落库时技能取扁平清单，技能匹配节点能直接读到"""

        async def fake_llm_json(prompt, variables, default, call_site="unknown", **kw):
            return {
                "basic_info": {"name": "候选人"},
                "skills": ["Python", "FastAPI"],
                "experience": [], "education": [],
            }

        monkeypatch.setattr(rg, "_llm_json", fake_llm_json)
        monkeypatch.setattr(rg.db, "get_resume_parsed", lambda cid: None)
        saved = {}
        monkeypatch.setattr(rg.db, "save_resume_parsed",
                            lambda cid, data, **kw: saved.update({"data": data, **kw}))

        state = {"candidate_id": 1, "candidate_name": "候选人",
                 "resume_text": "5 年 Python 经验", "workflow_progress": 0}
        out = asyncio.run(rg.parse_resume(state))

        assert out["parsed_resume"]["skills"] == ["Python", "FastAPI"]
        assert saved["skills"] == ["Python", "FastAPI"]
