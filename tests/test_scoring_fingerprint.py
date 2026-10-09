"""评分口径指纹。

为什么这些用例值得存在：

指纹的全部价值在于"改了评分口径就**一定**变"。一个只会返回 16 位十六进制字符串的
实现也能让所有"有值"的断言通过，却完全不解决问题。所以这里用**合成源码**直接驱动
纯函数，逐类断言"这类改动必须产生新指纹"（提示词、权重、门限），并单独断言
注释/缩进这类非语义改动**不产生**假警报。

`TestRealModules` 那组则防另一个方向的失效：指纹依赖"源码里确实有可扫描的提示词"。
如果某次重构把提示词换成一个变量名、或扫描逻辑失效，指纹会安静地退化成覆盖率极低的
东西——那比没有还糟，因为看起来仍然"有个指纹"。所以直接用 AST 检查真实源码里
每个 `_llm_json` 调用点的第一个参数都是字面量字符串。
"""
import ast

from src.workflow import scoring_fingerprint as sf


class TestCodeDigest:
    """AST 摘要：语义变化必变，格式变化不变"""

    def test_same_source_same_digest(self):
        src = {"m": "P = '请评分'\ndef f():\n    return 1\n"}
        assert sf.code_digest(src) == sf.code_digest(dict(src))

    def test_module_order_does_not_matter(self):
        a = {"x": "A = 1\n", "y": "B = 2\n"}
        b = {"y": "B = 2\n", "x": "A = 1\n"}
        assert sf.code_digest(a) == sf.code_digest(b), "结果不能依赖文件读取顺序"

    def test_comment_and_indent_change_keeps_digest(self):
        """注释、空行、缩进不是口径。若它们也改指纹，指纹会被假警报淹掉。"""
        a = {"m": "def f():\n    return 1\n"}
        b = {"m": "# 说明注释\n\n\ndef f():\n        return 1\n"}
        assert sf.code_digest(a) == sf.code_digest(b)

    def test_prompt_change_changes_digest(self):
        """核心用例：改提示词必须变——这正是手工改 SCORING_VERSION 时会漏掉的动作"""
        a = {"m": "PROMPT = '请给候选人评分'\n"}
        b = {"m": "PROMPT = '请给候选人严格评分'\n"}
        assert sf.code_digest(a) != sf.code_digest(b)

    def test_weight_change_changes_digest(self):
        a = {"m": "OVERALL_WEIGHTS = {'skill': 0.20}\n"}
        b = {"m": "OVERALL_WEIGHTS = {'skill': 0.25}\n"}
        assert sf.code_digest(a) != sf.code_digest(b)

    def test_threshold_change_changes_digest(self):
        """门限是硬编码数字，不在任何配置里——只能靠源码摘要覆盖"""
        a = {"m": "INTERVIEW_PASS = 70\n"}
        b = {"m": "INTERVIEW_PASS = 75\n"}
        assert sf.code_digest(a) != sf.code_digest(b)

    def test_syntax_error_is_stable_and_sensitive(self):
        """解析不了时退回原文哈希：既不抛异常，也不把两种情况算成同一个"""
        broken = "def broken(:\n"
        d1 = sf.code_digest({"m": broken})
        assert d1 == sf.code_digest({"m": broken}), "同一份坏源码必须得到同一结果"
        assert d1 != sf.code_digest({"m": "def other(:\n"})

    def test_empty_sources_yields_stable_digest(self):
        assert sf.code_digest({}) == sf.code_digest({})


class TestConfigDigest:
    """运行时口径：模型与采样参数对可比性的影响和提示词一样直接"""

    _BASE = {"model": "deepseek-flash", "base_url": "https://api.example.com",
             "temperature": 0.1, "timeout_seconds": 45}

    def test_same_config_same_digest(self):
        assert sf.config_digest(self._BASE) == sf.config_digest(dict(self._BASE))

    def test_model_change_changes_digest(self):
        assert sf.config_digest(self._BASE) != sf.config_digest(
            dict(self._BASE, model="另一个模型"))

    def test_temperature_change_changes_digest(self):
        assert sf.config_digest(self._BASE) != sf.config_digest(
            dict(self._BASE, temperature=0.7))

    def test_base_url_change_changes_digest(self):
        assert sf.config_digest(self._BASE) != sf.config_digest(
            dict(self._BASE, base_url="https://other.example.com"))

    def test_credentials_and_prices_are_excluded(self):
        """凭据与单价刻意不参与指纹。

        单价改的是"花了多少钱"，不是"分数怎么算"；密钥换更不该让历史评分变成
        "不可比"。顺带保证凭据没有任何机会被卷进指纹输入。
        """
        with_extra = dict(self._BASE, api_key="sk-should-not-matter",
                          input_price=123.0, output_price=456.0)
        assert sf.config_digest(self._BASE) == sf.config_digest(with_extra)


class TestRealModules:
    """真实仓库上的行为：指纹有值、稳定、且覆盖到内联提示词"""

    def test_fingerprint_is_stable_and_shaped(self):
        fp = sf.fingerprint()
        assert fp is not None, "本仓库源码可读，不该退化成 None"
        assert len(fp) == 16 and all(ch in "0123456789abcdef" for ch in fp)
        assert fp == sf.fingerprint(), "同一份代码与配置必须得到同一指纹"

    def test_all_scoring_modules_are_readable(self):
        """三个模块都必须读到。少读一个不会报错，只会让指纹悄悄少覆盖一块。"""
        assert set(sf.module_sources()) == set(sf._SCORING_MODULES)

    def test_inline_prompts_are_covered_by_source(self):
        """守住指纹的覆盖率：真实源码里每个 `_llm_json` 调用点的提示词都必须是
        **字面量**或**模块级常量名**——这两种都完整地住在 AST 里。

        只要这条成立，提示词就一定在源码摘要里：将来新增一处提示词会自动进入指纹，
        不需要谁记得去登记。若哪天有人把提示词改成运行时拼装（例如从多段变量拼出来），
        源码摘要就覆盖不到它了，这条会失败，提醒必须换一种方式把它纳入指纹。
        """
        src = sf.module_sources()["src.workflow.recruitment_graph"]
        tree = ast.parse(src)
        module_constants = {
            n.targets[0].id
            for n in ast.walk(tree)
            if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
        }

        prompts = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "_llm_json":
                assert node.args, "_llm_json 调用缺少 prompt 参数"
                first = node.args[0]
                if isinstance(first, ast.Constant):
                    assert isinstance(first.value, str), "提示词字面量必须是字符串"
                    prompts.append(first.value)
                else:
                    assert isinstance(first, ast.Name) and first.id in module_constants, (
                        "提示词既不是字面量也不是模块级常量，源码摘要将覆盖不到它")
                    prompts.append(first.id)

        # 数量下限只是"扫描没失效"的哨兵：评分/决策/出题的内联提示词共 10 处
        assert len(prompts) >= 10, (
            f"只扫到 {len(prompts)} 处提示词，远少于预期——"
            "要么图节点被重构了，要么这套扫描方式已经失效，必须重新审视指纹的覆盖率")

    def test_module_constant_prompts_are_covered(self):
        """模块级提示词常量（如面试出题）同样在 AST 摘要里"""
        src = sf.module_sources()["src.workflow.recruitment_graph"]
        names = {n.targets[0].id for n in ast.walk(ast.parse(src))
                 if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)}
        assert "_INTERVIEW_QUESTION_PROMPT" in names
