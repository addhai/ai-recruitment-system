"""简历文本数据清洗模块

处理多种格式简历的清洗与标准化，解决以下问题：
1. 空白字符清理 — 多余空格、换行、制表符
2. 编码统一 — GBK/GB2312/UTF-8 等编码自动检测与转换
3. 图片型PDF — 扫描件 PDF 的 OCR 文字识别
4. HTML 简历 — 去除标签提取纯文本
5. 特殊字符过滤 — 控制字符、emoji、零宽字符
6. 格式校验 — 文件类型、大小、是否为有效简历
7. 文本分段 — 长文本按段落分割，便于 LLM 解析
"""
from __future__ import annotations

import logging
import os
import re
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# 支持的文件类型
SUPPORTED_EXTENSIONS = {".txt", ".pdf", ".docx", ".doc", ".html", ".htm"}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
MAX_TEXT_LENGTH = 50000  # 最大文本长度


# ---------------------------------------------------------------------------
# 1. 格式校验
# ---------------------------------------------------------------------------
def validate_resume_file(filename: str, file_size: int) -> Tuple[bool, str, str]:
    """校验上传的简历文件

    Returns:
        (is_valid, reason, extension)
    """
    if not filename:
        return False, "文件名为空", ""

    ext = os.path.splitext(filename)[1].lower()
    if ext not in SUPPORTED_EXTENSIONS:
        return False, f"不支持的文件格式: {ext}，支持: {', '.join(SUPPORTED_EXTENSIONS)}", ext

    if file_size <= 0:
        return False, "文件为空", ext

    if file_size > MAX_FILE_SIZE:
        return False, f"文件过大({file_size // 1024 // 1024}MB)，最大支持 10MB", ext

    return True, "", ext


# ---------------------------------------------------------------------------
# 2. 编码统一
# ---------------------------------------------------------------------------
def detect_and_decode(content: bytes) -> str:
    """自动检测编码并解码为 UTF-8 字符串

    尝试顺序: UTF-8 → GB18030 → GBK → GB2312 → latin-1
    """
    if not content:
        return ""

    # 尝试 BOM 头判断
    if content.startswith(b'\xef\xbb\xbf'):
        return content[3:].decode('utf-8', errors='ignore')
    if content.startswith(b'\xff\xfe') or content.startswith(b'\xfe\xff'):
        try:
            return content.decode('utf-16', errors='ignore')
        except Exception:
            pass

    # 按优先级尝试常见中文编码
    encodings = ['utf-8', 'gb18030', 'gbk', 'gb2312', 'big5', 'latin-1']
    for enc in encodings:
        try:
            decoded = content.decode(enc)
            # 验证解码质量：如果出现大量替换字符，说明编码不对
            if decoded.count('\ufffd') < len(decoded) * 0.05:
                return decoded
        except (UnicodeDecodeError, LookupError):
            continue

    # 最终兜底
    return content.decode('utf-8', errors='ignore')


# ---------------------------------------------------------------------------
# 3. 空白字符清理
# ---------------------------------------------------------------------------
def clean_whitespace(text: str) -> str:
    """清理多余的空白字符"""
    if not text:
        return ""

    # 统一换行符：各种换行符统一为 \n
    text = re.sub(r'\r\n|\r|\u2028|\u2029', '\n', text)

    # 制表符替换为空格
    text = text.replace('\t', ' ')

    # 全角空格转半角
    text = text.replace('\u3000', ' ')

    # 连续空格压缩为单个空格（保留缩进结构）
    text = re.sub(r'[ ]{2,}', ' ', text)

    # 连续空行压缩为最多两个换行（保留段落分隔）
    text = re.sub(r'\n{3,}', '\n\n', text)

    # 去除每行首尾空格
    lines = [line.strip() for line in text.split('\n')]
    text = '\n'.join(lines)

    # 去除首尾空白
    return text.strip()


# ---------------------------------------------------------------------------
# 4. 特殊字符过滤
# ---------------------------------------------------------------------------
def filter_special_chars(text: str) -> str:
    """过滤控制字符、零宽字符、特殊符号，保留正常内容"""
    if not text:
        return ""

    # 移除零宽字符（零宽空格、零宽连接符、零宽非连接符）
    text = re.sub(r'[\u200b\u200c\u200d\ufeff]', '', text)

    # 移除其他控制字符（保留换行 \n 和制表已转空格）
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]', '', text)

    # 移除 BOM
    text = text.replace('\ufeff', '')

    # 移除过多的重复标点（如 。。。。）
    text = re.sub(r'([。！？，、；：])\1{2,}', r'\1', text)

    return text


# ---------------------------------------------------------------------------
# 5. HTML 简历处理
# ---------------------------------------------------------------------------
def extract_text_from_html(html_content: str) -> str:
    """从 HTML 内容中提取纯文本"""
    if not html_content:
        return ""

    # 移除 script 和 style 标签及其内容
    html_content = re.sub(r'<script[^>]*>.*?</script>', '', html_content, flags=re.DOTALL | re.IGNORECASE)
    html_content = re.sub(r'<style[^>]*>.*?</style>', '', html_content, flags=re.DOTALL | re.IGNORECASE)

    # 将 <br>、<p>、<div> 等块级标签转为换行
    html_content = re.sub(r'<br\s*/?>', '\n', html_content, flags=re.IGNORECASE)
    html_content = re.sub(r'</(?:p|div|h[1-6]|li|tr)>', '\n', html_content, flags=re.IGNORECASE)

    # 移除所有 HTML 标签
    text = re.sub(r'<[^>]+>', '', html_content)

    # 解码 HTML 实体
    import html
    text = html.unescape(text)

    return text


# ---------------------------------------------------------------------------
# 6. 图片型 PDF 的 OCR 支持
# ---------------------------------------------------------------------------
def ocr_pdf(file_path: str) -> Optional[str]:
    """对图片型 PDF 执行 OCR 文字识别

    优先使用 pdf2image + pytesseract（需安装 Tesseract OCR）
    如果依赖不可用则返回 None
    """
    try:
        from pdf2image import convert_from_path
        import pytesseract
    except ImportError:
        logger.info("OCR 依赖未安装(pdf2image/pytesseract)，跳过 OCR")
        return None

    try:
        # 将 PDF 每页转为图片
        images = convert_from_path(file_path, dpi=200)
        texts = []
        for i, img in enumerate(images):
            # 中文 + 英文识别
            text = pytesseract.image_to_string(img, lang='chi_sim+eng')
            if text.strip():
                texts.append(text.strip())
            logger.info("OCR 第 %d/%d 页完成，提取 %d 字符", i + 1, len(images), len(text))

        return "\n".join(texts) if texts else None
    except Exception as e:
        logger.warning("OCR 处理失败: %s", e)
        return None


# ---------------------------------------------------------------------------
# 7. 文本分段
# ---------------------------------------------------------------------------
def segment_text(text: str, max_chunk_size: int = 3000) -> List[str]:
    """将长文本按段落分段，便于 LLM 解析

    策略：
        1. 优先按双换行分段
        2. 段落超过 max_chunk_size 时按单换行二次分割
        3. 仍超长时按 max_chunk_size 硬切
    """
    if not text:
        return []

    if len(text) <= max_chunk_size:
        return [text]

    # 1. 按双换行分段
    paragraphs = re.split(r'\n\s*\n', text)

    chunks: List[str] = []
    current_chunk = ""

    for para in paragraphs:
        # 如果当前段落本身就超长，二次分割
        if len(para) > max_chunk_size:
            # 先保存已积累的 chunk
            if current_chunk:
                chunks.append(current_chunk.strip())
                current_chunk = ""

            # 按单换行分割
            lines = para.split('\n')
            for line in lines:
                if len(current_chunk) + len(line) + 1 > max_chunk_size:
                    if current_chunk:
                        chunks.append(current_chunk.strip())
                    current_chunk = line
                    # 单行仍超长，硬切
                    while len(current_chunk) > max_chunk_size:
                        chunks.append(current_chunk[:max_chunk_size])
                        current_chunk = current_chunk[max_chunk_size:]
                else:
                    current_chunk += "\n" + line if current_chunk else line
        else:
            if len(current_chunk) + len(para) + 2 > max_chunk_size:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                current_chunk = para
            else:
                current_chunk += "\n\n" + para if current_chunk else para

    if current_chunk:
        chunks.append(current_chunk.strip())

    return [c for c in chunks if c]


# ---------------------------------------------------------------------------
# 8. 综合清洗入口
# ---------------------------------------------------------------------------
def clean_resume_text(raw_text: str) -> str:
    """对提取出的简历文本执行完整清洗流程

    步骤：空白清理 → 特殊字符过滤 → 长度截断
    """
    if not raw_text:
        return ""

    # 1. 空白字符清理
    text = clean_whitespace(raw_text)

    # 2. 特殊字符过滤
    text = filter_special_chars(text)

    # 3. 长度截断
    if len(text) > MAX_TEXT_LENGTH:
        logger.warning("简历文本过长(%d字符)，截断为 %d 字符", len(text), MAX_TEXT_LENGTH)
        text = text[:MAX_TEXT_LENGTH]

    return text


def parse_resume_with_fallback(
    file_path: str,
    filename: str,
    raw_bytes: Optional[bytes] = None,
) -> Tuple[str, str, bool]:
    """从文件提取文本，支持多格式 + OCR 兜底

    Args:
        file_path: 临时文件路径
        filename: 原始文件名
        raw_bytes: 原始字节内容（用于编码检测）

    Returns:
        (cleaned_text, source_type, used_ocr)
        - source_type: "txt" / "pdf" / "docx" / "html" / "ocr" / "fallback"
        - used_ocr: 是否使用了 OCR
    """
    ext = os.path.splitext(filename)[1].lower()

    # ---- TXT ----
    if ext == ".txt":
        if raw_bytes:
            text = detect_and_decode(raw_bytes)
        else:
            with open(file_path, "rb") as f:
                text = detect_and_decode(f.read())
        return clean_resume_text(text), "txt", False

    # ---- HTML ----
    if ext in (".html", ".htm"):
        if raw_bytes:
            raw = detect_and_decode(raw_bytes)
        else:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                raw = f.read()
        text = extract_text_from_html(raw)
        return clean_resume_text(text), "html", False

    # ---- PDF ----
    if ext == ".pdf":
        # 先尝试文本提取
        text = ""
        try:
            from PyPDF2 import PdfReader
            reader = PdfReader(file_path)
            for page in reader.pages:
                page_text = page.extract_text() or ""
                text += page_text + "\n"
        except ImportError:
            logger.warning("PyPDF2 未安装，PDF 文本提取不可用")
        except Exception as e:
            logger.warning("PDF 文本提取失败: %s", e)

        text = clean_resume_text(text)

        # 如果提取文本太少（可能是扫描件），尝试 OCR
        if len(text) < 50:
            logger.info("PDF 文本过少(%d字符)，可能为扫描件，尝试 OCR", len(text))
            ocr_text = ocr_pdf(file_path)
            if ocr_text:
                cleaned = clean_resume_text(ocr_text)
                if len(cleaned) > len(text):
                    return cleaned, "ocr", True

        return text, "pdf", False

    # ---- DOCX ----
    if ext == ".docx":
        text = ""
        try:
            from docx import Document
            doc = Document(file_path)
            # 段落文本
            texts = [p.text for p in doc.paragraphs if p.text]
            # 表格文本
            for table in doc.tables:
                for row in table.rows:
                    for cell in row.cells:
                        if cell.text:
                            texts.append(cell.text)
            text = "\n".join(texts)
        except ImportError:
            logger.warning("python-docx 未安装，DOCX 解析不可用")
        except Exception as e:
            logger.warning("DOCX 解析失败: %s", e)

        return clean_resume_text(text), "docx", False

    # ---- DOC (旧格式二进制) ----
    if ext == ".doc":
        # 尝试用 antiword 或直接读取
        try:
            import subprocess
            result = subprocess.run(
                ["antiword", file_path],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                return clean_resume_text(result.stdout), "doc", False
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.warning("antiword 解析 DOC 失败: %s", e)

        # 兜底：直接读取
        if raw_bytes:
            text = detect_and_decode(raw_bytes)
        else:
            with open(file_path, "rb") as f:
                text = detect_and_decode(f.read())
        return clean_resume_text(text), "doc", False

    # ---- 其他格式兜底 ----
    try:
        if raw_bytes:
            text = detect_and_decode(raw_bytes)
        else:
            with open(file_path, "rb") as f:
                text = detect_and_decode(f.read())
        return clean_resume_text(text), "fallback", False
    except Exception:
        return "", "fallback", False
