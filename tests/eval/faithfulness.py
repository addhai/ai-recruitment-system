# -*- coding: utf-8 -*-
"""RAG 答案忠实度评测（离线，零 API 成本，可进日常 CI）。

## 为什么需要这一层

`src/rag/knowledge_base.py` 的提示词里写着"上下文没有的内容明确说
'知识库中暂无相关规定'，禁止编造"——但**提示词只是请求，不是保证**。
项目至今没有任何一条断言检查过"模型回答里的事实确实来自检索到的片段"，
这条防线从未被验证过。

本模块补的就是这个洞：给定（问题、检索到的片段、模型回答），断言回答里的
事实性内容**能在检索到的片段里找到出处**，而不是模型自己编的。

## 它管什么，不管什么

- **管**：数字（天数、比例、日期、金额、时长）、代号（`S级`/`M1-M8`）、
  条款名与制度名——这些要么在原文里出现，要么就是编的。
- **不管**：语义等价性、自由文本描述、回答质量高低。本模块不打分，
  只回答一个问题："回答里的事实，有出处吗？"

## 为什么宁可漏判不可滥判

忠实度评测的头号杀手是**误报**。一个满屏红字的报告会被立刻无视，
比没有报告更糟——人只会记住"这个评测老是误报"。所以这里的取舍是：

| 内容类型 | 判定力度 | 理由 |
| --- | --- | --- |
| 数字类事实 | 硬断言 | 要么原文出现，要么就是编的，可靠性最高 |
| 条款名/制度名 | 硬断言（允许"核心词命中"的宽松） | 名称是最典型的幻觉载体，且格式固定 |
| 代号（`S级`/`M1`） | 硬断言 | 字符边界清晰，抽取可靠 |
| 英文缩写（`HR`/`IT`） | **只记录不判定** | 模型通用话术里常见，上下文未必逐字出现，误报率高 |
| 自由文本 | **只记录不判定** | 无法区分"常识性补充"与"编造"，硬判必然误报 |

## 宽容度声明（明确写出，避免"悄悄放宽"）

1. **标点、空白、全半角差异不算差异**：在归一化阶段直接消除，
   因此**不计入**"匹配不上"的计数。
2. **默认零容忍**：`max_unsupported=0`，任何一个无依据事实即判 FAIL。
   可显式调大，用于"只看趋势"的宽松场景（会在报告里显式写出来）。
3. **单位同量纲兼容**：`30分钟` ↔ `30分` 视为同一量纲（宁可漏判）。
   跨量纲（`5天` ↔ `5元`）**不兼容**，判为编造。
4. **裸数字宽松**：回答里没有单位的数字，只要上下文出现过同值即算有出处
   （回答"5"而上下文写"5天"，不判编造）。
5. **制度名核心词宽松**：回答"公司的年假制度"而上下文只有"年假"，
   核心词命中即通过（模型常加"公司的/相关的"这类壳）。

## 用法

```python
from tests.eval.faithfulness import check_faithfulness

res = check_faithfulness(answer, contexts)
res["ok"]           # bool：是否通过
res["unsupported"]  # 无依据的事实 token 列表
res["checked"]      # 全部检查过的 token 及结论（含"只记录不判定"的软信号）
res["detail"]       # 人读摘要
```

`contexts` 是检索到的片段列表，每项至少含 `content`（可含 `title`；
也接受 `page_content` / `text`）。

离线自检（跑一遍用例集，不需要 LLM、不需要网络）：

```bash
python tests/eval/faithfulness.py
```
"""
from __future__ import annotations

import json
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[2]
EVAL_DIR = Path(__file__).resolve().parent
CASES_FILE = EVAL_DIR / "faithfulness_cases.json"


# ================================================================ 归一化
# 宽容度第 1 条：标点 / 空白 / 全半角差异在归一化阶段直接消除，
# 所以它们永远不会变成"匹配不上"的理由。

_WS_RE = re.compile(r"\s+")
_NONWORD_RE = re.compile(r"[^\w]+", re.UNICODE)


def _nfkc(text: str) -> str:
    """Unicode NFKC 归一：全角数字/字母/百分号 → 半角，全角空格 → 半角空格。

    `５` → `5`、`80％` → `80%`、`１年` → `1年`。中文标点也在此归一，
    但比较用的 `_loose` 会把标点一起去掉，所以不影响。
    """
    return unicodedata.normalize("NFKC", text or "")


def _compact(text: str) -> str:
    """去掉所有空白，保留标点。"""
    return _WS_RE.sub("", _nfkc(text))


def _loose(text: str) -> str:
    """去掉所有空白与标点，只留文字/数字/字母/下划线。

    这是短语匹配用的口径：`员工福利制度` 与 `员工 福利 制度。` 在这里等价。
    """
    return _NONWORD_RE.sub("", _compact(text))


# ================================================================ 中文数字

_CN_DIGIT = {
    "零": 0, "〇": 0, "一": 1, "壹": 1, "二": 2, "两": 2, "贰": 2,
    "三": 3, "叁": 3, "四": 4, "肆": 4, "五": 5, "伍": 5, "六": 6,
    "陆": 6, "七": 7, "柒": 7, "八": 8, "捌": 8, "九": 9, "玖": 9,
}
_CN_UNIT = {"十": 10, "拾": 10, "百": 100, "佰": 100, "千": 1000, "仟": 1000}


def _cn_to_int(text: str) -> Optional[int]:
    """中文数字转阿拉伯数字。支持 十五/二十/三十五/一百二十/两千 等常见写法。

    解析不了就返回 None（调用方按"不是数字"处理，绝不猜）。
    """
    if not text:
        return None
    total = 0
    section = 0
    number = 0
    seen = False
    for ch in text:
        if ch in _CN_DIGIT:
            number = _CN_DIGIT[ch]
            seen = True
        elif ch in _CN_UNIT:
            unit = _CN_UNIT[ch]
            # "十五" 的前导十：数值为 1
            if unit == 10 and number == 0:
                number = 1
            section += number * unit
            number = 0
            seen = True
        elif ch == "万":
            section += number
            total += section * 10000
            section = 0
            number = 0
            seen = True
        elif ch == "亿":
            section += number
            total += section * 100000000
            section = 0
            number = 0
            seen = True
        else:
            return None
    if not seen:
        return None
    return total + section + number


# ================================================================ 单位与量纲
# 只把"能确定是量"的字当单位。像"条/项/个/点/步"这类计数词**故意不在表里**，
# 否则"3条建议""第一步"会被当成事实——它们是结构，不是量。

_UNIT_ALIAS = {
    "个月": "month", "月": "month",
    "工作日": "workday", "天": "day", "日": "day",
    "星期": "week", "周": "week",
    "年": "year", "岁": "year",
    "元": "cny", "块": "cny", "人民币": "cny",
    "小时": "hour", "分钟": "minute", "秒": "second",
    "分": "score",
    "次": "times", "人": "person", "名": "person",
    "%": "percent",
}

# 同量纲簇：单位不同但量纲相同 → 视为匹配（宁可漏判，不可滥判）。
# "30分钟" 与 "30分" 同簇；"5天" 与 "5元" 不同簇 → 判为编造。
_UNIT_CLUSTER = {
    "day": "day", "workday": "day",
    "month": "month",
    "week": "week",
    "year": "year",
    "hour": "measure", "minute": "measure", "second": "measure", "score": "measure",
    "cny": "money",
    "percent": "percent",
    "times": "count", "person": "count",
}

# 数字 + 单位的正则：单位按长度降序，保证"个月"先于"月"被匹配。
_UNIT_PATTERN = "|".join(sorted(_UNIT_ALIAS.keys(), key=len, reverse=True))


def _unit_of(raw_unit: str) -> str:
    return _UNIT_ALIAS.get(raw_unit, "")


# ================================================================ 事实模型


@dataclass(frozen=True)
class Fact:
    """一个待验证的事实 token。

    - kind="num"：单值事实（value/unit）
    - kind="range"：区间事实（lo/hi/unit）
    - kind="name"：名称类事实（policy/clause/code/abbr）
    """
    kind: str
    raw: str
    unit: str = ""
    value: Optional[float] = None
    lo: Optional[float] = None
    hi: Optional[float] = None
    name_type: str = ""


# ================================================================ 套话白名单
# 规则（每条都有理由）：
# 1. 提示词要求模型说"知识库中暂无相关规定"——这是**正确行为**，
#    若把它当成事实去比对，必然天天误报。
# 2. "根据上述信息/如上所述"是连接词，指代已给出的内容，不承载新事实。
# 3. "您好/请问还有什么可以帮您"是礼貌与引导语，不承载事实。
# 4. "相关规定/公司制度"是**泛称**，模型用它指代任意制度，
#    上下文不可能逐字出现，判为编造纯属误报。
#
# 注意：这里是"短语替换"而非"整句跳过"——只有当句子去掉这些壳之后
# 不剩实质内容时，才整句跳过。带数字的句子永远不会被吞掉。

_FILLER_PHRASES = [
    "知识库中暂无相关规定", "知识库中暂无相关的内容", "知识库中暂无相关记录",
    "暂无相关规定", "暂无相关记载", "未找到相关规定", "没有相关规定",
    "根据上述信息", "根据以上信息", "根据上述内容", "根据以上内容",
    "根据上文信息", "根据上文", "根据上下文信息", "根据上下文",
    "根据知识库", "基于提供的上下文信息", "基于提供的上下文", "基于上下文",
    "结合上下文信息", "结合上下文", "如上所述", "综上所述", "综合以上",
    "综合上述", "总而言之", "总的来说",
    "如下所示", "具体如下", "具体来说", "以下是", "以下为", "如下",
    "您好", "你好", "感谢您的咨询", "感谢您的提问", "感谢咨询", "感谢您的耐心阅读",
    "希望对您有帮助", "希望以上信息", "希望这些信息",
    "如有任何疑问", "如有其他问题", "请随时提出", "随时告知", "欢迎继续咨询",
    "请问还有什么", "还有什么可以帮您", "还有其他问题",
    "祝您工作顺利", "祝您生活愉快",
    "公司的相关规章制度", "公司的规章制度", "公司各项规章制度", "企业内部制度",
    "相关规定", "相关制度", "国家规定", "公司制度", "公司的制度",
]

# 长度降序：保证"如下所示"先于"如下"、"根据上下文信息"先于"根据上下文"。
_FILLER_SORTED = sorted(set(_FILLER_PHRASES), key=len, reverse=True)

# 纯连接词：去掉这些之后句子就不剩实质内容了 → 整句跳过。
_CONNECTIVES = {
    "根据", "基于", "结合", "如上", "综上", "上述", "以下", "以上", "如下",
    "本", "该", "此", "其中", "此外", "另外", "同时", "再者", "首先", "其次",
    "最后", "总之", "请", "谢谢", "好的", "明白", "了解", "收到", "关于",
    "对于", "至于", "如果", "那么", "以及", "并且", "和", "与", "及", "或",
    "等", "信息", "内容", "情况", "方面", "规定", "制度", "要求", "内容",
}
_CONNECTIVES_SORTED = sorted(_CONNECTIVES, key=len, reverse=True)


def _strip_filler(text: str) -> str:
    """去掉套话/连接词外壳。"""
    out = text
    for phrase in _FILLER_SORTED:
        if phrase in out:
            out = out.replace(phrase, " ")
    return out


def _is_filler_only(text: str) -> bool:
    """判断去掉套话后是否只剩连接词（= 这句话不承载任何事实）。"""
    s = _loose(text)
    if not s:
        return True
    i = 0
    while i < len(s):
        for word in _CONNECTIVES_SORTED:
            if s.startswith(word, i):
                i += len(word)
                break
        else:
            return False
    return True


# ================================================================ 结构性句子
# "总结如下3点""分为3部分"里的数字是**对回答自身的描述**，
# 不是关于知识库的事实，拿去比对必然误报。

_STRUCTURE_HINTS = (
    "总结", "归纳", "概括", "要点", "建议", "可以分为", "主要分为", "分为",
    "以下几", "如下", "以上", "接下来", "首先", "其次", "最后", "第一",
    "第二", "第三", "一方面", "另一方面", "具体来说", "详细来说", "综上",
)

_COUNT_NOISE_RES = [
    # 引导词 + 计数词：整段抹掉
    re.compile(r"(?:共|计|合计|分为|主要有|包括|包含|涵盖|列举|以下|如下|以上|总计)"
               r"\s*[0-9]{1,3}\s*(?:条|点|项|个|部分|方面|步|种|类)"),
    # 计数词直接跟在数字后
    re.compile(r"[0-9]{1,3}\s*(?:个方面|个维度|点建议|条建议|个要点|项建议|"
               r"点提示|个小节|条要点|个步骤|点内容)"),
]

# 行首列表序号：不是事实。Markdown 的 "1. "、"（1）" 都要去掉。
_LIST_MARK_RE = re.compile(r"(?m)^[ \t]*(?:[-*+•]\s*)?"
                            r"(?:[0-9]{1,3}[.、)）]|\([0-9]{1,3}\))\s*")


def _preprocess(text: str) -> str:
    """预处理：抹掉结构性计数与行首列表序号。"""
    out = _nfkc(text)
    out = _LIST_MARK_RE.sub(" ", out)
    for pattern in _COUNT_NOISE_RES:
        out = pattern.sub(" ", out)
    return out


# ================================================================ 名称类规则
# 制度/条款名是幻觉的高发区（模型很容易顺口造一个《XXX办法》）。
# 但"相关规定""公司制度"是泛称，模型会随手用 → 泛称单列白名单。

_POLICY_SUFFIXES = (
    "制度", "规定", "办法", "细则", "政策", "条例", "守则", "准则",
    "章程", "流程", "规范",
)
_POLICY_SUFFIX_SORTED = sorted(_POLICY_SUFFIXES, key=len, reverse=True)

# 泛称前缀：剥掉后剩下的才是"实质名"。模型爱加"公司的/相关的/各项的"。
_GENERIC_PREFIXES = (
    "本公司的", "公司的", "公司", "我司", "贵司", "企业内部", "企业内部制度",
    "企业", "国家", "相关", "各项", "上述", "以下", "内部", "集团", "上级",
    "本", "该", "此",
)

# 引导连接词：出现在句首的"根据/依据/关于……"不是名称的一部分。
# 不剥掉它，"根据公司的年假制度" 会被当成整个制度名，核心词就再也提不出来。
_LEADING_TRIM = (
    "根据上述", "根据以上", "根据", "依据", "按照", "关于", "对于", "结合",
    "详见", "参见", "参阅", "上述", "以上", "以下",
)

# 泛称核心词：剥完前缀只剩这些 → 说明这只是个泛指，不判。
_GENERIC_CORES = {
    "", "相关", "公司", "国家", "企业", "各项", "该", "本", "此", "内部",
    "集团", "上级", "对应", "有关", "以下", "上述",
}

_POLICY_RE = re.compile(
    r"([一-鿿A-Za-z0-9][一-鿿A-Za-z0-9·\-]{0,19}?)"
    r"(" + "|".join(_POLICY_SUFFIX_SORTED) + r")"
)
_CLAUSE_RE = re.compile(r"第[0-9一二三四五六七八九十百千零两]{1,6}(?:条|章|节|款|部分)")
_GRADE_RE = re.compile(r"(?<![A-Za-z])[A-Za-z]\s*级")
_ABBR_RE = re.compile(r"(?<![A-Za-z])[A-Z]{2,}(?![A-Za-z])")
_BOOK_RE = re.compile(r"《([^《》]{1,30})》")


def _trim_leading(candidate: str) -> str:
    """剥掉名称前面的引导连接词。"""
    changed = True
    while changed:
        changed = False
        for word in _LEADING_TRIM:
            if candidate.startswith(word) and len(candidate) > len(word):
                candidate = candidate[len(word):]
                changed = True
                break
    return candidate


def _core_word(candidate: str) -> str:
    """取制度名的"实质核心词"：去掉后缀词、引导连接词与泛称前缀。

    "根据公司的年假制度" → "年假"；"相关规定" → ""（泛称）。
    """
    core = _trim_leading(candidate)
    for suffix in _POLICY_SUFFIX_SORTED:
        if core.endswith(suffix) and len(core) > len(suffix):
            core = core[: -len(suffix)]
            break
    changed = True
    while changed:
        changed = False
        for prefix in _GENERIC_PREFIXES:
            # 允许剥成空串（"相关规定" → ""），空串命中泛称白名单 → 只记录不判定。
            if core.startswith(prefix) and len(core) >= len(prefix):
                core = core[len(prefix):]
                changed = True
                break
        else:
            for word in _LEADING_TRIM:
                if core.startswith(word) and len(core) > len(word):
                    core = core[len(word):]
                    changed = True
                    break
    return core


def _core_hit(core: str, ctx_loose: str) -> Optional[str]:
    """核心词是否在上下文中有依据，返回命中的片段（没有则 None）。

    两级判定：
    1. 核心词整体命中（最强）。
    2. **长后缀命中**（兜底）：模型常在名称尾部换修饰或加壳，
       "员工福利制度" 被说成 "员工年假制度" 时，尾部实体名 "年假" 才是可靠的锚点。
       但要求后缀长度 ≥ max(2, len(core)/3)——门槛太高会放过
       "员工持股激励管理办法" 这类完全不同的名字（它的尾部 "管理" 只有 2 字，
       而门槛是 3 字），门槛太低又会把通用词当依据。
    """
    if len(core) < 2:
        return None
    if _loose(core) and _loose(core) in ctx_loose:
        return core
    need = max(2, (len(core) + 2) // 3)
    for length in range(len(core), need - 1, -1):
        fragment = core[-length:]
        if _loose(fragment) and _loose(fragment) in ctx_loose:
            return fragment
    return None


# ================================================================ 数字类规则
# 抽取顺序即优先级：先占坑，后来的低优先级模式不得重复抽取同一段文本。

_CODE_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]{1,3}[0-9]+"
                      r"(?:\s*[-–—~至到]\s*[A-Za-z]{1,3}[0-9]+)?(?![A-Za-z0-9])")
_TIME_RE = re.compile(r"(?<![0-9])[0-9]{1,2}\s*[:：]\s*[0-9]{2}(?:\s*[:：]\s*[0-9]{2})?(?![0-9])")
_YMD_RE = re.compile(r"(?<![0-9])[0-9]{4}\s*年(?:\s*[0-9]{1,2}\s*月)?(?:\s*[0-9]{1,2}\s*日)?")
_MD_RE = re.compile(r"(?<![0-9])[0-9]{1,2}\s*月\s*[0-9]{1,2}\s*日")
_DAY_RE = re.compile(r"(?<![0-9])[0-9]{1,2}\s*日")
_RANGE_RE = re.compile(r"(?<![0-9])[0-9]+(?:\.[0-9]+)?\s*[-–—~至到]\s*[0-9]+(?:\.[0-9]+)?"
                       r"(?:\s*(?:" + _UNIT_PATTERN + r"))?")
_PERCENT_RE = re.compile(r"(?<![0-9])[0-9]+(?:\.[0-9]+)?\s*%")
_NUM_UNIT_RE = re.compile(r"(?<![0-9])[0-9]+(?:\.[0-9]+)?\s*(?:" + _UNIT_PATTERN + r")")
_CN_PERCENT_RE = re.compile(r"百分之[零〇一二三四五六七八九十百千万两]{1,8}")
_CN_FOLD_RE = re.compile(r"[零〇一二三四五六七八九十百千万两]{1,4}折")
_CN_NUM_UNIT_RE = re.compile(r"[零〇一二三四五六七八九十百千万两]{1,8}\s*(?:" + _UNIT_PATTERN + r")")
_BARE_RE = re.compile(r"(?<![0-9.])\d+(?:\.\d+)?")


def _is_ordinal(text: str, start: int) -> bool:
    """`第3天` / `第三条` 里的数字是序数，不是量 → 不提取。

    序数不该拿去比对：上下文写"第3轮"而回答写"第3轮"当然对，
    但回答写"第1轮"而上下文只给了第2、3轮，也是模型编的——
    这类"编造轮次"由名称类规则（`第X条`）覆盖，数字规则不碰，避免双重误报。
    """
    return start > 0 and text[start - 1] == "第"


def _unit_suffix_len(raw: str) -> int:
    """从 token 末尾解析出单位字（返回它在 raw 中的长度）。"""
    for unit in sorted(_UNIT_ALIAS.keys(), key=len, reverse=True):
        if raw.endswith(unit):
            return len(unit)
    return 0


def _extract_numbers(text: str) -> List[Fact]:
    """按优先级抽取数字类事实。"""
    facts: List[Fact] = []
    occupied: List[Tuple[int, int]] = []

    def _free(start: int, end: int) -> bool:
        return all(end <= s or start >= e for s, e in occupied)

    def _take(m: "re.Match[str]") -> bool:
        if _is_ordinal(text, m.start()):
            return False
        if not _free(m.start(), m.end()):
            return False
        occupied.append((m.start(), m.end()))
        return True

    # 1) 代号：M1-M8 / T1 / F6 —— 字符边界清晰，先占坑
    for m in _CODE_RE.finditer(text):
        if _take(m):
            facts.append(Fact(kind="name", raw=m.group(0), name_type="code"))

    # 2) 时间：9:00 / 14:00 —— 拆成分钟数，两个端点分别比对
    for m in _TIME_RE.finditer(text):
        if not _take(m):
            continue
        parts = [int(x) for x in re.findall(r"\d+", m.group(0))]
        if len(parts) >= 2:
            facts.append(Fact(kind="num", raw=m.group(0), unit="minute",
                              value=parts[0] * 60 + parts[1]))

    # 3) 区间：1-3个月 / 2-4周 / 80-89分
    for m in _RANGE_RE.finditer(text):
        if not _take(m):
            continue
        nums = re.findall(r"\d+(?:\.\d+)?", m.group(0))
        if len(nums) < 2:
            continue
        unit_len = _unit_suffix_len(m.group(0))
        unit = _unit_of(m.group(0)[-unit_len:]) if unit_len else ""
        facts.append(Fact(kind="range", raw=m.group(0), unit=unit,
                          lo=float(nums[0]), hi=float(nums[1])))

    # 4) 日期：拆成年 / 月 / 日三个独立事实，方便与上下文逐项比对
    for m in _YMD_RE.finditer(text):
        if not _take(m):
            continue
        raw = m.group(0)
        year = re.search(r"(\d{4})\s*年", raw)
        if year:
            facts.append(Fact(kind="num", raw=year.group(0), unit="year",
                              value=float(year.group(1))))
        month = re.search(r"(\d{1,2})\s*月", raw)
        if month:
            facts.append(Fact(kind="num", raw=month.group(0), unit="month",
                              value=float(month.group(1))))
        day = re.search(r"(\d{1,2})\s*日", raw)
        if day:
            facts.append(Fact(kind="num", raw=day.group(0), unit="day",
                              value=float(day.group(1))))
    for m in _MD_RE.finditer(text):
        if not _take(m):
            continue
        raw = m.group(0)
        month = re.search(r"(\d{1,2})\s*月", raw)
        day = re.search(r"(\d{1,2})\s*日", raw)
        if month:
            facts.append(Fact(kind="num", raw=month.group(0), unit="month",
                              value=float(month.group(1))))
        if day:
            facts.append(Fact(kind="num", raw=day.group(0), unit="day",
                              value=float(day.group(1))))
    for m in _DAY_RE.finditer(text):
        if not _take(m):
            continue
        facts.append(Fact(kind="num", raw=m.group(0), unit="day",
                          value=float(re.search(r"\d+", m.group(0)).group(0))))

    # 5) 百分比：80% / 80％（NFKC 后已是半角）
    for m in _PERCENT_RE.finditer(text):
        if not _take(m):
            continue
        value = float(re.search(r"\d+(?:\.\d+)?", m.group(0)).group(0))
        facts.append(Fact(kind="num", raw=m.group(0), unit="percent", value=value))

    # 6) 阿拉伯数字 + 单位：5天 / 15日 / 300元 / 每月15日
    for m in _NUM_UNIT_RE.finditer(text):
        if not _take(m):
            continue
        raw = m.group(0)
        unit_len = _unit_suffix_len(raw)
        unit = _unit_of(raw[-unit_len:]) if unit_len else ""
        value = float(re.search(r"\d+(?:\.\d+)?", raw).group(0))
        facts.append(Fact(kind="num", raw=raw, unit=unit, value=value))

    # 7) 百分之八十 / 八成 → 百分比
    for m in _CN_PERCENT_RE.finditer(text):
        if not _take(m):
            continue
        value = _cn_to_int(re.search(r"百分之([零〇一二三四五六七八九十百千万两]+)", m.group(0)).group(1))
        if value is not None:
            facts.append(Fact(kind="num", raw=m.group(0), unit="percent", value=float(value)))

    # 8) 八折 → 80%
    for m in _CN_FOLD_RE.finditer(text):
        if not _take(m):
            continue
        value = _cn_to_int(m.group(0).replace("折", ""))
        if value is not None:
            facts.append(Fact(kind="num", raw=m.group(0), unit="percent", value=float(value * 10)))

    # 9) 中文数字 + 单位：五天 / 一百元 / 十五日
    for m in _CN_NUM_UNIT_RE.finditer(text):
        if not _take(m):
            continue
        raw = m.group(0)
        unit_len = _unit_suffix_len(raw)
        unit = _unit_of(raw[-unit_len:]) if unit_len else ""
        digits = raw[: len(raw) - unit_len].strip()
        value = _cn_to_int(digits)
        if value is None:
            continue
        facts.append(Fact(kind="num", raw=raw, unit=unit, value=float(value)))

    # 10) 裸数字：剩下的阿拉伯数字。序数（第X）不提取。
    for m in _BARE_RE.finditer(text):
        if not _take(m):
            continue
        facts.append(Fact(kind="num", raw=m.group(0), unit="",
                          value=float(m.group(0))))
    return facts


def _extract_names(text: str) -> List[Fact]:
    """抽取名称类事实：制度名 / 条款号 / 等级代号 / 英文缩写。"""
    facts: List[Fact] = []
    # 书名号里的内容一律当制度名候选：书名号是"引用某份文件"的强信号。
    for m in _BOOK_RE.finditer(text):
        facts.append(Fact(kind="name", raw=m.group(1), name_type="policy"))

    # 后缀式制度名。书名号内部已被 mask 掉，避免重复抽取。
    masked = _BOOK_RE.sub(lambda mm: " " * len(mm.group(0)), text)
    for m in _POLICY_RE.finditer(masked):
        candidate = _trim_leading(m.group(1) + m.group(2))
        if candidate:
            facts.append(Fact(kind="name", raw=candidate, name_type="policy"))
    for m in _CLAUSE_RE.finditer(masked):
        facts.append(Fact(kind="name", raw=m.group(0), name_type="clause"))
    for m in _GRADE_RE.finditer(masked):
        facts.append(Fact(kind="name", raw=m.group(0).replace(" ", ""), name_type="code"))
    for m in _ABBR_RE.finditer(masked):
        facts.append(Fact(kind="name", raw=m.group(0), name_type="abbr"))
    return facts


# ================================================================ 上下文索引


class _ContextIndex:
    """检索片段的预编译索引：一次建好，后面每条事实 O(片段数) 比对。"""

    def __init__(self, texts: Sequence[str]):
        self.text = "\n".join(t for t in texts if t)
        self.loose = _loose(self.text)
        self.facts: List[Fact] = []
        for chunk in texts:
            if not chunk:
                continue
            prepared = _preprocess(chunk)
            self.facts.extend(_extract_numbers(prepared))
            self.facts.extend(_extract_names(prepared))

    @classmethod
    def build(cls, contexts: Optional[Sequence[Dict[str, Any]]]) -> "_ContextIndex":
        texts: List[str] = []
        for ctx in contexts or []:
            if not isinstance(ctx, dict):
                continue
            # title 也算依据：文档标题本身就是制度名的一部分。
            title = str(ctx.get("title") or "").strip()
            if title:
                texts.append(title)
            for key in ("content", "page_content", "text"):
                value = ctx.get(key)
                if value:
                    texts.append(str(value))
        return cls(texts)


# ================================================================ 匹配


def _units_compatible(answer_unit: str, ctx_unit: str, strict: bool) -> bool:
    """单位是否兼容。

    - 一方没有单位 → 兼容（宽容度第 4 条）
    - strict → 只认完全相同
    - 否则同量纲簇兼容（`30分钟` ↔ `30分`）
    """
    if not answer_unit or not ctx_unit:
        return not strict or (not answer_unit and not ctx_unit)
    if answer_unit == ctx_unit:
        return True
    if strict:
        return False
    a = _UNIT_CLUSTER.get(answer_unit)
    b = _UNIT_CLUSTER.get(ctx_unit)
    return bool(a and b and a == b)


def _match_number(fact: Fact, ctx: _ContextIndex, strict: bool) -> Tuple[str, str]:
    """数值事实与上下文的匹配。"""
    for other in ctx.facts:
        if fact.kind == "num" and other.kind == "num":
            if fact.value == other.value and _units_compatible(fact.unit, other.unit, strict):
                return "supported", f"数值 {fact.value:g} 与上下文「{other.raw}」一致"
        elif fact.kind == "num" and other.kind == "range":
            if (other.lo is not None and other.hi is not None
                    and other.lo <= fact.value <= other.hi
                    and _units_compatible(fact.unit, other.unit, strict)):
                return "supported", f"数值 {fact.value:g} 落在上下文区间「{other.raw}」内"
        elif fact.kind == "range" and other.kind == "range":
            if (fact.lo == other.lo and fact.hi == other.hi
                    and _units_compatible(fact.unit, other.unit, strict)):
                return "supported", f"区间 {fact.raw} 与上下文「{other.raw}」一致"
            if (other.lo is not None and other.hi is not None
                    and other.lo <= fact.lo and other.hi >= fact.hi
                    and _units_compatible(fact.unit, other.unit, strict)):
                return "supported", f"区间 {fact.raw} 被上下文「{other.raw}」覆盖"
        elif fact.kind == "range" and other.kind == "num":
            if (fact.lo is not None and fact.hi is not None
                    and fact.lo <= other.value <= fact.hi
                    and _units_compatible(fact.unit, other.unit, strict)):
                return "supported", f"区间 {fact.raw} 覆盖上下文数值「{other.raw}」"
    return "unsupported", "上下文中找不到该数值的依据"


def _match_policy(fact: Fact, ctx: _ContextIndex, strict: bool) -> Tuple[str, str]:
    """制度名/条款名的匹配。

    宽松（默认）：核心词命中即可——模型常加"公司的/相关的"这类壳，
    或把"员工福利制度"说成"福利制度"。要求整体逐字命中会误报。
    严格（strict=True）：必须整体命中。
    """
    core = _core_word(fact.raw)
    if core in _GENERIC_CORES:
        return "soft", "泛称制度名（无实质内容），不判定"
    if _loose(fact.raw) and _loose(fact.raw) in ctx.loose:
        return "supported", "制度名在上下文原文中出现"
    if not strict:
        hit = _core_hit(core, ctx.loose)
        if hit:
            return "supported", f"制度名核心词「{hit}」在上下文中出现"
    return "unsupported", f"上下文中找不到「{fact.raw}」（核心词「{core or '—'}」）"


def _match_exact_name(fact: Fact, ctx: _ContextIndex, strict: bool) -> Tuple[str, str]:
    """条款号 / 等级代号的匹配：字符边界清晰，要求原文逐字出现。"""
    key = _loose(fact.raw)
    if key and key in ctx.loose:
        return "supported", f"「{fact.raw}」在上下文原文中出现"
    return "unsupported", f"上下文中找不到「{fact.raw}」"


def _match_abbr(fact: Fact, ctx: _ContextIndex, strict: bool) -> Tuple[str, str]:
    """英文缩写：默认**只记录不判定**。

    理由：模型在通用话术里经常冒出 HR / IT / AI 这类缩写，
    而检索片段未必逐字出现，硬判必然误报。
    """
    key = _loose(fact.raw)
    if key and key in ctx.loose:
        return "supported", f"「{fact.raw}」在上下文原文中出现"
    if strict:
        return "unsupported", f"上下文未出现英文缩写「{fact.raw}」"
    return "soft", f"英文缩写「{fact.raw}」未在上下文出现（默认只记录不判定）"


def _match_fact(fact: Fact, ctx: _ContextIndex, strict: bool) -> Tuple[str, str]:
    if fact.kind in ("num", "range"):
        return _match_number(fact, ctx, strict)
    if fact.name_type == "policy":
        return _match_policy(fact, ctx, strict)
    if fact.name_type in ("clause", "code"):
        return _match_exact_name(fact, ctx, strict)
    if fact.name_type == "abbr":
        return _match_abbr(fact, ctx, strict)
    return "soft", "未知事实类型，跳过"


# ================================================================ 句子切分

_SENT_SPLIT_RE = re.compile(r"[。！？!?；;\n]+")


def _split_sentences(text: str) -> List[str]:
    """切句。

    英文句号只在后面跟空白时才切——否则 "1.5%" 会被从中间劈开。
    """
    parts: List[str] = []
    for chunk in _SENT_SPLIT_RE.split(text or ""):
        if not chunk.strip():
            continue
        parts.extend(p for p in re.split(r"(?<=\.)\s+", chunk) if p.strip())
    return parts


# ================================================================ 主接口


def check_faithfulness(answer: str,
                        contexts: Optional[Sequence[Dict[str, Any]]],
                        *,
                        strict: bool = False,
                        max_unsupported: int = 0) -> Dict[str, Any]:
    """断言回答里的事实性内容都能在检索片段里找到出处。

    参数
    ----
    answer: 模型给出的回答文本。
    contexts: 检索到的片段列表，每项至少含 `content`（可含 `title`）。
    strict: 严格模式。默认 False（宽松，优先防误报）；True 时收紧四件事：
        1. 制度名必须**整体**命中（不再接受"核心词命中"）；
        2. 数字的单位必须**严格一致**（不再接受同量纲兼容，如 30分钟↔30分）；
        3. 回答里的裸数字必须与上下文**同单位**（不再接受"值相同即可"）；
        4. 英文缩写升级为硬断言（默认只记录）。
    max_unsupported: 允许有多少个事实 token 匹配不上仍算通过。
        默认 0（零容忍）。调大用于"只看趋势"的宽松场景。

    返回
    ----
    {"ok": bool, "unsupported": [...], "checked": [...], "detail": str, "stats": {...}}
    """
    ctx = _ContextIndex.build(contexts)
    checked: List[Dict[str, Any]] = []
    unsupported: List[Dict[str, Any]] = []
    sentences = _split_sentences(answer or "")
    skipped_sentences = 0

    for sentence in sentences:
        stripped = _strip_filler(sentence)
        if _is_filler_only(stripped):
            # 整句只剩套话/连接词 → 不承载事实，跳过（这是防误报的关键一步）
            skipped_sentences += 1
            checked.append({
                "kind": "sentence",
                "token": sentence.strip()[:60],
                "status": "skipped",
                "reason": "套话/连接词，不承载事实",
            })
            continue

        prepared = _preprocess(stripped)
        structural = any(hint in stripped for hint in _STRUCTURE_HINTS)

        facts = _extract_numbers(prepared) + _extract_names(prepared)
        for fact in facts:
            status, reason = _match_fact(fact, ctx, strict)
            # 结构性句子里的**裸数字**降级为软信号：那是模型在描述自己的
            # 回答结构（"总结3点"），不是关于知识库的事实。
            if (status == "unsupported" and structural and fact.kind == "num"
                    and not fact.unit):
                status = "soft"
                reason = "结构性句子中的裸数字（描述回答结构），只记录不判定"
            item = {
                "kind": fact.kind if fact.kind != "name" else fact.name_type,
                "token": fact.raw.strip(),
                "status": status,
                "reason": reason,
            }
            checked.append(item)
            if status == "unsupported":
                unsupported.append(item)

    ok = len(unsupported) <= max_unsupported
    detail = _build_detail(len(sentences), skipped_sentences, checked, unsupported, max_unsupported)
    return {
        "ok": ok,
        "unsupported": unsupported,
        "checked": checked,
        "detail": detail,
        "stats": {
            "sentences": len(sentences),
            "skipped_sentences": skipped_sentences,
            "facts": len([c for c in checked if c["kind"] != "sentence"]),
            "unsupported": len(unsupported),
            "soft": len([c for c in checked if c["status"] == "soft"]),
            "strict": strict,
            "max_unsupported": max_unsupported,
        },
    }


def _build_detail(sentence_count: int, skipped: int, checked: List[Dict[str, Any]],
                  unsupported: List[Dict[str, Any]], max_unsupported: int) -> str:
    facts = [c for c in checked if c["kind"] != "sentence"]
    soft = [c for c in checked if c["status"] == "soft"]
    parts = [f"检查 {len(facts)} 个事实 token（{sentence_count} 句，跳过套话 {skipped} 句）"]
    if unsupported:
        tokens = "、".join(f"「{u['token']}」" for u in unsupported[:5])
        parts.append(f"无依据 {len(unsupported)} 个：{tokens}")
    else:
        parts.append("全部有出处")
    if soft:
        parts.append(f"只记录不判定 {len(soft)} 个")
    if max_unsupported:
        parts.append(f"宽容度：允许 {max_unsupported} 个无依据 token")
    return "；".join(parts)


# ================================================================ 用例集执行


def run_cases(cases: Sequence[Dict[str, Any]],
              *,
              strict: bool = False,
              max_unsupported: int = 0) -> Dict[str, Any]:
    """跑一遍用例集，返回结构化结果（供单测与 CLI 自检共用）。"""
    results: List[Dict[str, Any]] = []
    for case in cases or []:
        result = check_faithfulness(
            case.get("answer", ""),
            case.get("contexts") or [],
            strict=bool(case.get("strict", strict)),
            max_unsupported=int(case.get("max_unsupported", max_unsupported)),
        )
        expected = bool(case.get("expect_ok", True))
        results.append({
            "name": case.get("name", ""),
            "note": case.get("note", ""),
            "expected": expected,
            "ok": result["ok"],
            "passed": result["ok"] == expected,
            "detail": result["detail"],
            "unsupported": result["unsupported"],
        })
    passed = sum(1 for r in results if r["passed"])
    return {
        "cases": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "ok": passed == len(results),
        "results": results,
    }


def main() -> int:
    """离线自检：跑一遍用例集，不碰网络、不调 LLM。"""
    # Windows 控制台默认 GBK，打不出「✗」这类符号会直接崩在 print 上。
    # 报告里全是中文，编码必须是 UTF-8 才读得懂。
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    if not CASES_FILE.exists():
        print(f"用例文件不存在: {CASES_FILE}")
        return 2
    cases = json.loads(CASES_FILE.read_text(encoding="utf-8"))["cases"]
    report = run_cases(cases)

    print("=" * 74)
    print(f"忠实度用例自检：{report['passed']}/{report['cases']} 通过")
    print("=" * 74)
    for r in report["results"]:
        mark = "PASS" if r["passed"] else "FAIL"
        expect = "PASS" if r["expected"] else "FAIL"
        print(f"\n[{mark}] {r['name']}（期望 {expect}）")
        print(f"    {r['detail']}")
        if not r["passed"]:
            for u in r["unsupported"]:
                print(f"      ✗ 「{u['token']}」: {u['reason']}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
