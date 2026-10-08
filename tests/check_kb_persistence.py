"""验证知识库自定义文档持久化（模拟重启）。"""
import os
import sys

sys.path.insert(0, ".")

from src.models.database import ensure_tables, SessionLocal, KnowledgeDocument

ensure_tables()

# 清理旧测试数据
db = SessionLocal()
db.query(KnowledgeDocument).filter(KnowledgeDocument.title.like("持久化验证%")).delete(
    synchronize_session=False)
db.commit()
db.close()

import src.rag.knowledge_base as kb

print("=== 1. 添加文档 ===")
doc_id = kb.add_document("持久化验证文档", "这是一条用于验证重启后不丢失的自定义文档内容。")
print(f"  文档 id={doc_id}")
print(f"  内存缓存条数: {len(kb._extra_documents)}")

print("\n=== 2. 模拟重启（清空进程内缓存）===")
kb._extra_documents = []
kb._init_attempted = False
kb._vector_store = None
kb._bm25_retriever = None
print(f"  清空后缓存条数: {len(kb._extra_documents)}")

print("\n=== 3. 重新初始化（应自动从数据库恢复）===")
kb.init_knowledge_base()
titles = [d["title"] for d in kb._extra_documents]
print(f"  恢复后缓存条数: {len(kb._extra_documents)}")
print(f"  文档标题: {titles}")

found = any(t.startswith("持久化验证") for t in titles)
print(f"\n  结果: {'通过 —— 文档在重启后仍存在' if found else '失败 —— 文档丢失'}")

print("\n=== 4. 删除文档 ===")
ok = kb.delete_document(doc_id)
print(f"  delete_document 返回: {ok}")
print(f"  删除后缓存条数: {len(kb._extra_documents)}")

db = SessionLocal()
left = db.query(KnowledgeDocument).filter(KnowledgeDocument.title.like("持久化验证%")).count()
db.close()
print(f"  库中残留: {left} 条")

print("\n=== 5. 校验向量库备份逻辑（不应再静默 rmtree）===")
import inspect
src = inspect.getsource(kb._reset_vector_dir)
print(f"  含 os.rename 备份: {'os.rename' in src}")
print(f"  含 warning 日志: {'logger.warning' in src}")

sys.exit(0 if found and left == 0 else 1)
