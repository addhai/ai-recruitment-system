import re
from typing import Tuple, Any


class InputGuard:
    """输入安全防护：拦截提示注入与危险内容。

    设计取舍：宁可误杀少量包含"忽略""系统提示"等词的正常 HR 文本，
    也不放过让 LLM 脱离系统角色的注入企图。招聘场景下用户没有正当理由
    在问卷/反馈/知识库提问中指挥模型"扮演别的角色"。
    """

    # 提示注入模式（中英文）。每条规则只描述攻击特征，避免正常业务用词误伤。
    INJECTION_PATTERNS = [
        # 英文注入
        r"ignore\s+(all\s+)?(previous|prior|above)\s+(instructions?|prompts?|context)",
        r"disregard\s+(all\s+)?(previous|prior|above)",
        r"forget\s+(everything|all|your\s+instructions)",
        r"you\s+are\s+now\s+a",
        r"act\s+as\s+a(n)?\s+",
        r"new\s+instructions?\s*[:：]",
        r"system\s*:\s*you",
        r"<\s*/?\s*system\s*>",
        r"reveal\s+(your\s+)?(system\s+)?prompt",
        r"print\s+your\s+(instructions|prompt)",
        r"jailbreak",
        r"\bDAN\b",
        # 中文注入
        r"忽略(以上|之前|上述|前面|所有)(的)?(指令|要求|规则|提示|设定|内容)",
        r"无视(以上|之前|上述|前面|所有)",
        r"忘记(你的|之前|所有|以上)(的)?(指令|设定|身份|角色|规则)",
        r"忘掉(你的|之前|所有)(的)?(指令|设定|身份|角色)",
        r"你现在是(一个|一名)?",
        r"你不再是(招聘|HR|人事|面试|助手)",
        r"请?扮演(一个|一名)?",
        r"进入(角色模式|角色|模式).{0,10}[:：]",  # 需带冒号指令，避免误伤"快速进入角色"
        r"输出(你的)?系统(提示词|指令|设定|消息)",
        r"泄露(你的)?(系统|初始|原始)(提示词|指令|prompt)",
        r"给(你|我)(新的|以下)(指令|任务|规则)[:：]",
        r"解除(你的)?(限制|安全|护栏|过滤)",
        r"越(狱|权)",
        r"开发者模式",
        r"role\s*[:：]\s*system",
    ]

    # 危险内容模式（XSS 等。SQL 注入在 ORM 参数化查询下风险低，不设规则以免误伤）
    DANGEROUS_PATTERNS = [
        r"<\s*script[^>]*>",
        r"javascript\s*:",
        r"on(?:error|load|click)\s*=",
        r"eval\s*\(",
        r"<\s*iframe[^>]*>",
        r"document\.cookie",
    ]

    MAX_LENGTH = 50000

    @classmethod
    def check(cls, text: str) -> Tuple[bool, str]:
        """检查输入是否安全，返回 (is_safe, reason)"""
        if not text:
            return True, ""

        for pattern in cls.INJECTION_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                return False, f"检测到可能的提示注入: {pattern}"

        for pattern in cls.DANGEROUS_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                return False, f"检测到危险内容: {pattern}"

        if len(text) > cls.MAX_LENGTH:
            return False, f"输入内容过长，超过{cls.MAX_LENGTH}字符限制"

        return True, ""

    @classmethod
    def check_dict_values(cls, data: Any) -> Tuple[bool, str]:
        """检查 dict/list 结构中所有字符串值（问卷作答等结构化输入）"""
        texts = []

        def _collect(obj):
            if isinstance(obj, str):
                texts.append(obj)
            elif isinstance(obj, dict):
                for v in obj.values():
                    _collect(v)
            elif isinstance(obj, (list, tuple)):
                for item in obj:
                    _collect(item)

        _collect(data)
        return cls.check("\n".join(texts))


class OutputGuard:
    """输出安全防护：过滤 PII。

    注意不用 \\b 做边界：中文与数字都属于 \\w，"身份证110101..." 中
    汉字与数字之间不存在 \\b，会导致紧贴中文的号码漏过。改用数字断言。
    邮箱在招聘场景是必要联系信息，不予脱敏。
    """

    PII_PATTERNS = [
        # 身份证号：17位数字 + 数字/X（用断言防止手机号/卡号部分匹配）
        (r"(?<![0-9A-Za-z])\d{17}[0-9Xx](?![0-9A-Za-z])", "[身份证号已隐藏]"),
        # 手机号：1开头的11位
        (r"(?<!\d)1[3-9]\d{9}(?!\d)", "[手机号已隐藏]"),
        # 银行卡号：16-19位连续数字（放在身份证/手机之后，避免被提前截断）
        (r"(?<!\d)\d{16,19}(?!\d)", "[银行卡号已隐藏]"),
    ]

    @classmethod
    def sanitize(cls, text: str) -> str:
        """过滤字符串中的 PII"""
        if not text or not isinstance(text, str):
            return text
        for pattern, replacement in cls.PII_PATTERNS:
            text = re.sub(pattern, replacement, text)
        return text

    @classmethod
    def sanitize_obj(cls, obj: Any) -> Any:
        """递归过滤 dict/list/字符串结构中的 PII（API 响应与 workflow state 复用）"""
        if isinstance(obj, str):
            return cls.sanitize(obj)
        if isinstance(obj, dict):
            return {k: cls.sanitize_obj(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [cls.sanitize_obj(item) for item in obj]
        return obj
