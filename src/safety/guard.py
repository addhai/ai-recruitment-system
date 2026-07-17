import re
from typing import Tuple

class InputGuard:
    """输入安全防护 — 防止提示注入和恶意内容"""
    
    # 常见提示注入模式
    INJECTION_PATTERNS = [
        r"ignore\s+(all\s+)?previous\s+instructions",
        r"disregard\s+(all\s+)?prior",
        r"you\s+are\s+now\s+a",
        r"system\s*:\s*you",
        r"<\s*system\s*>",
        r"忘记.*指令",
        r"忽略.*以上",
        r"你现在是",
    ]
    
    # 危险内容模式
    DANGEROUS_PATTERNS = [
        r"<script[^>]*>",
        r"javascript:",
        r"onerror\s*=",
        r"eval\s*\(",
    ]
    
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
        
        # 检查超长输入（可能是DoS攻击）
        if len(text) > 50000:
            return False, "输入内容过长，超过50000字符限制"
        
        return True, ""

class OutputGuard:
    """输出安全防护 — 过滤敏感信息"""
    
    # PII 模式
    PII_PATTERNS = [
        (r'\b\d{18}\b', '[身份证已隐藏]'),  # 身份证号
        (r'\b1[3-9]\d{9}\b', '[手机号已隐藏]'),  # 手机号
        (r'\b\d{16,19}\b', '[银行卡号已隐藏]'),  # 银行卡号
    ]
    
    @classmethod
    def sanitize(cls, text: str) -> str:
        """过滤输出中的PII信息"""
        if not text:
            return text
        import re
        for pattern, replacement in cls.PII_PATTERNS:
            text = re.sub(pattern, replacement, text)
        return text
