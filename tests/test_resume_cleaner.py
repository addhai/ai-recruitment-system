# -*- coding: utf-8 -*-
"""简历清洗模块单测：格式校验、编码检测、空白清理、特殊字符过滤、HTML 提取、分段、多格式解析兜底。"""
import os
import tempfile

import pytest

from src.services.resume_cleaner import (
    validate_resume_file,
    detect_and_decode,
    clean_whitespace,
    filter_special_chars,
    extract_text_from_html,
    segment_text,
    clean_resume_text,
    parse_resume_with_fallback,
    MAX_TEXT_LENGTH,
    SUPPORTED_EXTENSIONS,
)


# ---------------------------------------------------------------------------
# 格式校验
# ---------------------------------------------------------------------------
def test_validate_ok():
    is_valid, reason, ext = validate_resume_file("简历.pdf", 1024)
    assert is_valid and ext == ".pdf" and reason == ""


def test_validate_empty_filename():
    is_valid, reason, _ = validate_resume_file("", 100)
    assert not is_valid and "文件名为空" in reason


def test_validate_unsupported_ext():
    is_valid, reason, ext = validate_resume_file("photo.exe", 100)
    assert not is_valid and ext == ".exe" and "不支持的文件格式" in reason


def test_validate_empty_file():
    is_valid, reason, _ = validate_resume_file("a.txt", 0)
    assert not is_valid and "文件为空" in reason


def test_validate_too_large():
    is_valid, reason, _ = validate_resume_file("a.txt", 11 * 1024 * 1024)
    assert not is_valid and "文件过大" in reason


# ---------------------------------------------------------------------------
# 编码检测
# ---------------------------------------------------------------------------
def test_decode_utf8_with_bom():
    raw = "张三 后端工程师".encode("utf-8")
    assert detect_and_decode(b"\xef\xbb\xbf" + raw) == "张三 后端工程师"


def test_decode_utf16_le_bom():
    raw = "张三".encode("utf-16-le")
    assert detect_and_decode(b"\xff\xfe" + raw) == "张三"


def test_decode_gbk():
    assert detect_and_decode("张三的简历".encode("gbk")) == "张三的简历"


def test_decode_empty_bytes():
    assert detect_and_decode(b"") == ""


def test_decode_latin1_fallback():
    # 构造 UTF-8/GBK 都解不了的字节，最终 latin-1 必然成功
    weird = bytes([0x81, 0x82, 0x83])
    out = detect_and_decode(weird)
    assert isinstance(out, str) and len(out) == 3


# ---------------------------------------------------------------------------
# 空白清理
# ---------------------------------------------------------------------------
def test_clean_whitespace_normalizes():
    text = "姓名：张三\r\n技能：Python\tJava\u3000Go\n\n\n\n经历"
    out = clean_whitespace(text)
    assert "\r" not in out and "\t" not in out and "\u3000" not in out
    assert "\n\n\n" not in out  # 连续空行压缩
    assert out.startswith("姓名")


def test_clean_whitespace_empty():
    assert clean_whitespace("") == ""
    assert clean_whitespace(None) == ""


def test_clean_whitespace_collapses_spaces():
    assert clean_whitespace("a    b") == "a b"


# ---------------------------------------------------------------------------
# 特殊字符过滤
# ---------------------------------------------------------------------------
def test_filter_removes_zero_width():
    text = "张\u200b三\u200c的\u200d简\ufeff历"
    assert filter_special_chars(text) == "张三的简历"


def test_filter_removes_control_chars():
    text = "abc\x00\x01\x02def\x7f"
    assert filter_special_chars(text) == "abcdef"


def test_filter_collapses_repeated_punct():
    assert filter_special_chars("很好。。。。") == "很好。"
    assert filter_special_chars("真的！！！") == "真的！"


def test_filter_empty():
    assert filter_special_chars("") == ""
    assert filter_special_chars(None) == ""


# ---------------------------------------------------------------------------
# HTML 提取
# ---------------------------------------------------------------------------
def test_html_extracts_text():
    html = "<html><body><h1>张三</h1><p>Python 工程师</p><br>五年经验</body></html>"
    text = extract_text_from_html(html)
    assert "张三" in text and "Python 工程师" in text and "五年经验" in text
    assert "<" not in text


def test_html_removes_script_style():
    html = "<style>.a{color:red}</style><script>alert(1)</script><p>正文</p>"
    text = extract_text_from_html(html)
    assert "alert" not in text and "color" not in text and "正文" in text


def test_html_decodes_entities():
    # </p> 会转换为换行（块级标签行为），strip 后比对
    assert extract_text_from_html("<p>A &amp; B</p>").strip() == "A & B"


def test_html_empty():
    assert extract_text_from_html("") == ""
    assert extract_text_from_html(None) == ""


# ---------------------------------------------------------------------------
# 文本分段
# ---------------------------------------------------------------------------
def test_segment_short_text_single_chunk():
    assert segment_text("短文本", max_chunk_size=100) == ["短文本"]


def test_segment_empty():
    assert segment_text("") == []


def test_segment_by_paragraph():
    text = "第一段\n\n第二段\n\n第三段"
    chunks = segment_text(text, max_chunk_size=4)
    assert len(chunks) == 3
    assert chunks[0] == "第一段"


def test_segment_hard_cut_single_line():
    # 单行超长无换行，必须硬切
    text = "字" * 100
    chunks = segment_text(text, max_chunk_size=30)
    assert len(chunks) == 4
    assert all(len(c) <= 30 for c in chunks)
    assert "".join(chunks) == text


def test_segment_long_para_split_by_line():
    # 段落超长但含换行，按单换行二次分割
    text = "行一\n行二\n行三"
    chunks = segment_text(text, max_chunk_size=5)
    assert len(chunks) >= 2
    assert all(len(c) <= 5 for c in chunks)


# ---------------------------------------------------------------------------
# 综合清洗
# ---------------------------------------------------------------------------
def test_clean_resume_text_truncates():
    out = clean_resume_text("字" * (MAX_TEXT_LENGTH + 100))
    assert len(out) == MAX_TEXT_LENGTH


def test_clean_resume_text_empty():
    assert clean_resume_text("") == ""
    assert clean_resume_text(None) == ""


# ---------------------------------------------------------------------------
# 多格式解析兜底（parse_resume_with_fallback）
# ---------------------------------------------------------------------------
def _tmp_file(content: bytes, suffix: str) -> str:
    fd, path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "wb") as f:
        f.write(content)
    return path


def test_parse_txt_with_raw_bytes():
    text, source, used_ocr = parse_resume_with_fallback(
        "/nonexistent/x.txt", "resume.txt", raw_bytes="张三 Python 五年".encode("utf-8")
    )
    assert source == "txt" and not used_ocr
    assert "张三" in text and "Python" in text


def test_parse_txt_reads_file_when_no_bytes():
    path = _tmp_file("李四 Java 三年".encode("utf-8"), ".txt")
    try:
        text, source, _ = parse_resume_with_fallback(path, "a.txt", raw_bytes=None)
        assert "李四" in text and source == "txt"
    finally:
        os.remove(path)


def test_parse_html():
    html = "<html><body><p>王五 全栈工程师</p></body></html>".encode("utf-8")
    text, source, _ = parse_resume_with_fallback("/x.html", "resume.html", raw_bytes=html)
    assert source == "html" and "王五" in text and "<p>" not in text


def test_parse_html_reads_file_when_no_bytes():
    path = _tmp_file("<p>赵六 前端工程师</p>".encode("utf-8"), ".html")
    try:
        text, source, _ = parse_resume_with_fallback(path, "a.html", raw_bytes=None)
        assert source == "html" and "赵六" in text
    finally:
        os.remove(path)


def test_parse_doc_fallback_binary_read():
    # antiword 不存在 → 直接按字节解码兜底
    path = _tmp_file("孙七 测试工程师".encode("utf-8"), ".doc")
    try:
        text, source, _ = parse_resume_with_fallback(path, "a.doc", raw_bytes=None)
        assert source == "doc" and "孙七" in text
    finally:
        os.remove(path)


def test_parse_unknown_ext_fallback():
    text, source, _ = parse_resume_with_fallback(
        "/x.log", "a.log", raw_bytes="周九 运维".encode("utf-8")
    )
    assert source == "fallback" and "周九" in text


def test_parse_pdf_without_pypdf2(monkeypatch):
    # 模拟 PyPDF2 不可用 → 文本为空且 OCR 依赖也不可用 → 返回空文本 + source=pdf
    import builtins

    real_import = builtins.__import__

    def _blocked(name, *args, **kwargs):
        if name in ("PyPDF2", "pdf2image", "pytesseract"):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocked)
    path = _tmp_file(b"%PDF-1.4 fake", ".pdf")
    try:
        text, source, used_ocr = parse_resume_with_fallback(path, "a.pdf", raw_bytes=None)
        assert source == "pdf" and not used_ocr
        assert text == ""
    finally:
        os.remove(path)


def test_parse_docx_without_python_docx(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def _blocked(name, *args, **kwargs):
        if name == "docx":
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocked)
    path = _tmp_file(b"PK\x03\x04 fake-docx", ".docx")
    try:
        text, source, _ = parse_resume_with_fallback(path, "a.docx", raw_bytes=None)
        assert source == "docx" and text == ""
    finally:
        os.remove(path)
