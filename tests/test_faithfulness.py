# -*- coding: utf-8 -*-
"""答案忠实度评测的单测（离线，零 API 成本）。

## 为什么它能进日常 CI

它只做纯字符串比对：不联网、不调 LLM、不碰数据库，结果完全可复现。
和 `tests/eval/run_chunk_eval.py`（L2 检索层）一样属于**可进 CI** 的那一层；
真正跑 LLM 看模型有没有编造的活儿留在 `tests/eval/run_eval.py`（L1，手动触发）。

## 它拦什么

1. **判定逻辑退化**：有人把白名单删了、或把容差改成 0，套话用例会当场变红。
2. **误报回归**（最重要）：防误报用例必须一直是 PASS。一旦变成 FAIL，
   说明评测开始对着「您好」「根据上述信息」报警——这种报告没人看。
3. **用例集本身腐化**：漏场景、忘写 note、判定与声明不一致。
"""
import ast
import json
import re
from pathlib import Path

import pytest

from tests.eval import faithfulness
from tests.eval.faithfulness import (
    CASES_FILE,
    _cn_to_int,
    _core_hit,
    _core_word,
    _loose,
    check_faithfulness,
    run_cases,
)


@pytest.fixture(scope="module")
def cases():
    """用例集。内容缺失就直接报错——门禁不能静默跳过。"""
    assert CASES_FILE.exists(), f"用例集不存在: {CASES_FILE}"
    data = json.loads(CASES_FILE.read_text(encoding="utf-8"))
    loaded = data["cases"]
    assert loaded, "用例集为空，忠实度回归无从谈起"
    return loaded


def pytest_generate_tests(metafunc):
    """把用例集注入到需要它的测试里。

    `parametrize` 不能直接吃 fixture 的值——fixture 是运行时才有的，
    而参数化必须在**收集期**就确定。所以这里在收集期自己读一遍用例文件。
    文件缺失会直接变成收集错误，这正是我们要的：门禁不能静默跳过。
    """
    if "case" in metafunc.fixturenames:
        assert CASES_FILE.exists(), f"用例集不存在: {CASES_FILE}"
        loaded = json.loads(CASES_FILE.read_text(encoding="utf-8"))["cases"]
        metafunc.parametrize("case", loaded,
                             ids=[c.get("name", "?") for c in loaded])


# ================================================================ 用例集门禁


def test_case_expectation_holds(case):
    """每条用例的判定结果必须与用例声明一致。

    这是本文件的主干：28 条用例把「该 PASS 的 PASS、该 FAIL 的 FAIL」钉死。
    """
    result = check_faithfulness(
        case.get("answer", ""),
        case.get("contexts") or [],
        strict=bool(case.get("strict", False)),
        max_unsupported=int(case.get("max_unsupported", 0)),
    )
    expected = bool(case.get("expect_ok", True))
    tolerance = int(case.get("max_unsupported", 0))
    assert result["ok"] == expected, (
        f"用例「{case.get('name')}」期望 {expected}，实际 {result['ok']}\n"
        f"    详情: {result['detail']}\n"
        f"    无依据: {result['unsupported']}")
    if expected and tolerance == 0:
        # 宽容度为 0 时，PASS 就意味着一个无依据事实都没有。
        # 宽容度 > 0 的用例允许留痕（它要验证的正是"留痕但放行"）。
        assert not result["unsupported"], (
            f"用例「{case.get('name')}」期望 PASS，不该有无依据事实: {result['unsupported']}")
    if not expected:
        assert result["unsupported"], (
            f"用例「{case.get('name')}」期望 FAIL，却没抓出任何无依据事实")


def test_case_file_covers_required_scenarios(cases):
    """用例集必须覆盖任务书点名的场景，否则「覆盖」只是嘴上说说。"""
    blob = json.dumps(cases, ensure_ascii=False)
    required = {
        "正常回答": "正常回答",
        "编造数字": "编造数字",
        "编造制度名": "编造制度名",
        "套话": "套话",
        "中英混排": "中英混排",
        "全半角": "全半角",
        "空上下文": "空上下文",
        "空回答": "空回答",
    }
    missing = [key for key, marker in required.items() if marker not in blob]
    assert not missing, f"用例集缺少场景: {missing}"


def test_every_case_has_note(cases):
    """每条用例都要写 note 说明它在防什么——没有理由的用例过半年没人敢改。"""
    missing = [c.get("name") for c in cases if not (c.get("note") or "").strip()]
    assert not missing, f"以下用例缺少 note: {missing}"


def test_run_cases_agrees_with_per_case_calls(cases):
    """批量运行器与逐条调用必须给出同一结论（防止两套口径漂移）。"""
    report = run_cases(cases)
    assert report["ok"], (
        f"用例集自检失败：{report['passed']}/{report['cases']} 通过\n"
        + "\n".join(f"  ✗ {r['name']}: {r['detail']}"
                    for r in report["results"] if not r["passed"]))
    assert report["failed"] == 0


# ================================================================ 防误报（最关键）
# 下面这几条是「评测本身不能变成误报源」的看门狗。


def test_polite_boilerplate_is_not_a_fact():
    """「您好」「根据上述信息」「如有其他问题」不承载事实，不能被当成幻觉。"""
    result = check_faithfulness(
        "您好！根据上述信息，如上所述。如有其他问题请随时提出？",
        [],
    )
    assert result["ok"], result["detail"]
    assert not result["unsupported"]
    # 整句跳过，而不是"比对后发现没依据"
    assert all(c["status"] == "skipped" for c in result["checked"])
    assert result["stats"]["skipped_sentences"] == 3


def test_boilerplate_does_not_hide_real_facts():
    """反过来：套话不能把真正的编造一起吞掉。"""
    result = check_faithfulness(
        "您好，根据上述信息，员工入职满1年可享受7天年假。如有其他问题请随时提出。",
        [{"title": "员工福利制度", "content": "年假：入职满1年享受5天，最多15天"}],
    )
    assert not result["ok"], "带套话的编造必须仍然被抓出来"
    tokens = [u["token"] for u in result["unsupported"]]
    assert "7天" in tokens, f"应抓出编造的天数，实际抓到: {tokens}"


def test_list_ordinals_are_not_facts():
    """Markdown 列表序号（1. 2. 3.）是排版，不是知识库里的事实。"""
    result = check_faithfulness(
        "1. 入职满1年享受5天\n2. 最多15天\n3. 每增加1年增加1天",
        [{"title": "员工福利制度", "content": "年假：入职满1年享受5天，每增加1年增加1天，最多15天"}],
    )
    assert result["ok"], result["detail"]
    assert not result["unsupported"]


def test_self_referential_counts_are_not_facts():
    """「共3点」「以下3点」是模型在描述自己的回答结构，不能拿去比对。"""
    result = check_faithfulness(
        "根据上述信息，年假相关规定可归纳为以下几点：共3点。",
        [],
    )
    assert result["ok"], result["detail"]
    assert not result["unsupported"]


def test_no_context_but_only_refusal_still_passes():
    """「知识库中暂无相关规定」是提示词要求的正确行为，不是幻觉。"""
    result = check_faithfulness("知识库中暂无相关规定。", [])
    assert result["ok"], result["detail"]
    assert not result["unsupported"]


def test_abbreviation_is_soft_signal_by_default():
    """英文缩写默认只记录不判定（模型通用话术里 IT/HR 太常见）。"""
    result = check_faithfulness("IT 部门会配置电脑，HR 负责入职材料。", [])
    assert result["ok"], result["detail"]
    soft = [c for c in result["checked"] if c["status"] == "soft"]
    assert soft, "缩写应被记为软信号，而不是悄悄消失"
    assert all(c["kind"] == "abbr" for c in soft)


def test_abbreviation_becomes_hard_in_strict_mode():
    """strict 模式下缩写升级为硬断言——严格模式的意义就在这里。"""
    result = check_faithfulness("IT 部门会配置电脑。", [], strict=True)
    assert not result["ok"]
    assert any(c["kind"] == "abbr" for c in result["unsupported"])


# ================================================================ 边界与结构


def test_empty_inputs_pass():
    """空回答 / 空上下文 / None 上下文都不得抛异常，且空回答必须 PASS。"""
    assert check_faithfulness("", [])["ok"] is True
    assert check_faithfulness("", None)["ok"] is True
    assert check_faithfulness("", [])["unsupported"] == []


def test_result_shape_is_stable():
    """返回结构固定：ok / unsupported / checked / detail / stats。"""
    result = check_faithfulness("年假5天。", [{"title": "员工福利制度", "content": "年假5天"}])
    for key in ("ok", "unsupported", "checked", "detail", "stats"):
        assert key in result, f"缺少字段 {key}"
    assert isinstance(result["ok"], bool)
    assert isinstance(result["unsupported"], list)
    assert isinstance(result["checked"], list)
    assert isinstance(result["detail"], str) and result["detail"]
    for item in result["unsupported"]:
        assert set(("kind", "token", "status", "reason")) <= set(item)


def test_title_counts_as_evidence():
    """文档标题本身就是制度名的一部分，必须算依据。"""
    result = check_faithfulness("根据《员工福利制度》，年假最多15天。",
                                 [{"title": "员工福利制度", "content": "年假：最多15天"}])
    assert result["ok"], result["detail"]


def test_content_alias_is_accepted():
    """片段可以只给 content，也可以给 page_content / text。"""
    for key in ("content", "page_content", "text"):
        result = check_faithfulness("年假最多15天。", [{"title": "员工福利制度", key: "年假：最多15天"}])
        assert result["ok"], f"{key} 未被识别为依据: {result['detail']}"


def test_max_unsupported_is_explicit_tolerance():
    """宽容度是显式参数：允许 N 个无依据事实仍算通过。"""
    answer = "入职满1年可享受5天年假，最多20天。"
    contexts = [{"title": "员工福利制度", "content": "年假：入职满1年享受5天，最多15天"}]
    strict_result = check_faithfulness(answer, contexts)
    assert not strict_result["ok"]
    assert len(strict_result["unsupported"]) == 1
    lenient = check_faithfulness(answer, contexts, max_unsupported=1)
    assert lenient["ok"], lenient["detail"]
    assert lenient["stats"]["max_unsupported"] == 1
    assert "宽容度" in lenient["detail"]


def test_same_value_different_dimension_fails():
    """数值相同但量纲不同（300元 ↔ 300天）必须判为编造。"""
    result = check_faithfulness("试用期最多300天。",
                                 [{"title": "员工福利制度", "content": "餐补：每月300元"}])
    assert not result["ok"]
    assert any("300" in u["token"] for u in result["unsupported"])


def test_full_and_half_width_are_equivalent():
    """全半角与空格差异在归一化阶段消除，绝不能算成无依据。"""
    result = check_faithfulness("试用期为１－３个月，比例为 80％。",
                                 [{"title": "员工入职流程",
                                   "content": "试用期为1-3个月，比例为80%"}])
    assert result["ok"], result["detail"]


def test_chinese_numeral_is_recognized():
    """中文数字与阿拉伯数字指向同一个量。"""
    result = check_faithfulness("餐补每月三百元。",
                                 [{"title": "员工福利制度", "content": "餐补：每月300元"}])
    assert result["ok"], result["detail"]


# ================================================================ 归一化工具的直接测试


@pytest.mark.parametrize("text,expected", [
    ("十五", 15), ("二十", 20), ("三十五", 35), ("一百二十", 120),
    ("两百", 200), ("九", 9), ("一百零五", 105),
])
def test_cn_to_int(text, expected):
    assert _cn_to_int(text) == expected


def test_cn_to_int_rejects_non_numbers():
    """解析不了就返回 None，绝不猜——猜错会让评测误报。"""
    assert _cn_to_int("五险") is None
    assert _cn_to_int("") is None
    assert _cn_to_int("制度") is None


def test_loose_strips_punctuation_and_spaces():
    """短语匹配口径：标点与空白等价。

    注意 `_loose` 连符号一起去（"80%" → "80"）——它只用于**名称**匹配；
    数字类事实走的是专门的抽取与比对，不经过 loose，所以不受影响。
    """
    assert _loose("员工 福利 制度。") == _loose("员工福利制度")
    assert _loose("80％") == _loose("80%")


def test_core_word_strips_shell():
    """制度名的核心词：去后缀、去引导词、去泛称前缀。"""
    assert _core_word("公司的年假制度") == "年假"
    assert _core_word("根据公司的年假制度") == "年假"
    assert _core_word("相关规定") == ""


def test_core_hit_requires_a_real_anchor():
    """核心词兜底必须命中足够长的锚点，否则完全不同的名字会被放过。"""
    ctx = "员工福利制度年假入职满1年享受5天"
    assert _core_hit("年假", ctx) == "年假"
    assert _core_hit("员工年假", ctx) == "年假"      # 尾部锚点
    assert _core_hit("员工持股激励管理", ctx) is None  # 完全不同的名字


# ================================================================ 离线保证


def test_module_has_no_network_or_llm_dependency():
    """本模块必须纯离线：不引网络库、不引 LLM SDK、不引项目 src。"""
    source = Path(faithfulness.__file__).read_text(encoding="utf-8")
    # 用 ast 取真实 import，别用正则扫源码——docstring 里的示例会误伤。
    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    allowed = {
        "json", "re", "sys", "unicodedata", "dataclasses", "pathlib",
        "typing", "__future__",
    }
    unexpected = {name for name in imported if name.split(".")[0] not in allowed}
    assert not unexpected, f"模块引入了不允许的依赖: {unexpected}"
    for banned in ("requests", "httpx", "openai", "langchain", "urlopen"):
        assert banned not in source, f"模块不应出现 {banned}"


class TestRunnerLoader:
    """run_eval 里的动态加载路径必须真的能加载出模块。

    **为什么单独守这一条**：run_eval 用 importlib 按路径加载忠实度模块
    （脚本方式运行时 tests/eval 下没有 __init__.py，普通 import 不可用）。
    动态加载出来的模块默认不在 sys.modules 里，而 @dataclass 在类定义完成时
    会回查 `sys.modules[cls.__module__]` —— 回查拿到 None 就抛
    `AttributeError: 'NoneType' object has no attribute '__dict__'`。

    那个异常发生在**导入期**，后果是整个忠实度断言模块加载不起来：
    每条知识库用例都只报一句"断言执行异常"，看起来像断言不通过，
    实际是**断言压根没跑**——一个不会自己冒头的静默故障。
    """

    def test_loader_returns_usable_module(self):
        from tests.eval.run_eval import _load_faithfulness

        module = _load_faithfulness()
        assert hasattr(module, "check_faithfulness"), "加载出来的模块应当能用"

    def test_loader_registers_module_in_sys_modules(self):
        import sys

        from tests.eval.run_eval import _load_faithfulness

        module = _load_faithfulness()
        assert sys.modules.get(module.__name__) is module, (
            "模块必须注册进 sys.modules，否则 dataclass 装饰器回查不到自己的模块")

    def test_assertion_returns_verdict_not_exception(self):
        """断言要给出判定结论，而不是抛异常。"""
        from tests.eval.run_eval import _a_faithful

        ctxs = [{"title": "员工福利制度", "content": "年假：入职满1年可休5天，最多15天。"}]
        ok, detail = _a_faithful({"answer": "年假可以休5天", "_kb_contexts": ctxs},
                                 {"kind": "faithful"})
        assert isinstance(ok, bool)
        assert detail, "判定必须带说明"

    def test_fabricated_number_is_caught(self):
        """有出处的数字放行，凭空编的数字必须被抓住。"""
        from tests.eval.run_eval import _a_faithful

        ctxs = [{"title": "员工福利制度", "content": "年假：入职满1年可休5天，最多15天。"}]
        ok, detail = _a_faithful({"answer": "年假可以休30天", "_kb_contexts": ctxs},
                                 {"kind": "faithful"})
        assert ok is False, "凭空出现的 30 天应当被抓住：%s" % detail
