"""岗位 JD 解析模块。

为什么需要：此前人岗匹配用的是 candidate.position 这个岗位名字符串，
等于拿简历自述的职责去匹配简历自己（自我印证）。现在要求先录入 JD，
解析成结构化画像后作为匹配的真实依据。

两条纪律（写进提示词并在校验中强制）：
1. 每个技能必须带 evidence 原文片段——JD 解析错了会静默污染该岗位下
   所有候选人的评分，这是最需要防的失败模式，所以每个结论都要可核对。
2. culture_values 只能来自 JD 原文，不得套用通用价值观模板——此前
   文化契合用的是代码里写死的公司价值观，与岗位无关，是打分最不稳定的来源。
"""
from __future__ import annotations

import os
import tempfile
from typing import Any, Dict, List, Optional, Tuple

from src.services.llm_json import llm_json, extract_llm_error
from src.services.resume_cleaner import parse_resume_with_fallback
from src.services import budget
from src.safety import InputGuard
from src.config import settings

# "不限制"的识别口径与工作流共用同一份，避免两边认的词不一致
UNCONSTRAINED_MARKERS = settings.UNCONSTRAINED_MARKERS

# JD 文本长度上限：超过部分对结构化解析没有额外价值
MAX_JD_LENGTH = 20000

# 技能条目归一化：允许解析出裸字符串，也允许 {"skill","evidence"} 结构
_SKILL_KEYS = ("skill", "name", "keyword")

# JD basic 下的标量字段集合
_EMPTY_BASIC = {
    "department": "", "location": "", "employment_type": "", "headcount": 0,
    "experience_years_required": "", "experience_years_preferred": "",
    "education_required": "", "education_preferred": "",
}


def extract_text_from_file(filename: str, raw_bytes: bytes) -> Tuple[str, str, bool]:
    """从上传的 JD 文件提取纯文本。

    直接复用简历清洗模块：JD 文件格式（txt/pdf/docx/html/图片 OCR）
    与简历完全一致，没有必要重写一套抽取逻辑。
    """
    suffix = os.path.splitext(filename)[1] or ".txt"
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(tmp_fd, "wb") as tmp:
            tmp.write(raw_bytes)
        return parse_resume_with_fallback(tmp_path, filename, raw_bytes)
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


_PARSE_PROMPT = """解析以下招聘岗位 JD，提取结构化岗位画像。

JD 原文：
{raw_text}

请以 JSON 格式输出，结构如下：
{{
  "basic": {{
    "department": "所属部门，未提及填空字符串",
    "location": "工作地点，未提及填空字符串",
    "employment_type": "全职/兼职/实习，未提及填空字符串",
    "headcount": 0,
    "experience_years_required": "明确写出的最低工作年限要求，未提及填空字符串",
    "experience_years_preferred": "加分项里的年限要求，未提及填空字符串",
    "education_required": "学历要求，未提及填空字符串",
    "education_preferred": "学历加分项，未提及填空字符串"
  }},
  "responsibilities": ["岗位职责逐条列出"],
  "required_skills": [{{"skill": "硬性技能名", "evidence": "JD 中支撑该技能的原文片段"}}],
  "preferred_skills": [{{"skill": "加分技能名", "evidence": "JD 原文片段"}}],
  "tech_stack": ["JD 中明确出现的技术栈名词"],
  "domain_knowledge": ["需要具备的领域知识"],
  "soft_skills": ["软性要求"],
  "culture_values": ["JD 原文中明确写出的价值观/我们看重的能力"],
  "keywords": ["检索关键词"]
}}

硬性要求：
1. required_skills / preferred_skills 的每一项都必须带 evidence，
   evidence 必须是 JD 中真实出现的原文片段；没有原文依据的技能不得输出。
2. culture_values 只能来自 JD 原文（如"我们看重：严谨负责、主动学习"），
   严禁套用任何通用价值观模板或你自行臆测的企业文化。
3. 只提取 JD 里写明的内容，不得脑补 JD 中没有的要求。
4. JD 没有写明的字段一律填空字符串或空数组，不要填"不限"之类的推断值。
"""


def _normalize_skills(items: Any) -> List[Dict[str, str]]:
    """把技能条目统一成 {"skill","evidence"}，并丢弃无原文依据的项。

    无 evidence 的技能会被丢弃：宁可少提取，也不能让没有原文支撑的
    要求进入匹配流程去污染评分。
    """
    if not isinstance(items, list):
        return []
    out: List[Dict[str, str]] = []
    for item in items:
        if isinstance(item, str):
            item = {"skill": item, "evidence": ""}
        if not isinstance(item, dict):
            continue
        name = next((str(item[k]).strip() for k in _SKILL_KEYS if item.get(k)), "")
        evidence = str(item.get("evidence") or "").strip()
        if not name or not evidence:
            continue
        out.append({"skill": name, "evidence": evidence})
    return out


def _normalize_str_list(value: Any, limit: int = 50) -> List[str]:
    if not isinstance(value, list):
        return []
    out = []
    for v in value:
        if isinstance(v, str) and v.strip():
            out.append(v.strip())
        elif isinstance(v, dict):
            # 容忍模型把列表项写成对象
            name = next((str(v[k]).strip() for k in _SKILL_KEYS if v.get(k)), "")
            if name:
                out.append(name)
    return out[:limit]


def normalize_profile(result: Any) -> Optional[Dict[str, Any]]:
    """把任意来源的结构化结果归一化成岗位画像。

    LLM 解析结果与人工编辑结果走同一条归一化路径——否则 HR 在编辑框里
    就能塞进没有原文依据的技能项，绕过"必须可核对"这条纪律。
    没有可核对的技能项时返回 None（画像对匹配无意义）。
    """
    if not isinstance(result, dict):
        return None

    basic = result.get("basic") if isinstance(result.get("basic"), dict) else {}
    profile: Dict[str, Any] = {
        "basic": {k: basic.get(k, "") for k in _EMPTY_BASIC},
        "responsibilities": _normalize_str_list(result.get("responsibilities")),
        "required_skills": _normalize_skills(result.get("required_skills")),
        "preferred_skills": _normalize_skills(result.get("preferred_skills")),
        "tech_stack": _normalize_str_list(result.get("tech_stack")),
        "domain_knowledge": _normalize_str_list(result.get("domain_knowledge")),
        "soft_skills": _normalize_str_list(result.get("soft_skills")),
        "culture_values": _normalize_str_list(result.get("culture_values")),
        "keywords": _normalize_str_list(result.get("keywords")),
    }
    if not profile["required_skills"] and not profile["preferred_skills"]:
        return None
    return profile


async def parse_job_description(raw_text: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """把 JD 文本解析成结构化画像。

    Returns:
        (parsed_data, error)。成功时 error 为 None；
        失败（LLM 异常 / 无有效技能 / 文本不安全）时 parsed_data 为 None。
    """
    if not raw_text or not raw_text.strip():
        return None, "JD 内容为空"

    safe, reason = InputGuard.check(raw_text)
    if not safe:
        return None, f"JD 内容未通过安全检查：{reason}"

    text = raw_text[:MAX_JD_LENGTH]

    empty_profile: Dict[str, Any] = {
        "basic": dict(_EMPTY_BASIC),
        "responsibilities": [], "required_skills": [], "preferred_skills": [],
        "tech_stack": [], "domain_knowledge": [], "soft_skills": [],
        "culture_values": [], "keywords": [],
    }

    result = await await_llm(text, empty_profile)
    if result is None:
        return None, "JD 解析调用大模型失败，请检查 LLM 配置后重试"

    profile = normalize_profile(result)
    # 没有可核对的硬技能，画像对匹配没有意义，判失败且不允许启用
    if profile is None:
        return None, "未能从 JD 中提取到带原文依据的技能项，无法生成可用的岗位画像"

    return profile, None


async def await_llm(text: str, default: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """调用 LLM 解析 JD；返回 None 表示调用失败（与"返回 default"区分开）。

    转调统一接入层以获得埋点与成本记账；call_site=parse_job_description
    让 JD 解析的花费在统计里可单独归集。
    """
    from src.services.llm_invoke import invoke_json

    try:
        return await invoke_json(_PARSE_PROMPT, {"raw_text": text},
                                 default, "parse_job_description")
    except budget.BudgetExceeded:
        # 预算耗尽是系统级信号，向上抛让 API 返回可读错误而不是伪装成解析失败
        raise
    except Exception as e:
        print(f"[job_parser] JD 解析失败: {extract_llm_error(e)}")
        return None


def is_activatable(parsed_data: Optional[Dict[str, Any]]) -> Tuple[bool, str]:
    """校验画像是否达到可启用标准（activate 前置条件）"""
    if not isinstance(parsed_data, dict):
        return False, "尚未完成解析，无法启用"
    required = parsed_data.get("required_skills") or []
    preferred = parsed_data.get("preferred_skills") or []
    if not required and not preferred:
        return False, "岗位画像中没有带原文依据的技能项，无法启用"
    return True, ""