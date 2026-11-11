# -*- coding: utf-8 -*-
"""任务质量评测：一次跑批同时量「工具调用准确率 / 流程步骤数与成本 / 推理质量」。

## 为什么一个脚本出三类指标

这三件事如果各跑各的，成本会翻倍，而且**前后对比必须用同一批任务才可比**——
同一次运行里调用了多少次、花了多少钱、结论质量如何，本来就是同一次运行的三个侧面。
拆开测就会出现"这次测质量的那批任务恰好简单"的偏差。

## 测什么

### 一、工具调用准确率

`invoke_json` 原本只保证"返回的是可解析的 JSON"，不保证字段齐全、类型正确。
模型返回 `{"score": null}` 时这次调用在埋点里是 `status=ok / degraded=false`，
节点层兜底成 70 分 —— **埋点完全看不出它不合规**。

所以本脚本从 `llm_call_logs` 读三个字段（由 `src/services/llm_contracts.py` 的契约校验写入）：

- **首次结构合规率** = 首次返回就合规的调用数 / 首次调用数
- **修正成功率** = 修正后合规的调用数 / 发起修正的调用数
- **不合规字段分布** = 哪些字段最常出错（schema_errors 聚合）

### 二、流程步骤数与成本

- 单任务调用数（首次调用与修正调用**分开统计**，不混成一个数）
- 单任务成本、单任务端到端延迟 p50/p90

### 三、推理质量（四口径）

1. **决策一致性**（规则，零成本）：模型决策 vs 规则档位。
   规则档位复刻 `generate_hiring_decision` 的分档逻辑（≥80 推荐录用 / ≥65 待定 / 其余不推荐）。
2. **理由-分数自洽**（规则，零成本）：理由里的强情绪词与分数引用是否与真实分数矛盾。
3. **幻觉率**（LLM 裁判）：理由与风险项是否引入简历/JD 之外的事实。
4. **综合推理质量分**（LLM 裁判，0~100）。

规则口径与裁判口径**同时产出**，互为交叉校验：规则便宜但只能查形式矛盾，
裁判能查语义但每次都要花钱。

## 用法

```powershell
# 基线（关修正调用，量"不修的时候到底有多少不合规"）
python tests/eval/task_quality_eval.py --n 15 --no-repair --no-judge `
      --out tests/eval/task_quality_before.json

# 改进后（开修正调用，开裁判）
python tests/eval/task_quality_eval.py --n 15 --judge `
      --out tests/eval/task_quality_after.json
```

## 隔离

用独立的临时 SQLite，不碰用户的 recruitment.db。
预算墙读的是库里的 llm_call_logs，隔离库里是空的，所以**本实验不受预算墙拦截**
（这是有意的，否则任务会被中途掐断，测出来的就不是正常路径）。花费单独统计。
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import re
import sys
import tempfile
import time
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

# ---- 隔离环境：必须在 import src 之前设置 ----
# 复用注入实验的模块级环境设置（临时库 + 关预算墙 + 关重排），
# 保证两套实验用同一套 fixture、同一套依赖开关。
_spec = importlib.util.spec_from_file_location(
    "tse", REPO_ROOT / "tests" / "eval" / "task_success_eval.py")
tse = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tse)

# 兜底：万一注入实验模块又设了占位 key，这里清掉。
# src.config.settings 是 import 时创建的模块级单例，事后改 os.environ 对它无效。
if os.environ.get("LLM_API_KEY") == "eval-key":
    os.environ.pop("LLM_API_KEY", None)

from src.models.database import (Candidate, Evaluation, LLMCallLog,  # noqa: E402
                                 SessionLocal, WorkflowRun, init_db)
from src.services import llm_invoke  # noqa: E402
from src.workflow import runner  # noqa: E402


# =====================================================================
# 一、简历固定装置
# =====================================================================
# 刻意用**多份**简历而不是同一份：同一份简历跑 N 次，
# 每个维度的分数几乎不变，所有质量指标都会退化成常数，
# 测不出任何差异，也测不出改动有没有把结论搞坏。
RESUMES = [
    {
        "name": "强匹配-后端",
        "level": "strong",
        "interview_scores": [88, 85, 90],
        "text": "计算机科学与技术专业本科，6 年 Python 后端开发经验，"
                "主导过招聘系统与 AI 评估链路建设，"
                "熟练使用 Python/FastAPI/PostgreSQL/Redis，"
                "负责过日均百万级请求的服务设计与性能优化，接口 P99 从 800ms 降到 120ms；"
                "带领 5 人小组完成跨部门交付，有明确的量化成果。",
    },
    {
        "name": "中匹配-缺技术栈",
        "level": "medium",
        "interview_scores": [72, 71, 75],
        "text": "软件工程本科，4 年 Python 开发经验，主要做 Django 后端与定时任务，"
                "使用过 MySQL 与消息队列，没有大规模系统设计经验；"
                "参与过两个内部系统的交付，负责接口与报表模块。",
    },
    {
        "name": "弱匹配-跨语言",
        "level": "weak",
        "interview_scores": [52, 48, 55],
        "text": "计算机本科，3 年 Java 后端开发经验，做过电商订单系统，"
                "最近一年自学 Python，写过一些脚本与数据处理工具，"
                "没有 FastAPI 与 PostgreSQL 的项目经历。",
    },
    {
        "name": "应届生-项目型",
        "level": "medium",
        "interview_scores": [72, 70, 71],
        "text": "计算机科学与技术专业本科应届生。校园项目用 FastAPI 做过一个课程作业问答后端，"
                "用 PostgreSQL 存数据，接了向量检索做资料召回；"
                "实习两个月，做过数据清洗脚本与接口联调，没有正式工作经验。",
    },
    {
        "name": "资深-跨行业",
        "level": "medium",
        "interview_scores": [70, 71, 72],
        "text": "金融工程本科，8 年金融行业后端开发经验，主要用 Java 与 Oracle 做核心交易系统，"
                "熟悉高可用与稳定性建设，近两年接触 Python 用于风控脚本，"
                "没有互联网业务与微服务经验。",
    },
    {
        "name": "运营转岗",
        "level": "weak",
        "interview_scores": [40, 45, 42],
        "text": "大专学历，5 年互联网运营经验，负责用户增长与活动策划，"
                "自学过 Python 做数据分析和自动化报表，能读写脚本，"
                "没有软件工程背景，没有参与过产品研发。",
    },
    {
        "name": "技术扎实-管理多",
        "level": "strong",
        "interview_scores": [90, 88, 92],
        "text": "计算机硕士，7 年后端开发经验，其中 3 年带团队，"
                "技术栈为 Python/FastAPI/MySQL/Redis/Kubernetes，"
                "负责过招聘与人事系统重构，把人工简历筛选流程从 3 天缩短到 4 小时，"
                "有跨部门协作与带人交付的记录。",
    },
    {
        "name": "极简简历",
        "level": "weak",
        "interview_scores": [35, 38, 40],
        "text": "熟悉 Python 和数据库，希望找后端开发工作。",
    },
]


def _new_candidate(jd_id: int, resume: dict) -> int:
    """每个场景建一个**新**候选人。

    刻意不删旧的：`parse_resume` 是幂等的（读 resumes 表判断是否已结构化解析），
    而 SQLite 会复用被删掉的行 id。清理不彻底时下一个场景会拿到同一个 id、
    读到上一轮残留的解析结果，于是**跳过 LLM 调用**——测出来的调用数会偏小。
    """
    with SessionLocal() as db:
        c = Candidate(
            name=f"评测候选人-{uuid.uuid4().hex[:6]}",
            email=f"quality_{uuid.uuid4().hex[:8]}@t.com",
            resume_text=resume["text"],
            position="后端工程师",
            job_description_id=jd_id,
        )
        db.add(c)
        db.commit()
        db.refresh(c)
        return c.id


# =====================================================================
# 二、推理质量：规则口径（零成本）
# =====================================================================
_DECISION_LEVEL = {"不推荐": 0, "待定": 1, "推荐录用": 2}
# 决策字符串可能带括号补充说明（如"不推荐（问卷测评不达标）"），
# 比对档位前先剥掉，只留档位本身。
_DECISION_SUFFIX = re.compile(r"[（(].*?[）)]")


def normalize_decision(text: str) -> str:
    """把决策字符串归一到档位：剥掉括号补充，保留档位词。"""
    s = _DECISION_SUFFIX.sub("", str(text or "")).strip()
    for level in ("推荐录用", "不推荐", "待人工复核", "待定"):
        if level in s:
            return level
    return s


def rule_decision(capped: int) -> str:
    """复刻 `generate_hiring_decision` 的分档逻辑。

    代码里是 `capped = min(overall, 79) if hard_veto else overall` 之后再分档，
    而落库的 `overall_score` 就是 capped，所以直接对它分档即可得到同一个档位。
    """
    if capped >= 80:
        return "推荐录用"
    if capped >= 65:
        return "待定"
    return "不推荐"


def check_decision_consistency(model_decision: str, capped: int) -> dict:
    """决策一致性：模型决策 vs 规则档位。

    刻意分两级：
    - exact：完全一致；
    - aligned：档位差 ≤ 1（提示词明确允许"有硬伤可下调一档、有亮点可上调一档"），
      所以差一档**不算错**，但差两档（高档判不推荐 / 低档判录用）是硬伤。

    注意：`overall_score` 为空说明**决策节点没跑到**（流程在问卷或面试处就结束了），
    这时不能拿"规则档位"去比一个不存在的决策——直接标成"未参与比对"。
    """
    expected = rule_decision(capped)
    model = normalize_decision(model_decision)
    if not model_decision or capped <= 0:
        return {"expected": expected, "model": model or None,
                "exact": None, "aligned": None,
                "note": "决策节点未执行（流程在此之前结束），不参与比对"}
    if model == "待人工复核":
        # 设计内的锁档（文化契合落在待复核区间），不算不一致
        return {"expected": expected, "model": model,
                "exact": None, "aligned": True, "note": "人工复核锁档，不参与比对"}
    lvl_model = _DECISION_LEVEL.get(model)
    lvl_rule = _DECISION_LEVEL.get(expected)
    if lvl_model is None:
        return {"expected": expected, "model": model,
                "exact": None, "aligned": None, "note": f"无法识别的决策：{model_decision}"}
    delta = abs(lvl_model - lvl_rule)
    return {"expected": expected, "model": model,
            "exact": model == expected,
            "aligned": delta <= 1,
            "level_delta": delta}


# 强情绪词：只在**分句**内与维度一起出现时才判矛盾，
# 否则"技能匹配 85 分，经验匹配 40 分明显不足"会被整句误判。
_POSITIVE = ["完全符合", "完全满足", "高度匹配", "高度契合", "非常匹配",
             "远超", "大幅超出", "显著优于", "完全胜任", "充分满足"]
_NEGATIVE = ["完全不符合", "完全不满足", "严重不足", "严重不匹配", "显著不足",
             "远低于", "明显不达标", "毫不匹配", "完全不达标"]

_DIM_KEYWORDS = [("技能", "skill_match_score"), ("经验", "experience_match_score"),
                 ("教育", "education_match_score"), ("文化", "culture_match_score"),
                 ("综合", "overall_score")]

_SCORE_REF = re.compile(r"(技能|经验|教育|文化|综合)[^。；;，,]{0,12}?(\d{1,3})\s*分")
_CLAUSE_SPLIT = re.compile(r"[，,；;。！!？?\n]")


def check_reason_score_consistency(reasons, results: dict) -> dict:
    """理由-分数自洽（规则口径）。

    查两件事：
    1. 理由里引用的分数与真实分数对不上（"技能匹配 82 分"但真实是 75）；
    2. 强情绪词与分数矛盾（"高度匹配"但只有 40 分）。
    """
    issues = []
    scores = {
        "技能": results.get("skill_match_score"),
        "经验": results.get("experience_match_score"),
        "教育": results.get("education_match_score"),
        "文化": results.get("culture_match_score"),
        "综合": results.get("overall_score"),
    }

    for reason in reasons or []:
        text = str(reason)
        for dim, num in _SCORE_REF.findall(text):
            real = scores.get(dim)
            if real is None:
                continue
            if int(num) != int(real):
                issues.append(f"分数引用不符：理由称{dim}{num}分，实际{int(real)}分")

        for clause in _CLAUSE_SPLIT.split(text):
            if not clause.strip():
                continue
            dim = next((d for d, _ in _DIM_KEYWORDS if d in clause), None)
            if dim is None:
                continue
            real = scores.get(dim)
            if real is None:
                continue
            if any(w in clause for w in _POSITIVE) and real < 60:
                issues.append(f"措辞与分数矛盾：{dim}仅{real}分但理由称高度/完全匹配")
            if any(w in clause for w in _NEGATIVE) and real >= 80:
                issues.append(f"措辞与分数矛盾：{dim}{real}分但理由称严重不足")

    return {"consistent": not issues, "issues": issues}


# =====================================================================
# 三、推理质量：LLM 裁判口径
# =====================================================================
JUDGE_PROMPT = """你是招聘评审质量审计员。审计一条招聘结论的推理质量。

【候选人简历原文】
{resume_text}

【岗位要求】
{job_requirements}

【系统给出的各维度评分】
{scores}

【系统给出的最终决策】
{decision}

【决策理由】
{reasons}

【风险项】
{risks}

请审计三件事：

1. **决策与评分是否自洽**：决策档位（推荐录用/待定/不推荐）是否与上面的分数相符。
   若分数明显偏高却判"不推荐"、或分数明显偏低却判"推荐录用"，判为不一致。

2. **理由与分数是否自洽**：理由里的措辞是否与分数矛盾。
   例如说"高度匹配"但该维度只有 40 分、说"完全不满足"但该维度 90 分、
   理由里引用的分数与实际分数对不上。

3. **是否有幻觉**：理由与风险项里是否出现简历原文和岗位要求里**没有**的事实。
   例如简历没写"主导过 5 人团队"却写进了理由；岗位没要求"英语流利"却写成了风险。
   只输出确实在原文找不到依据的事实——措辞不同不等于幻觉。

硬性要求：
1. 只依据简历原文与岗位要求判断，不得推测。
2. 判断要保守：拿不准的不算幻觉、不算不一致。
3. 风险项里写"该岗位未设限"之类的免责说明不算幻觉。

输出JSON：{{"decision_consistent": true, "reason_score_consistent": true, "has_hallucination": false, "hallucination_items": [], "quality_score": 80, "issues": []}}"""


_DIM_LABEL = ("技能匹配", "经验匹配", "教育匹配", "文化契合", "问卷测评")


def _dimension_reasons(evals) -> list[dict]:
    """从 Evaluation 行里取出每个维度给分时写的理由。

    这些理由是**模型原文**：节点把 LLM 返回的 reason / analysis / reasons
    拼进了 comment。它们是推理质量的**大样本**——每个任务 4~5 条，
    而最终决策只有跑到决策节点的那批任务才有（本次跑批里大部分候选人
    被问卷或面试关卡淘汰了），只靠决策样本会薄得没法下结论。
    """
    out = []
    for e in evals:
        if e.dimension not in _DIM_LABEL:
            continue
        comment = (e.comment or "").strip()
        if not comment:
            continue
        # 技能匹配的 comment 形如「匹配: a,b；缺失: c,d；{理由}」，
        # 真正的理由在最后一段
        reason = comment.split("；")[-1].strip() if "匹配:" in comment else comment
        if not reason:
            continue
        out.append({"dimension": e.dimension, "score": e.score,
                    "reason": reason[:300]})
    return out


async def judge_task(resume_text: str, jd_requirements: str, results: dict) -> dict:
    """用一次 LLM 调用审计**最终决策**的推理质量。

    裁判调用发生在任务结束之后、任何 trace 上下文之外，
    所以它的埋点 thread_id 为空，不会被算进"单任务调用数"里。
    它的花费单独统计（judge_cost）。

    决策节点没跑到时返回空 dict —— 没有决策就没有可判的决策。
    """
    if not results.get("final_decision") or not results.get("overall_score"):
        return {}

    scores_text = "\n".join(
        f"- {label}：{results.get(key)}"
        for label, key in (("技能匹配", "skill_match_score"),
                           ("经验匹配", "experience_match_score"),
                           ("教育匹配", "education_match_score"),
                           ("文化契合", "culture_match_score"),
                           ("问卷测评", "questionnaire_score"),
                           ("综合分", "overall_score"))
        if results.get(key) is not None
    ) or "（无维度评分）"

    # 面试分也必须给裁判看：决策理由里会引用"面试均分 87.7"，
    # 裁判看不到就会把它当成幻觉——那是裁判自己没拿到证据，不是模型编的。
    rounds = results.get("interview_scores") or []
    if rounds:
        detail = "、".join(f"第{r.get('round')}轮{r.get('score')}" for r in rounds)
        avg = round(sum(r.get("score", 0) for r in rounds) / len(rounds), 1)
        scores_text += f"\n- 面试分：{detail}（均分 {avg}）"

    rec = results.get("analysis", {}).get("final_recommendation") or {}
    reasons = rec.get("reasons") or []
    risks = rec.get("risks") or []

    out = await llm_invoke.invoke_json(
        JUDGE_PROMPT,
        {
            "resume_text": resume_text,
            "job_requirements": jd_requirements,
            "scores": scores_text,
            "decision": results.get("final_decision") or "",
            "reasons": "\n".join(f"- {r}" for r in reasons) or "（无）",
            "risks": "\n".join(f"- {r}" for r in risks) or "（无）",
        },
        {"decision_consistent": True, "reason_score_consistent": True,
         "has_hallucination": False, "hallucination_items": [],
         "quality_score": 0, "issues": []},
        call_site="reasoning_judge",
    )
    return out if isinstance(out, dict) else {}


DIMENSION_JUDGE_PROMPT = """你是招聘评审质量审计员。审计招聘系统给出的**各维度评分理由**。

【候选人简历原文】
{resume_text}

【岗位要求】
{job_requirements}

【问卷题目与候选人作答】
{questionnaire_context}

【各维度评分与理由】
{dimensions}

请审计两件事：

1. **理由与分数是否自洽**：理由里的措辞是否与该维度的分数矛盾。
   例如理由说"高度匹配"但分数只有 40 分、说"完全不满足"但分数 90 分、
   理由里引用的分数与实际分数对不上。指出具体是哪个维度不自洽。

2. **是否有幻觉**：理由里是否出现简历原文和岗位要求中**没有**的事实。
   例如简历没写"主导过 5 人团队"却写进了理由；岗位没要求"英语流利"却写成了风险。
   只输出确实在原文找不到依据的事实——措辞不同不等于幻觉。

硬性要求：
1. 只依据简历原文与岗位要求判断，不得推测。
2. 判断要保守：拿不准的不算幻觉、不算不自洽。
3. 技能匹配维度的理由里会列出"匹配/缺失的技能清单"，那是**引用岗位要求**，
   不算幻觉；但如果理由里出现清单之外的、简历没写的技能，算幻觉。
4. **问卷测评维度的理由引用上面的问卷题目与作答是正常的**，不算幻觉；
   只有当它引用了作答里没有的额外事实才算。

输出JSON：{{"reason_score_consistent": true, "has_hallucination": false, "hallucination_items": [], "inconsistent_dimensions": [], "quality_score": 80, "issues": []}}"""


async def judge_dimensions(resume_text: str, jd_requirements: str,
                            dimensions: list[dict],
                            questionnaire_context: str = "") -> dict:
    """用一次 LLM 调用审计一个任务里**全部维度**的理由。

    为什么要单独一路：最终决策只有跑到决策节点的任务才有（本次跑批里
    大部分候选人被问卷/面试关卡淘汰了），样本太薄；而**维度理由**
    每个任务都有 4~5 条，是推理质量可靠的大样本。

    questionnaire_context 必须一起给：问卷测评维度的理由本来就会引用
    问卷题目与作答，裁判看不到就会把它们当成幻觉——那是裁判缺证据，
    不是模型编的。（上一版就是这么把幻觉率测成 1.0 的。）
    """
    if not dimensions:
        return {}
    text = "\n".join(
        f"- {d['dimension']}（{d['score']} 分）：{d['reason']}"
        for d in dimensions)
    out = await llm_invoke.invoke_json(
        DIMENSION_JUDGE_PROMPT,
        {
            "resume_text": resume_text,
            "job_requirements": jd_requirements,
            "questionnaire_context": questionnaire_context or "（本次任务没有问卷）",
            "dimensions": text[:6000],
        },
        {"reason_score_consistent": True, "has_hallucination": False,
         "hallucination_items": [], "inconsistent_dimensions": [],
         "quality_score": 0, "issues": []},
        call_site="dimension_judge",
    )
    return out if isinstance(out, dict) else {}


# =====================================================================
# 四、把工作流一路驱动到终局（合成问卷答案 + 合成面试分）
# =====================================================================
_ANSWER_DEPTH = {
    "strong": "切题作答：直接针对问题作答，给出具体方案、关键设计与取舍依据，"
              "并结合简历里的一个具体项目作为案例；控制在 250 字以内，"
              "不要罗列与问题无关的内容。",
    "medium": "切题作答：给出可行方案与关键点，结合简历里的一个具体项目作为案例，"
              "控制在 200 字以内，不展开实现细节。",
    "weak": "浅层作答：只说做法的大意，不给具体设计与细节，控制在 80 字以内。",
}

_SYNTHETIC_ANSWER_PROMPT = """你扮演一位正在参加技术面试的候选人，回答下面几道技术问题。

【你的真实背景（这是你唯一能依据的事实）】
{resume_text}

【回答深度要求】
{depth}

【要回答的问题】
{questions}

硬性要求：
1. 必须以第一人称、从上述背景出发作答，**不得编造背景里没有的经历或数据**；
2. 逐题作答，题号与问题一一对应；
3. 只输出 JSON。

输出JSON：{{"answers": [{{"qid": "题号", "text": "答案正文"}}]}}"""


async def _synthetic_answers(questionnaire_id: int, profile: dict):
    """用一次 LLM 调用模拟候选人作答，键用真实题号。

    为什么不用模板答案：AI 生成的问卷题是**真正的技术设计题**
    （"设计一个基于 FastAPI 的评估接口"、"设计 PostgreSQL 核心表结构"），
    模板化的通用回答答非所问，问卷评分会正确地压到 60 分以下——
    于是流程在问卷关卡就把人淘汰了，决策节点永远跑不到，
    推理质量的三条口径全都没法测。

    让 LLM 按"简历真实背景 + 指定深度"作答，才是一个可信的合成被试。
    这次调用发生在**任务之外**（不在 trace 上下文里），所以：
    - 它的埋点 thread_id 为空，不会被算进"单任务调用数"；
    - 它的花费单独统计（synthetic_cost）。

    返回 (答案 dict, 题目列表)。
    """
    from src.models.database import Questionnaire
    with SessionLocal() as db:
        q = db.query(Questionnaire).filter(
            Questionnaire.id == questionnaire_id).first()
        questions = (q.questions if q else []) or []
    if not questions:
        return {}, []

    qids = []
    qtexts = []
    for i, item in enumerate(questions):
        qid = str(item.get("id", i + 1))
        qids.append(qid)
        qtexts.append(f"{qid}. {str(item.get('question', ''))[:400]}")

    out = await llm_invoke.invoke_json(
        _SYNTHETIC_ANSWER_PROMPT,
        {
            "resume_text": profile["text"],
            "depth": _ANSWER_DEPTH.get(profile.get("level", "medium"),
                                      _ANSWER_DEPTH["medium"]),
            "questions": "\n".join(qtexts),
        },
        {"answers": []},
        call_site="synthetic_candidate_answer",
    )

    answers = {}
    for item in (out.get("answers") or []) if isinstance(out, dict) else []:
        if isinstance(item, dict) and item.get("qid") is not None:
            answers[str(item["qid"])] = str(item.get("text") or "")
    # 合成失败时留空：空答案会被问卷评分压到低分，
    # 这比用通用模板冒充回答更诚实——它如实反映了"没答"。
    return answers, [{"id": qid, "question": str(it.get("question", ""))[:120]}
                     for qid, it in zip(qids, questions)]


def _synthetic_interview(round_no: int, profile: dict) -> dict:
    """合成面试录入结果。

    面试分是**人工录入**的（不经过 LLM），所以这里直接给固定分，
    分数随简历档次变化，保证最终综合分有方差。
    """
    scores = profile.get("interview_scores") or [70]
    return {
        "round": round_no,
        "score": scores[(round_no - 1) % len(scores)],
        "feedback": "评测用合成反馈：按简历匹配度给定的固定分",
        "notes": "评测用合成数据",
    }


async def _current_interrupt(cid: int):
    """取当前挂起的 interrupt 内容；不在挂起态就返回 None。

    复刻 runner.resume_workflow 里的读法（它也这么读），
    但只读不改：这里需要先知道"在等什么"才能造出对应的 payload。
    """
    graph = await runner.get_graph()
    with SessionLocal() as db:
        run = runner._get_active_run(db, cid)
        thread_id = runner._thread_id_of(run) if run else None
    if not thread_id:
        return None
    snapshot = await graph.aget_state({"configurable": {"thread_id": thread_id}})
    for task in snapshot.tasks:
        for intr in getattr(task, "interrupts", None) or []:
            if intr.value:
                return intr.value
    return None


async def _drive_to_completion(cid: int, profile: dict, max_resumes: int = 8):
    """启动工作流并把每一处人工挂起都恢复掉，直到终局。

    **为什么要恢复**：工作流在问卷与面试处 interrupt 等真人，
    不恢复的话任务停在 `waiting_human`，**决策节点根本跑不到**——
    决策一致性、理由自洽、幻觉率这三个推理质量口径就全都没法测，
    流程成本也只测到一半（没有面试出题与决策这两段）。
    """
    out = await runner.start_workflow(cid, position_requirements="后端工程师")
    resumes = 0
    human_inputs = []
    while out.get("status") == "waiting_human" and resumes < max_resumes:
        intr = await _current_interrupt(cid)
        if not intr:
            break
        itype = intr.get("type")
        if itype == "await_questionnaire":
            answers, questions = await _synthetic_answers(
                intr.get("questionnaire_id"), profile)
            payload = {"responses": answers}
            human_inputs.append({"type": itype, "questions": questions,
                                 "responses": {k: v[:200] for k, v in answers.items()}})
        elif itype == "await_interview":
            payload = _synthetic_interview(int(intr.get("round") or 1), profile)
            human_inputs.append({"type": itype, "round": payload["round"],
                                 "score": payload["score"]})
        else:
            break
        out = await runner.resume_workflow(cid, itype, payload)
        resumes += 1
    return out, resumes, human_inputs


async def _run_one(index: int, resume: dict, jd_id: int, jd_requirements: str,
                   with_judge: bool) -> dict:
    cid = _new_candidate(jd_id, resume)
    t0 = time.time()
    try:
        out, resumes, human_inputs = await asyncio.wait_for(
            _drive_to_completion(cid, resume), timeout=600.0)
        status = out.get("status")
        error = out.get("error")
    except asyncio.TimeoutError:
        status, error, resumes, human_inputs = "timeout", "任务超过 600s 未结束", 0, []
    except Exception as e:
        status, error, resumes, human_inputs = (
            "exception", f"{type(e).__name__}: {e}", 0, [])
    elapsed_ms = int((time.time() - t0) * 1000)

    with SessionLocal() as db:
        run = (db.query(WorkflowRun)
               .filter(WorkflowRun.candidate_id == cid)
               .order_by(WorkflowRun.id.desc()).first())
        run_status = run.status if run else None
        results = (run.results if run and run.results else {}) or {}
        evals = db.query(Evaluation).filter(Evaluation.candidate_id == cid).all()
        dims = sorted({e.dimension for e in evals})
        thread_id = results.get("thread_id")

        rows = []
        if thread_id:
            rows = db.query(LLMCallLog).filter(
                LLMCallLog.thread_id == thread_id).order_by(LLMCallLog.id.asc()).all()

    first_calls = [r for r in rows if not r.is_repair]
    repair_calls = [r for r in rows if r.is_repair]

    # ---- 工具调用准确率 ----
    checked = [r for r in first_calls if r.schema_valid is not None]
    valid_first = sum(1 for r in checked if r.schema_valid)
    repaired = [r for r in first_calls if r.schema_fixed is not None]
    repaired_ok = sum(1 for r in repaired if r.schema_fixed)
    err_fields = {}
    for r in checked:
        if r.schema_valid:
            continue
        for item in str(r.schema_errors or "").split(";"):
            item = item.strip()
            if not item:
                continue
            key = item.split(":")[0].strip() or item
            err_fields[key] = err_fields.get(key, 0) + 1

    # ---- 流程步骤数与成本 ----
    cost = sum((r.cost or 0.0) for r in rows)
    latency_sum = sum((r.latency_ms or 0) for r in rows)

    # ---- 推理质量（规则口径）----
    rec = results.get("analysis", {}).get("final_recommendation") or {}
    reasons = rec.get("reasons") or []
    decision_check = check_decision_consistency(
        results.get("final_decision") or "", int(results.get("overall_score") or 0))
    reason_check = check_reason_score_consistency(reasons, results)

    judged = {}
    if with_judge:
        judged = await judge_task(resume["text"], jd_requirements, results)

    dim_reasons = _dimension_reasons(evals)
    dim_judged = {}
    if with_judge:
        # 问卷题目与作答必须一并给裁判：问卷测评维度的理由本来就会引用它们
        q_ctx = ""
        for hi in human_inputs:
            if hi.get("type") != "await_questionnaire":
                continue
            lines = []
            resp = hi.get("responses") or {}
            for q in hi.get("questions") or []:
                lines.append(f"Q{q.get('id')}: {q.get('question')}")
                lines.append(f"A: {resp.get(str(q.get('id')), '')}")
            q_ctx = "\n".join(lines)[:3000]
        dim_judged = await judge_dimensions(resume["text"], jd_requirements,
                                            dim_reasons, q_ctx)

    return {
        "index": index,
        "profile": resume["name"],
        "level": resume.get("level"),
        "status": status,
        "run_status": run_status,
        "error": (error or "")[:300] if error else None,
        "elapsed_ms": elapsed_ms,
        "human_resumes": resumes,
        "human_inputs": human_inputs,
        "evaluated_dims": dims,
        "evaluated_count": len(dims),
        "degraded_dims_reported": list(results.get("degraded_dimensions") or []),
        "thread_id": thread_id,
        # 成本与步骤数
        "calls_total": len(rows),
        "calls_first": len(first_calls),
        "calls_repair": len(repair_calls),
        "cost": round(cost, 5),
        "llm_latency_sum_ms": latency_sum,
        # 工具调用准确率
        "schema_checked": len(checked),
        "schema_valid_first": valid_first,
        "schema_repaired": len(repaired),
        "schema_repaired_ok": repaired_ok,
        "schema_error_fields": err_fields,
        # 推理质量（规则口径）
        "overall_score": results.get("overall_score"),
        "final_decision": results.get("final_decision"),
        "dim_scores": {
            "skill": results.get("skill_match_score"),
            "experience": results.get("experience_match_score"),
            "education": results.get("education_match_score"),
            "culture": results.get("culture_match_score"),
            "questionnaire": results.get("questionnaire_score"),
            "overall": results.get("overall_score"),
        },
        "reasons": [str(r)[:300] for r in reasons],
        "decision_check": decision_check,
        "reason_check": reason_check,
        # 推理质量（维度理由口径：大样本）
        "dim_reasons": dim_reasons,
        "dim_judge": {
            "reason_score_consistent": dim_judged.get("reason_score_consistent"),
            "has_hallucination": dim_judged.get("has_hallucination"),
            "hallucination_items": dim_judged.get("hallucination_items") or [],
            "inconsistent_dimensions": dim_judged.get("inconsistent_dimensions") or [],
            "quality_score": dim_judged.get("quality_score"),
            "issues": dim_judged.get("issues") or [],
        } if dim_judged else None,
        # 推理质量（裁判口径）
        "judge": {
            "decision_consistent": judged.get("decision_consistent"),
            "reason_score_consistent": judged.get("reason_score_consistent"),
            "has_hallucination": judged.get("has_hallucination"),
            "hallucination_items": judged.get("hallucination_items") or [],
            "quality_score": judged.get("quality_score"),
            "issues": judged.get("issues") or [],
        } if judged else None,
    }


def _pct(vals: list[float], p: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    return s[min(len(s) - 1, int(len(s) * p))]


def _summarize(records: list[dict], with_judge: bool) -> dict:
    n = len(records)
    if not n:
        return {"n": 0}

    checked = sum(r["schema_checked"] for r in records)
    valid_first = sum(r["schema_valid_first"] for r in records)
    repaired = sum(r["schema_repaired"] for r in records)
    repaired_ok = sum(r["schema_repaired_ok"] for r in records)
    err_fields = {}
    for r in records:
        for k, v in (r.get("schema_error_fields") or {}).items():
            err_fields[k] = err_fields.get(k, 0) + v

    dec_exact = [r["decision_check"]["exact"] for r in records
                 if r["decision_check"].get("exact") is not None]
    dec_aligned = [r["decision_check"]["aligned"] for r in records
                   if r["decision_check"].get("aligned") is not None]
    reason_ok = [r["reason_check"]["consistent"] for r in records]

    lat = [r["elapsed_ms"] for r in records]
    cost = sum(r["cost"] for r in records)

    out = {
        "n": n,
        # 工具调用准确率
        "schema_checked_calls": checked,
        "schema_valid_first": valid_first,
        "schema_first_valid_rate": round(valid_first / checked, 4) if checked else None,
        "schema_repaired_calls": repaired,
        "schema_repaired_ok": repaired_ok,
        "schema_repair_success_rate": round(repaired_ok / repaired, 4) if repaired else None,
        "schema_error_fields": err_fields,
        # 流程步骤数与成本
        "calls_total": sum(r["calls_total"] for r in records),
        "calls_first": sum(r["calls_first"] for r in records),
        "calls_repair": sum(r["calls_repair"] for r in records),
        "avg_calls_first": round(sum(r["calls_first"] for r in records) / n, 2),
        "avg_calls_total": round(sum(r["calls_total"] for r in records) / n, 2),
        "total_cost": round(cost, 4),
        "avg_cost": round(cost / n, 5),
        "latency_p50_ms": int(_pct(lat, .5)),
        "latency_p90_ms": int(_pct(lat, .9)),
        "latency_max_ms": int(max(lat)) if lat else 0,
        # 推理质量（规则口径）
        "decision_exact_n": len(dec_exact),
        "decision_exact_rate": round(sum(1 for x in dec_exact if x) / len(dec_exact), 4)
        if dec_exact else None,
        "decision_aligned_rate": round(sum(1 for x in dec_aligned if x) / len(dec_aligned), 4)
        if dec_aligned else None,
        "reason_consistent_rate": round(sum(1 for x in reason_ok if x) / len(reason_ok), 4)
        if reason_ok else None,
        "reason_issue_samples": [i for r in records for i in r["reason_check"]["issues"]][:8],
    }

    if with_judge:
        judged = [r["judge"] for r in records if r.get("judge")]
        if judged:
            q = [j["quality_score"] for j in judged if isinstance(j.get("quality_score"), (int, float))]
            out["judge_n"] = len(judged)
            out["judge_decision_consistent_rate"] = round(
                sum(1 for j in judged if j.get("decision_consistent") is True) / len(judged), 4)
            out["judge_reason_consistent_rate"] = round(
                sum(1 for j in judged if j.get("reason_score_consistent") is True) / len(judged), 4)
            out["judge_hallucination_rate"] = round(
                sum(1 for j in judged if j.get("has_hallucination") is True) / len(judged), 4)
            out["judge_quality_avg"] = round(sum(q) / len(q), 2) if q else None
            out["judge_quality_min"] = min(q) if q else None
            out["judge_issue_samples"] = [i for j in judged for i in (j.get("issues") or [])][:8]
            out["judge_hallucination_samples"] = [
                item for j in judged for item in (j.get("hallucination_items") or [])][:8]

        # 维度理由口径（样本量最大的推理质量口径）
        dim_judged = [r["dim_judge"] for r in records if r.get("dim_judge")]
        dim_samples = sum(len(r.get("dim_reasons") or []) for r in records)
        if dim_judged:
            dq = [j["quality_score"] for j in dim_judged
                  if isinstance(j.get("quality_score"), (int, float))]
            out["dim_judge_tasks"] = len(dim_judged)
            out["dim_samples"] = dim_samples
            out["dim_reason_consistent_rate"] = round(
                sum(1 for j in dim_judged if j.get("reason_score_consistent") is True)
                / len(dim_judged), 4)
            out["dim_hallucination_rate"] = round(
                sum(1 for j in dim_judged if j.get("has_hallucination") is True)
                / len(dim_judged), 4)
            out["dim_quality_avg"] = round(sum(dq) / len(dq), 2) if dq else None
            out["dim_quality_min"] = min(dq) if dq else None
            out["dim_inconsistent_samples"] = [
                d for j in dim_judged for d in (j.get("inconsistent_dimensions") or [])][:8]
            out["dim_hallucination_samples"] = [
                item for j in dim_judged for item in (j.get("hallucination_items") or [])][:8]
            out["dim_issue_samples"] = [i for j in dim_judged for i in (j.get("issues") or [])][:8]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=15)
    ap.add_argument("--out", default="task_quality.json")
    ap.add_argument("--repair", dest="repair", action="store_true", default=True,
                    help="开启结构不合规时的修正调用（默认开）")
    ap.add_argument("--no-repair", dest="repair", action="store_false")
    ap.add_argument("--judge", dest="judge", action="store_true", default=False,
                    help="开启 LLM 裁判（要花钱，默认关）")
    ap.add_argument("--no-judge", dest="judge", action="store_false")
    args = ap.parse_args()

    # 修正调用开关：src.config.settings 是 import 时创建的模块级单例，
    # 事后改 os.environ 对它**无效**（os.environ 的优先级只在实例化时生效）。
    # 所以这里直接给单例赋值，而不是改环境变量。
    from src.config import settings
    settings.LLM_SCHEMA_REPAIR_ENABLED = bool(args.repair)

    init_db()
    runner._CHECKPOINT_DB = str(Path(tse._TMPDIR) / "cp_quality.db")

    jd_id = tse._ensure_jd()
    with SessionLocal() as db:
        from src.models.database import JobDescription
        jd = db.query(JobDescription).filter(JobDescription.id == jd_id).first()
        parsed = (jd.parsed_data if jd else {}) or {}
    # 必须给裁判**与打分模型看到的同一份** JD：_jd_brief 就是 str(jd)[:3000]，
    # 裁判少看字段就会把"岗位里有 Redis"当成"岗位没要求 Redis"，
    # 把正常理由误判成幻觉。（上一版只给了 4 个键，幻觉率因此虚高。）
    jd_requirements = str(parsed)[:3000]

    records: list[dict] = []

    async def run_all():
        try:
            for i in range(args.n):
                resume = RESUMES[i % len(RESUMES)]
                r = await _run_one(i, resume, jd_id, jd_requirements, args.judge)
                records.append(r)
                print(f"  [{i + 1}/{args.n}] {resume['name']:<14} "
                      f"终态={str(r['run_status']):<12} 耗时={r['elapsed_ms'] / 1000:6.1f}s "
                      f"调用={r['calls_first']}+{r['calls_repair']}修 "
                      f"合规={r['schema_valid_first']}/{r['schema_checked']} "
                      f"决策={str(r['final_decision']):<8} ¥{r['cost']:.4f}", flush=True)
        finally:
            await runner.close_graph()

    asyncio.run(run_all())

    # 裁判调用的花费单独算：它们的 thread_id 为空，不在任务统计里
    with SessionLocal() as db:
        judge_cost = sum((r.cost or 0.0) for r in db.query(LLMCallLog).filter(
            LLMCallLog.call_site.in_(["reasoning_judge", "dimension_judge"])).all())

    overall = _summarize(records, args.judge)
    report = {
        "meta": {
            "n": len(records),
            "mode": "real_llm",
            "repair_enabled": args.repair,
            "judge_enabled": args.judge,
            "note": "隔离临时库，不受预算墙拦截；花费单独统计",
        },
        "overall": overall,
        "records": records,
    }

    out_path = Path(args.out)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    o = overall
    print("\n" + "=" * 72)
    print(f"任务质量汇总  n={o['n']}  repair={args.repair}  judge={args.judge}")
    print("=" * 72)
    print(f"  首次结构合规率   {o['schema_valid_first']}/{o['schema_checked_calls']} = "
          f"{o['schema_first_valid_rate']}")
    if o["schema_repaired_calls"]:
        print(f"  修正成功率       {o['schema_repaired_ok']}/{o['schema_repaired_calls']} = "
              f"{o['schema_repair_success_rate']}")
    if o.get("schema_error_fields"):
        print(f"  不合规字段       {o['schema_error_fields']}")
    print(f"  单任务调用数     首次 {o['avg_calls_first']}  含修正 {o['avg_calls_total']}")
    print(f"  延迟             p50={o['latency_p50_ms']}ms  p90={o['latency_p90_ms']}ms")
    print(f"  花费             总 ¥{o['total_cost']:.4f}  单任务均 ¥{o['avg_cost']:.5f}"
          + (f"  裁判 ¥{judge_cost:.4f}" if args.judge else ""))
    print(f"  决策一致性       精确 {o['decision_exact_rate']}  档位对齐 {o['decision_aligned_rate']}")
    print(f"  理由-分数自洽    {o['reason_consistent_rate']}")
    if args.judge:
        print(f"  决策裁判       样本 {o.get('judge_n', 0)}  "
              f"决策一致 {o.get('judge_decision_consistent_rate')}  "
              f"理由自洽 {o.get('judge_reason_consistent_rate')}  "
              f"幻觉 {o.get('judge_hallucination_rate')}  "
              f"质量均分 {o.get('judge_quality_avg')}")
        print(f"  维度裁判       任务 {o.get('dim_judge_tasks', 0)}  "
              f"维度样本 {o.get('dim_samples', 0)}  "
              f"理由自洽 {o.get('dim_reason_consistent_rate')}  "
              f"幻觉 {o.get('dim_hallucination_rate')}  "
              f"质量均分 {o.get('dim_quality_avg')}")
    print(f"  报告             {out_path.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
