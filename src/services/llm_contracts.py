# -*- coding: utf-8 -*-
"""LLM 调用的输出契约：每次调用应该返回什么形状。

## 为什么需要这一层

`invoke_json` 只保证"返回的是**可解析的 JSON**"，不保证字段齐全、类型正确、
取值在合理范围内。于是模型返回 `{"score": null}` 时：

- `invoke_json` 认为这次调用**成功了**（埋点记 `status=ok`、`degraded=false`）；
- 节点层用兜底值把它变成 70 分，并标进 `degraded_dimensions`；
- 但 `llm_call_logs` 里**完全看不出这次调用不合规**。

上一轮的注入实验已经证明这类违规不是小事：`bad_schema` 模式下任务存活率只有
0.400（超时/5xx/坏 JSON 都是 1.000，因为后三者被 `invoke_json` 捕获并降级了）。
也就是说，**类型违规是唯一会穿透到驱动层的故障**，而它在真实流量里发生几次、
能不能靠一次修正救回来，此前**一个数字都给不出来**。

## 这一层做什么

1. 给每个 call_site 声明输出契约（必填字段、类型、取值域）。
2. 调用返回后校验，把"首次是否合规""修正后是否合规""哪里不合规"落进埋点。
3. 不合规时**自动发起一次修正调用**（把错误回灌给模型），修正仍不合规则用兜底值。

## 这一层不做什么

**不判断内容对不对**。契约只管形状（字段在不在、类型对不对、分值在不在 0~100），
不管理由写得靠不靠谱、决策合不合理——那是"推理质量"的评测口径，见
`tests/eval/task_quality_eval.py`。两者必须分开：

- 形状不合规 = 调用坏了，兜底是**必要**的保护；
- 形状合规但内容离谱 = 调用没坏，是模型判断的问题，靠评测集发现而不是靠兜底。

把两者混在一起，会出现"因为内容不好所以兜底"的假保护——兜底值 70 分在通过区间，
一旦被伪装成模型评分就会直接放行候选人（上一轮修掉的正是这个）。

## 类型说明

- `number`：int/float（**bool 不算**，因为 `True` 在 Python 里是 int 的子类）；
- `string`：str；
- `list`：任意元素的列表；
- `dict`：映射；
- `list[dict]`：元素必须是 dict，且每个元素都要有 `item_keys` 里列的键。
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

KIND_NUMBER = "number"
KIND_STRING = "string"
KIND_LIST = "list"
KIND_DICT = "dict"
KIND_LIST_DICT = "list[dict]"
KIND_BOOL = "bool"

_KIND_CN = {
    KIND_NUMBER: "数字",
    KIND_STRING: "字符串",
    KIND_LIST: "列表",
    KIND_DICT: "对象",
    KIND_LIST_DICT: "对象列表",
    KIND_BOOL: "布尔",
}


@dataclass(frozen=True)
class FieldSpec:
    """单个字段的契约。"""

    name: str
    kind: str
    required: bool = True
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    item_keys: Tuple[str, ...] = ()
    choices: Tuple[str, ...] = ()

    def describe(self) -> str:
        """人类可读的期望描述，用于拼修正提示词。"""
        bits = [_KIND_CN.get(self.kind, self.kind)]
        if self.kind == KIND_NUMBER and self.minimum is not None and self.maximum is not None:
            bits.append(f"{self.minimum:g}~{self.maximum:g}")
        if self.choices:
            bits.append("取值之一：" + "/".join(self.choices))
        if self.kind == KIND_LIST_DICT and self.item_keys:
            bits.append("每项必含 " + "/".join(self.item_keys))
        return "，".join(bits)

    def check(self, value: Any) -> Optional[str]:
        """返回错误描述；合规返回 None。"""
        if value is None:
            return f"缺失（期望{self.describe()}）"

        if self.kind == KIND_BOOL:
            if not isinstance(value, bool):
                return f"期望{self.describe()}，实际 {type(value).__name__}"
            return None

        if self.kind == KIND_NUMBER:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return f"期望{self.describe()}，实际 {type(value).__name__}"
            if self.minimum is not None and value < self.minimum:
                return f"期望{self.describe()}，实际 {value}"
            if self.maximum is not None and value > self.maximum:
                return f"期望{self.describe()}，实际 {value}"
            return None

        if self.kind == KIND_STRING:
            if not isinstance(value, str):
                return f"期望{self.describe()}，实际 {type(value).__name__}"
            if self.choices and value not in self.choices:
                return (f"期望{self.describe()}，实际 {value!r}")
            return None

        if self.kind == KIND_LIST:
            if not isinstance(value, list):
                return f"期望{self.describe()}，实际 {type(value).__name__}"
            return None

        if self.kind == KIND_DICT:
            if not isinstance(value, dict):
                return f"期望{self.describe()}，实际 {type(value).__name__}"
            return None

        if self.kind == KIND_LIST_DICT:
            if not isinstance(value, list):
                return f"期望{self.describe()}，实际 {type(value).__name__}"
            for i, item in enumerate(value):
                if not isinstance(item, dict):
                    return f"第 {i + 1} 项期望对象，实际 {type(item).__name__}"
                missing = [k for k in self.item_keys if k not in item]
                if missing:
                    return f"第 {i + 1} 项缺少 {'/'.join(missing)}"
            return None

        return None


@dataclass(frozen=True)
class Contract:
    """某个 call_site 的输出契约。"""

    call_site: str
    fields: Tuple[FieldSpec, ...] = field(default_factory=tuple)

    def validate(self, value: Any) -> List[str]:
        """校验返回值，返回错误列表；合规返回空列表。"""
        if not isinstance(value, dict):
            return [f"整个返回不是对象，实际 {type(value).__name__}"]
        errors: List[str] = []
        for spec in self.fields:
            if spec.name not in value:
                if spec.required:
                    errors.append(f"{spec.name}: 缺失（期望{spec.describe()}）")
                continue
            err = spec.check(value.get(spec.name))
            if err:
                errors.append(f"{spec.name}: {err}")
        return errors

    def describe(self) -> str:
        """给模型看的契约描述（修正提示词里要用）。"""
        lines = []
        for spec in self.fields:
            mark = "必填" if spec.required else "可选"
            lines.append(f"- {spec.name}（{mark}）：{spec.describe()}")
        return "\n".join(lines)


# ---------------------------------------------------------------- 契约表

def _f(name: str, kind: str, **kw) -> FieldSpec:
    return FieldSpec(name=name, kind=kind, **kw)


_SCORE = _f("score", KIND_NUMBER, minimum=0, maximum=100)

CONTRACTS: Dict[str, Contract] = {
    "parse_resume": Contract(
        call_site="parse_resume",
        fields=(
            _f("basic_info", KIND_DICT),
            _f("education", KIND_LIST),
            _f("experience", KIND_LIST),
            _f("skills", KIND_LIST),
            _f("projects", KIND_LIST, required=False),
            _f("language", KIND_LIST, required=False),
        ),
    ),
    "extract_skills": Contract(
        call_site="extract_skills",
        fields=(
            _f("skills_technical", KIND_LIST),
            _f("skills_soft", KIND_LIST, required=False),
            _f("domain_knowledge", KIND_LIST, required=False),
        ),
    ),
    "evaluate_skill_match": Contract(
        call_site="evaluate_skill_match",
        fields=(
            _SCORE,
            _f("matched_skills", KIND_LIST),
            _f("missing_skills", KIND_LIST),
            _f("reason", KIND_STRING, required=False),
        ),
    ),
    "evaluate_experience": Contract(
        call_site="evaluate_experience",
        fields=(_SCORE, _f("analysis", KIND_STRING)),
    ),
    "evaluate_education": Contract(
        call_site="evaluate_education",
        fields=(_SCORE, _f("analysis", KIND_STRING)),
    ),
    "assess_cultural_fit": Contract(
        call_site="assess_cultural_fit",
        fields=(
            _SCORE,
            _f("evidence", KIND_LIST),
            _f("reasons", KIND_LIST),
        ),
    ),
    "questionnaire_stage": Contract(
        call_site="questionnaire_stage",
        fields=(
            _f("questions", KIND_LIST_DICT, item_keys=("question",)),
        ),
    ),
    "score_questionnaire": Contract(
        call_site="score_questionnaire",
        fields=(
            _SCORE,
            _f("per_question", KIND_LIST_DICT, item_keys=("q", "score"), required=False),
            _f("feedback", KIND_STRING, required=False),
        ),
    ),
    "generate_interview_questions": Contract(
        call_site="generate_interview_questions",
        fields=(
            _f("questions", KIND_LIST_DICT, item_keys=("question",)),
        ),
    ),
    "generate_hiring_decision": Contract(
        call_site="generate_hiring_decision",
        fields=(
            _f("decision", KIND_STRING,
               choices=("推荐录用", "待定", "不推荐", "待人工复核")),
            _f("reasons", KIND_LIST),
            _f("risks", KIND_LIST, required=False),
        ),
    ),
    "parse_job_description": Contract(
        call_site="parse_job_description",
        fields=(
            _f("basic", KIND_DICT),
            _f("required_skills", KIND_LIST),
        ),
    ),
    # 推理质量裁判（评测侧专用，见 tests/eval/task_quality_eval.py）。
    # 裁判自己也要受契约约束——裁判输出不合规时，那一票本来就不可信。
    "reasoning_judge": Contract(
        call_site="reasoning_judge",
        fields=(
            _f("decision_consistent", KIND_BOOL),
            _f("reason_score_consistent", KIND_BOOL),
            _f("has_hallucination", KIND_BOOL),
            _f("hallucination_items", KIND_LIST),
            _f("quality_score", KIND_NUMBER, minimum=0, maximum=100),
            _f("issues", KIND_LIST),
        ),
    ),
    # 评测侧：合成候选人作答（见 tests/eval/task_quality_eval.py）。
    "synthetic_candidate_answer": Contract(
        call_site="synthetic_candidate_answer",
        fields=(
            _f("answers", KIND_LIST_DICT, item_keys=("qid", "text")),
        ),
    ),
    # 评测侧：维度理由审计。维度理由是推理质量的**大样本**
    # （每个任务 4~5 条，而最终决策只有跑到决策节点的那批任务才有）。
    "dimension_judge": Contract(
        call_site="dimension_judge",
        fields=(
            _f("reason_score_consistent", KIND_BOOL),
            _f("has_hallucination", KIND_BOOL),
            _f("hallucination_items", KIND_LIST),
            _f("inconsistent_dimensions", KIND_LIST),
            _f("quality_score", KIND_NUMBER, minimum=0, maximum=100),
            _f("issues", KIND_LIST),
        ),
    ),
}


def contract_for(call_site: str) -> Optional[Contract]:
    """取某个 call_site 的契约；没有登记就返回 None（不校验）。"""
    return CONTRACTS.get(call_site)


def repair_prompt(contract: Contract, bad_value: Any, errors: Sequence[str]) -> str:
    """拼修正提示词：把不合规的输出和错误清单回灌给模型，要求重出。

    为什么要单独一次调用而不是本地补默认值：本地补默认值等于**伪造模型输出**
    （把 `score: null` 改成 70 并记成模型给的 70 分），那正是上一轮修掉的
    "兜底被伪装成模型评分"。让模型自己重出一次，出来的才是真值；
    修不好再退回兜底，此时才是诚实的兜底。
    """
    import json

    try:
        shown = json.dumps(bad_value, ensure_ascii=False)[:2000]
    except Exception:
        shown = repr(bad_value)[:2000]

    return (
        "你上一次的输出不符合要求的结构，本次只需修正结构，内容重新给一次。\n\n"
        f"【要求的输出结构】\n{contract.describe()}\n\n"
        f"【你上一次的输出】\n{shown}\n\n"
        "【存在的问题】\n"
        + "\n".join(f"- {e}" for e in errors)
        + "\n\n只输出一个 JSON 对象，不要输出任何解释文字或 Markdown 代码块标记。"
    )
