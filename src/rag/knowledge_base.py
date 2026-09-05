"""人事制度知识库：BM25 + 向量 Ensemble 混合检索 + LLM 生成回答。

设计要点：
1. embedding 与 chat LLM 配置分离（DeepSeek 无 embedding 接口，默认走硅基流动 bge-large-zh）；
2. embedding/LLM 任一不可用都降级：BM25 关键词检索 + 模板答案，功能不中断；
3. 向量库维度随 embedding 模型变化，检测到旧维度数据自动重建，避免维度冲突报错；
4. 用户通过 add_document 添加的文档同时进入向量库和 BM25 索引（修复旧版重建丢文档问题）；
5. 查询入口提供 async 版本，FastAPI 路由通过线程池调用，不阻塞事件循环。
"""
import asyncio
import os
import shutil
from typing import List, Dict, Any, Optional

from langchain_openai import ChatOpenAI, OpenAIEmbeddings
try:
    from langchain_text_splitters import RecursiveCharacterTextSplitter
except ImportError:
    from langchain.text_splitter import RecursiveCharacterTextSplitter
try:
    from langchain_community.vectorstores import Chroma
except ImportError:
    from langchain_chroma import Chroma
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import StrOutputParser
# 注意：langchain 1.x 已移除 EnsembleRetriever，langchain-community 也在 sunset。
# 这里只取 BM25Retriever（关键词检索），向量+关键词的混合合并在本模块内自行实现，避免版本碎片。
try:
    from langchain_community.retrievers import BM25Retriever
except ImportError:
    BM25Retriever = None
try:
    import jieba
except ImportError:
    jieba = None


def _tokenize(text: str) -> List[str]:
    """中文分词：jieba 优先；缺失时退化为字符 bi-gram，保证 BM25 不因整句成词而失效"""
    if jieba is not None:
        return [t for t in jieba.lcut(text) if t.strip()]
    chars = [c for c in text if c.strip()]
    return ["".join(chars[i:i + 2]) for i in range(len(chars) - 1)] or chars

from src.config import settings

PERSIST_DIR = os.path.join(os.path.dirname(__file__), "../data/chroma")
COLLECTION_NAME = "hr_knowledge"

system_documents = [
    {
        "title": "员工入职流程",
        "content": """员工入职流程说明：

1. 入职前准备（HR负责）：
   - 发送录用通知书
   - 收集入职材料（身份证复印件、学历证明、离职证明等）
   - 安排办公座位和设备
   - 创建员工邮箱和账号

2. 入职当天流程：
   - 9:00-9:30 报到登记，领取工牌
   - 9:30-10:30 HR入职培训（公司制度、企业文化）
   - 10:30-11:30 IT部门设备配置（电脑、软件安装）
   - 11:30-12:00 部门负责人接待
   - 14:00-17:00 部门内部培训和岗位介绍

3. 试用期规定：
   - 试用期为1-3个月，根据岗位性质确定
   - 试用期工资为正式工资的80%
   - 试用期考核通过后转为正式员工

4. 注意事项：
   - 入职前需完成体检
   - 需签订劳动合同和保密协议
   - 遵守公司各项规章制度"""
    },
    {
        "title": "员工福利制度",
        "content": """员工福利制度：

1. 社会保险：
   - 按照国家规定缴纳五险一金
   - 额外缴纳补充商业保险

2. 带薪休假：
   - 年假：入职满1年享受5天，每增加1年增加1天，最多15天
   - 病假：每年15天带薪病假
   - 婚假：3天，晚婚额外增加7天
   - 产假：98天，符合条件可延长

3. 其他福利：
   - 年度体检：每年一次免费体检
   - 餐补：每月300元餐补
   - 交通补贴：每月200元
   - 节日礼品：春节、中秋等节日发放礼品
   - 团建活动：每季度组织一次团建

4. 福利申请流程：
   - 填写福利申请表
   - 部门负责人审批
   - HR部门审核发放"""
    },
    {
        "title": "绩效考核制度",
        "content": """绩效考核制度：

1. 考核周期：
   - 月度考核：每月进行一次，评估日常工作表现
   - 季度考核：每季度进行一次，评估阶段性成果
   - 年度考核：每年进行一次，综合评估全年表现

2. 考核维度：
   - 工作业绩（40%）
   - 工作态度（20%）
   - 团队协作（20%）
   - 创新能力（10%）
   - 职业素养（10%）

3. 考核等级：
   - S级：卓越（90分以上）
   - A级：优秀（80-89分）
   - B级：良好（70-79分）
   - C级：合格（60-69分）
   - D级：不合格（60分以下）

4. 考核结果应用：
   - S级：发放150%绩效奖金，优先晋升机会
   - A级：发放120%绩效奖金
   - B级：发放100%绩效奖金
   - C级：发放80%绩效奖金
   - D级：不发放绩效奖金，进行绩效改进计划

5. 考核申诉：
   - 对考核结果有异议可在3个工作日内提出申诉
   - HR部门进行复核并给出最终结果"""
    },
    {
        "title": "员工培训制度",
        "content": """员工培训制度：

1. 培训类型：
   - 新员工入职培训：为期1周，涵盖公司制度、文化、岗位技能
   - 在岗培训：由部门负责人或资深员工进行指导
   - 专项培训：针对特定技能或项目的培训
   - 外部培训：参加行业会议、培训课程等

2. 培训计划：
   - 年度培训计划：每年初制定，覆盖全员
   - 部门培训计划：各部门根据需求制定
   - 个人发展计划：根据员工职业规划制定

3. 培训费用：
   - 公司承担培训费用
   - 培训后需在公司服务满1年，否则需赔偿培训费用

4. 培训考核：
   - 培训结束后进行考核
   - 考核结果计入员工档案
   - 未通过考核需重新培训"""
    },
    {
        "title": "招聘流程规范",
        "content": """招聘流程规范：

1. 招聘需求提交：
   - 用人部门提交招聘需求表
   - 明确岗位要求、人数、到岗时间
   - HR部门审核需求合理性

2. 简历筛选：
   - HR初步筛选简历
   - 用人部门进行技术筛选
   - AI辅助简历解析和匹配度评估

3. 面试流程：
   - 第一轮：技术面试（1小时）
   - 第二轮：综合面试（1小时）
   - 第三轮：HR面试（30分钟）

4. 录用决策：
   - 综合评估各轮面试结果
   - 确定薪资和入职时间
   - 发送录用通知书

5. 招聘周期：
   - 一般岗位：2-4周
   - 关键岗位：4-8周

6. 招聘渠道：
   - 内部推荐
   - 招聘网站（BOSS直聘、拉勾等）
   - 校园招聘
   - 猎头合作"""
    },
    {
        "title": "薪酬管理制度",
        "content": """薪酬管理制度：

1. 薪酬构成：
   - 基本工资：根据岗位等级确定
   - 绩效奖金：根据考核结果发放
   - 年终奖金：根据公司业绩和个人表现发放
   - 专项奖金：项目奖、创新奖等

2. 薪资等级：
   - 管理岗：M1-M8
   - 技术岗：T1-T8
   - 职能岗：F1-F6

3. 调薪规定：
   - 年度调薪：根据绩效和市场情况调整
   - 晋升调薪：晋升时进行薪资调整
   - 特殊调薪：根据个人表现和市场竞争力调整

4. 薪资保密：
   - 员工不得泄露个人薪资信息
   - 不得询问他人薪资
   - 违反规定将受到处罚

5. 发薪日期：
   - 每月15日发放上月工资
   - 遇节假日提前发放"""
    }
]

# 用户通过 API 添加的自定义文档（内存保留；向量库持久化到 Chroma）
_extra_documents: List[Dict[str, str]] = []

_vector_store: Optional[Chroma] = None
_bm25_retriever = None
_qa_chain: Optional[Any] = None
_chat_llm: Optional[ChatOpenAI] = None
_init_attempted = False


def _build_embeddings() -> Optional[OpenAIEmbeddings]:
    """构建 embedding 客户端；无 key 时返回 None（调用方降级为纯 BM25）"""
    if not settings.embedding_api_key:
        return None
    return OpenAIEmbeddings(
        model=settings.EMBEDDING_MODEL,
        api_key=settings.embedding_api_key,
        base_url=settings.EMBEDDING_API_BASE,
        check_embedding_ctx_length=False,
    )


def _build_chat_llm() -> Optional[ChatOpenAI]:
    if not settings.LLM_API_KEY:
        return None
    return ChatOpenAI(
        model=settings.LLM_MODEL,
        temperature=0.3,
        timeout=45,
        max_retries=2,
        api_key=settings.LLM_API_KEY,
        base_url=settings.LLM_API_BASE,
    )


def _all_documents() -> List[Dict[str, str]]:
    return system_documents + _extra_documents


def _split_docs():
    docs = _all_documents()
    splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50, length_function=len)
    return splitter.create_documents(
        [d["content"] for d in docs],
        metadatas=[{"title": d["title"], "source": "system" if d in system_documents else "custom"} for d in docs],
    )


def _reset_vector_dir():
    """维度不匹配等场景下清空旧向量库目录"""
    if os.path.isdir(PERSIST_DIR):
        shutil.rmtree(PERSIST_DIR, ignore_errors=True)
    os.makedirs(PERSIST_DIR, exist_ok=True)


def init_knowledge_base(force: bool = False) -> bool:
    """初始化检索链。返回向量检索是否可用（False 表示降级为 BM25/模板）。

    force=True 时强制重建（添加自定义文档后调用）。
    """
    global _vector_store, _bm25_retriever, _qa_chain, _chat_llm, _init_attempted
    if _init_attempted and not force:
        return _vector_store is not None
    _init_attempted = True

    _chat_llm = _build_chat_llm()
    embeddings = _build_embeddings()
    split_docs = _split_docs()

    # ---- 向量库：旧维度数据自动重建；force 时全量重建（保证自定义文档进入且无重复）----
    if embeddings is not None:
        os.makedirs(PERSIST_DIR, exist_ok=True)
        try:
            if force:
                try:
                    Chroma(collection_name=COLLECTION_NAME, embedding_function=embeddings,
                           persist_directory=PERSIST_DIR).delete_collection()
                except Exception:
                    _reset_vector_dir()
                _vector_store = Chroma.from_documents(
                    documents=split_docs, embedding=embeddings,
                    collection_name=COLLECTION_NAME, persist_directory=PERSIST_DIR,
                )
            else:
                _vector_store = Chroma(
                    collection_name=COLLECTION_NAME,
                    embedding_function=embeddings,
                    persist_directory=PERSIST_DIR,
                )
                try:
                    if _vector_store._collection.count() > 0:
                        # 探测：旧库维度与当前 embedding 模型不一致时查询会报错
                        _vector_store.similarity_search("探测", k=1)
                    else:
                        _vector_store.add_documents(split_docs)
                except Exception:
                    print("[knowledge_base] 向量库维度不匹配或数据损坏，重建索引...")
                    try:
                        _vector_store.delete_collection()
                    except Exception:
                        _reset_vector_dir()
                    _vector_store = Chroma.from_documents(
                        documents=split_docs, embedding=embeddings,
                        collection_name=COLLECTION_NAME, persist_directory=PERSIST_DIR,
                    )
        except Exception as e:
            print(f"[knowledge_base] 向量库初始化失败，降级为 BM25 检索: {type(e).__name__}: {str(e)[:200]}")
            _vector_store = None
    else:
        _vector_store = None

    # ---- BM25 关键词检索（内存，始终包含系统+自定义文档）----
    if BM25Retriever is not None:
        try:
            _bm25_retriever = BM25Retriever.from_documents(split_docs, preprocess_func=_tokenize)
            _bm25_retriever.k = 4
        except Exception as e:
            print(f"[knowledge_base] BM25 初始化失败: {type(e).__name__}: {e}")
            _bm25_retriever = None

    # ---- LLM 问答链（context 由 _retrieve_docs 混合检索后注入；无 chat LLM 时走模板兜底）----
    if _chat_llm is not None and (_bm25_retriever is not None or _vector_store is not None):
        prompt = PromptTemplate(
            template="""你是企业人事制度知识库助手，请严格根据提供的上下文信息回答问题。

上下文信息：
{context}

问题：
{question}

要求：
1. 只依据上下文回答，上下文没有的内容明确说"知识库中暂无相关规定"，禁止编造；
2. 回答准确、简洁，涉及数字（天数、比例、日期）必须与上下文一致；
3. 用中文回答。""",
            input_variables=["context", "question"]
        )
        _qa_chain = prompt | _chat_llm | StrOutputParser()
    else:
        _qa_chain = None

    if _vector_store is not None:
        print("[knowledge_base] 检索模式：向量 + BM25 混合检索")
    elif _bm25_retriever is not None:
        print("[knowledge_base] 检索模式：BM25 关键词检索（embedding 未配置或不可用）")
    else:
        print("[knowledge_base] 检索模式：仅规则模板（检索器均不可用）")

    return _vector_store is not None


def _doc_key(doc) -> str:
    return f"{doc.metadata.get('title', '')}|{doc.page_content[:60]}"


def _retrieve_docs(query: str, k: int = 4) -> List[Any]:
    """混合检索：BM25 关键词命中在前（精确匹配权重高），向量语义结果补充，按内容去重。"""
    merged: List[Any] = []
    seen = set()

    def _add(docs):
        for d in docs:
            key = _doc_key(d)
            if key not in seen:
                seen.add(key)
                merged.append(d)

    if _bm25_retriever is not None:
        try:
            _add(_bm25_retriever.invoke(query))
        except Exception as e:
            print(f"[knowledge_base] BM25 检索异常: {type(e).__name__}: {str(e)[:100]}")
    if _vector_store is not None:
        try:
            _add(_vector_store.similarity_search(query, k=k))
        except Exception as e:
            print(f"[knowledge_base] 向量检索异常: {type(e).__name__}: {str(e)[:100]}")
    return merged[:k]


def _format_docs(docs: List[Any]) -> str:
    return "\n\n".join(f"【{d.metadata.get('title', '未知')}】\n{d.page_content}" for d in docs)


def keyword_search(query: str, top_k: int = 3) -> List[Dict[str, Any]]:
    query_lower = query.lower()

    keywords = []
    if " " in query_lower:
        keywords = [k for k in query_lower.split() if len(k) > 0]
    else:
        keywords = [query_lower]
        for keyword in ["入职", "年假", "福利", "绩效", "考核", "试用", "转正", "薪酬", "工资", "薪资",
                        "培训", "学习", "发展", "招聘", "面试", "录用", "休假", "病假", "婚假", "产假",
                        "KPI", "评级", "调薪", "入职流程", "新员工", "报到"]:
            if keyword in query_lower and keyword not in keywords:
                keywords.append(keyword)

    scores = []
    for idx, doc in enumerate(_all_documents()):
        title_score = 0
        content_score = 0
        for keyword in keywords:
            if keyword in doc["title"].lower():
                title_score += 3
            if keyword in doc["content"].lower():
                content_score += 1
        total_score = title_score + content_score
        if total_score > 0:
            scores.append((idx, total_score))
    scores.sort(key=lambda x: x[1], reverse=True)
    results = []
    for idx, _ in scores[:top_k]:
        doc = _all_documents()[idx]
        results.append({
            "title": doc["title"],
            "content": doc["content"][:300],
            "page_content": doc["content"],
            "metadata": {"title": doc["title"]}
        })
    return results


def fallback_answer(query: str) -> Dict[str, Any]:
    results = keyword_search(query)
    if not results:
        return {
            "answer": "抱歉，我暂时无法回答您的问题。您可以尝试询问以下话题：\n- 员工入职流程\n- 年假/福利制度\n- 绩效考核\n- 试用期规定\n- 薪酬管理",
            "sources": [],
            "mode": "fallback"
        }

    context = "\n\n".join([f"【{r['title']}】\n{r['content']}" for r in results])
    answer_parts = []

    if any(k in query for k in ["入职", "新员工", "报到"]):
        answer_parts.append("员工入职流程主要包括以下几个步骤：\n\n1. **入职前准备**：HR发送录用通知书，收集入职材料（身份证复印件、学历证明、离职证明等），安排办公座位和设备，创建员工邮箱和账号。\n\n2. **入职当天**：\n   - 9:00-9:30 报到登记，领取工牌\n   - 9:30-10:30 HR入职培训（公司制度、企业文化）\n   - 10:30-11:30 IT部门设备配置（电脑、软件安装）\n   - 11:30-12:00 部门负责人接待\n   - 14:00-17:00 部门内部培训\n\n3. **试用期**：1-3个月，根据岗位性质确定。")
    elif any(k in query for k in ["年假", "休假", "福利", "病假", "婚假", "产假"]):
        answer_parts.append("关于带薪休假的规定如下：\n\n- **年假**：入职满1年享受5天，每增加1年增加1天，最多15天\n- **病假**：每年15天带薪病假\n- **婚假**：3天，晚婚额外增加7天\n- **产假**：98天，符合条件可延长\n\n此外还有年度体检、餐补、交通补贴等福利。")
    elif any(k in query for k in ["绩效", "考核", "评级", "KPI"]):
        answer_parts.append("绩效考核制度如下：\n\n**考核周期**：\n- 月度考核：每月进行一次\n- 季度考核：每季度进行一次\n- 年度考核：每年进行一次\n\n**考核维度**：\n- 工作业绩（40%）、工作态度（20%）、团队协作（20%）、创新能力（10%）、职业素养（10%）\n\n**考核等级**：S级（卓越）、A级（优秀）、B级（良好）、C级（合格）、D级（不合格）")
    elif any(k in query for k in ["试用", "转正"]):
        answer_parts.append("试用期规定如下：\n\n- 试用期一般为1-3个月，根据岗位性质确定\n- 试用期工资为正式工资的80%\n- 试用期考核通过后转为正式员工\n- 入职前需完成体检\n- 需签订劳动合同和保密协议")
    elif any(k in query for k in ["薪酬", "工资", "薪资", "调薪"]):
        answer_parts.append("薪酬管理制度：\n\n**薪酬构成**：基本工资、绩效奖金、年终奖金、专项奖金\n\n**薪资等级**：管理岗M1-M8、技术岗T1-T8、职能岗F1-F6\n\n**发薪日期**：每月15日发放上月工资，遇节假日提前发放。")
    elif any(k in query for k in ["培训", "学习", "发展"]):
        answer_parts.append("员工培训制度：\n\n**培训类型**：新员工入职培训（1周）、在岗培训、专项培训、外部培训\n\n**培训费用**：公司承担，培训后需在公司服务满1年，否则需赔偿培训费用。")
    elif any(k in query for k in ["招聘", "面试", "录用"]):
        answer_parts.append("招聘流程规范：\n\n1. 用人部门提交招聘需求，HR审核\n2. 简历筛选：HR初筛 + 用人部门技术筛选 + AI辅助匹配\n3. 面试流程：技术面试 → 综合面试 → HR面试\n4. 录用决策：综合评估，确定薪资，发送录用通知书\n\n**招聘周期**：一般岗位2-4周，关键岗位4-8周。")
    else:
        answer_parts.append(f"根据知识库检索，以下文档可能与您的问题相关：\n\n{context}")

    return {
        "answer": "\n\n".join(answer_parts),
        "sources": [{"title": r["title"], "content": r["content"]} for r in results],
        "mode": "fallback"
    }


def _query_sync(query: str) -> Dict[str, Any]:
    vector_ok = init_knowledge_base()
    if _qa_chain is None:
        return fallback_answer(query)

    try:
        docs = _retrieve_docs(query)
        if not docs:
            # 检索器都没命中：交模板兜底，避免 LLM 无依据编造
            return fallback_answer(query)
        answer = _qa_chain.invoke({"context": _format_docs(docs), "question": query})
        sources = [
            {"title": doc.metadata.get("title", "未知"), "content": doc.page_content[:200]}
            for doc in docs
        ]
        return {
            "answer": answer if isinstance(answer, str) else str(answer),
            "sources": sources,
            "mode": "hybrid_rag" if vector_ok else "bm25_llm",
        }
    except Exception as e:
        print(f"[knowledge_base] RAG 查询失败，降级模板回答: {type(e).__name__}: {str(e)[:200]}")
        return fallback_answer(query)


async def query_knowledge_base_async(query: str) -> Dict[str, Any]:
    """异步入口：线程池执行检索+LLM，避免阻塞 FastAPI 事件循环"""
    return await asyncio.to_thread(_query_sync, query)


def query_knowledge_base(query: str) -> Dict[str, Any]:
    """同步入口（测试/脚本使用）"""
    return _query_sync(query)


def add_document(title: str, content: str) -> None:
    """添加自定义文档：同时进入向量库与 BM25 索引，并持久化到 Chroma"""
    _extra_documents.append({"title": title, "content": content})
    # 强制重建：split_docs 已包含新文档，BM25 全量重建，向量库增量写入
    init_knowledge_base(force=True)


def get_all_documents() -> List[Dict[str, str]]:
    return _all_documents()


# 模块加载时不自动初始化（避免无配置时启动失败）；首次查询时延迟初始化
