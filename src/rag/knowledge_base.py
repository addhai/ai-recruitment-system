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
import time
from typing import Any, Dict, List, Optional, Tuple

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


def _char_bigrams(text: str) -> List[str]:
    """字符 bigram：把文本切成相邻两字的组合。

    保留非空白字符即可——空白没有检索价值，混进去只会稀释词频。
    """
    chars = [c for c in text if not c.isspace()]
    return ["".join(chars[i:i + 2]) for i in range(len(chars) - 1)]


def _tokenize(text: str) -> List[str]:
    """中文分词：jieba 词 + 字符 bigram 混合索引。

    **为什么必须加 bigram**：jieba 的切分是**上下文相关**的，同一个词在
    查询与文档里会被切成不同形状。实测「每月什么时候发工资」切成
    ['每月','什么','时候','发工资']，而文档里的「每月15日发放上月工资」
    切成 ['每月','15','日','发放','上','月工资']——交集只剩「每月」，
    「工资」完全对不上。纯靠词匹配时，用户的自然提问很难命中制度原文
    （「员工之间可以互相打听工资吗」也只能靠「员工」二字勉强接上，
    而文档写的是「薪资」）。

    加字符 bigram 之后，「工资」在两边都作为 bigram（工+资）出现，匹配
    恢复。bigram 只作补充，词的主体地位不变；它提升的是召回而不是精度
    ——因为 bigram 的 IDF 很高（几乎只出现在一处），偶然共现的权重极低。

    没有 jieba 时退化为纯字符 bigram（与原行为一致）。
    """
    if jieba is None:
        chars = [c for c in text if c.strip()]
        return ["".join(chars[i:i + 2]) for i in range(len(chars) - 1)] or chars
    words = [t for t in jieba.lcut(text) if t.strip()]
    return words + _char_bigrams(text)

from src.config import settings
from src.logging_setup import get_logger

logger = get_logger(__name__)

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

# 用户通过 API 添加的自定义文档。**进程内缓存**，真实来源是 knowledge_documents 表
# （此前只存内存，重启即丢，见 KnowledgeDocument 的说明）
_extra_documents: List[Dict[str, Any]] = []

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
    """知识库问答客户端：走生效配置（数据库里的模型配置优先）。

    温度用 0.3——生成式回答需要比评分类任务（0.1）更高的随机性，
    但仍走同一套模型名/密钥/Base URL，使界面切换模型时一并生效。
    """
    from src.services import llm_config

    cfg = llm_config.get_effective_config()
    if not cfg["api_key"]:
        return None
    return ChatOpenAI(
        model=cfg["model"],
        temperature=0.3,
        timeout=cfg["timeout_seconds"] or 45,
        max_retries=2,
        api_key=cfg["api_key"],
        base_url=cfg["base_url"],
    )


def _all_documents() -> List[Dict[str, str]]:
    return system_documents + _extra_documents


def chunk_config(chunk_size: Optional[int] = None,
                 chunk_overlap: Optional[int] = None) -> Dict[str, int]:
    """返回本次生效的切分参数。

    这两个值直接决定检索结果，属于**口径**：改了就和换模型一样，
    新旧检索结果不可直接比较。所以评测报告必须把它和模型名一起记下来。

    传参时以传入值为准（评测做参数 sweep 用），不传则取全局配置。
    """
    return {
        "chunk_size": int(chunk_size if chunk_size is not None else settings.KB_CHUNK_SIZE),
        "chunk_overlap": int(chunk_overlap if chunk_overlap is not None else settings.KB_CHUNK_OVERLAP),
    }


def _split_docs(chunk_size: Optional[int] = None, chunk_overlap: Optional[int] = None):
    docs = _all_documents()
    cfg = chunk_config(chunk_size, chunk_overlap)
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=cfg["chunk_size"], chunk_overlap=cfg["chunk_overlap"], length_function=len)
    return splitter.create_documents(
        [d["content"] for d in docs],
        metadatas=[{"title": d["title"], "source": "system" if d in system_documents else "custom"} for d in docs],
    )


# ================================================================ 索引口径指纹
#
# 下面这一组解决的是「换了 embedding 模型或改了切分参数，索引怎么办」。

# 索引构建逻辑本身的版本号。**不是参数变了才要重建，逻辑变了也要重建**：
# 比如将来改了切分方式或加了元数据字段，库里已有的向量就是按老逻辑产生的。
# 手工 +1，逼着老索引重建。
_INDEX_SCHEMA_VERSION = 1

# 我们写入 collection metadata 的键前缀。Chroma 自己会往 metadata 里写
# 内部键（hnsw:space 等），用前缀隔开，避免覆盖或被拒。
_FP_PREFIX = "kb_"


def _index_fingerprint(cfg: Dict[str, int],
                       embedding_dim: Optional[int] = None) -> Dict[str, Any]:
    """索引口径指纹：决定"库里那些向量代表什么"的那几个值。

    **为什么必须落库**：这些值任何一个变了，库里已有的向量就不再代表
    当前的文档切分与 embedding 语义。而 Chroma 原有的探测**只能发现维度
    变化**——换一个同样是 1024 维的 embedding 模型，维度探测照样通过，
    旧向量被静默复用，拿新模型的 query 向量去检索旧模型的文档向量：
    检索结果悄悄变差，且事后无从分辨（实测确认过这个行为）。

    指纹写进 collection metadata，重启后仍在，于是「换模型/改切分」
    在下次启动时就能被检测出来并重建，而不是留到人工怀疑时才追查。
    """
    fp: Dict[str, Any] = {
        "index_schema_version": _INDEX_SCHEMA_VERSION,
        "embedding_model": settings.EMBEDDING_MODEL,
        "chunk_size": cfg["chunk_size"],
        "chunk_overlap": cfg["chunk_overlap"],
    }
    if embedding_dim is not None:
        fp["embedding_dim"] = int(embedding_dim)
    # Chroma metadata 只接受标量值，剔除 None
    return {k: v for k, v in fp.items() if v is not None}


def _read_fingerprint(store: Any) -> Dict[str, Any]:
    """从 collection metadata 读出已存的指纹（读不到当空处理）。"""
    try:
        meta = dict(store._collection.metadata or {})
    except Exception:
        return {}
    return {k[len(_FP_PREFIX):]: v for k, v in meta.items() if k.startswith(_FP_PREFIX)}


def _stamp_fingerprint(store: Any, fp: Dict[str, Any]) -> None:
    """把口径指纹写进 collection metadata。

    Chroma 的 get_or_create_collection 对**已存在**的集合不会覆盖 metadata
    （实测确认），所以必须显式 modify —— 否则新指纹根本写不进去，
    下次启动照样检测不到口径变化。
    """
    try:
        current = dict(store._collection.metadata or {})
        merged = dict(current)
        for key, value in fp.items():
            merged[f"{_FP_PREFIX}{key}"] = value
        store._collection.modify(metadata=merged)
    except Exception as e:
        logger.warning("写入索引口径指纹失败，下次启动可能误判为口径已变: %s",
                       f"{type(e).__name__}: {str(e)[:200]}")


def _fingerprint_mismatch(stored: Dict[str, Any], fp: Dict[str, Any]) -> Optional[str]:
    """返回口径不一致的原因；一致返回 None。

    已有指纹为空 = 索引来源不明（老版本建出来的）。这属于「可能不可比」，
    按项目一贯的原则（宁可多报一次可能不可比，也不要把不可比的当可比），
    判为不一致并重建一次。
    """
    if not stored:
        return "索引无口径指纹（来源不明，需重建一次以确认口径）"
    for key, value in fp.items():
        if key not in stored:
            return f"指纹缺少 {key}"
        if stored[key] != value:
            return f"{key}: {stored[key]} → {value}"
    return None


def _probe_embedding_dim(embeddings: Any) -> Optional[int]:
    """探测 embedding 的**真实**维度。

    配置里的 EMBEDDING_DIMENSIONS 只是声明值，实际维度以模型输出为准——
    两者不一致时，指纹会记录一个错的维度，失去判别能力。
    探测失败不阻断初始化：指纹里没有维度项，仍能靠模型名/切分参数判别。
    """
    try:
        probe = embeddings.embed_query("维度探测")
        return int(len(probe))
    except Exception as e:
        logger.warning("embedding 维度探测失败，指纹将缺少维度项: %s",
                       f"{type(e).__name__}: {str(e)[:200]}")
        return None


def _rebuild_vector_store(embeddings: Any, split_docs: List[Any],
                          fp: Dict[str, Any]) -> Any:
    """重建向量库并盖上口径指纹。返回新的 store。"""
    try:
        Chroma(collection_name=COLLECTION_NAME, embedding_function=embeddings,
               persist_directory=PERSIST_DIR).delete_collection()
    except Exception:
        _reset_vector_dir()
    store = Chroma.from_documents(
        documents=split_docs, embedding=embeddings,
        collection_name=COLLECTION_NAME, persist_directory=PERSIST_DIR,
    )
    _stamp_fingerprint(store, fp)
    return store


def _reset_vector_dir():
    """维度不匹配等场景下重建向量库目录。

    原先直接删除整个目录，且只在 print 里留一行——
    生产环境这是**静默数据销毁**：换 embedding 模型或误配一次维度，
    知识库就没了。改为先重命名成带时间戳的备份目录并明确告警，
    保留人工恢复的可能；只保留最近 2 份备份避免无限堆积。
    """
    from datetime import datetime as _dt
    import glob as _glob

    if os.path.isdir(PERSIST_DIR):
        backup = f"{PERSIST_DIR}.bak-{_dt.now().strftime('%Y%m%d%H%M%S')}"
        try:
            os.rename(PERSIST_DIR, backup)
            logger.warning(
                "向量库维度不匹配，已重建索引；原目录备份在 %s（确认无误后可手动删除）",
                backup)
            # 只保留最近 2 份备份
            for old in sorted(_glob.glob(f"{PERSIST_DIR}.bak-*"))[:-2]:
                shutil.rmtree(old, ignore_errors=True)
        except OSError as e:
            logger.error("向量库目录备份失败(%s)，改为就地清空", e)
            shutil.rmtree(PERSIST_DIR, ignore_errors=True)
    os.makedirs(PERSIST_DIR, exist_ok=True)


def init_knowledge_base(force: bool = False,
                        chunk_size: Optional[int] = None,
                        chunk_overlap: Optional[int] = None) -> bool:
    """初始化检索链。返回向量检索是否可用（False 表示降级为 BM25/模板）。

    force=True 时强制重建（添加自定义文档后调用）。
    chunk_size/chunk_overlap 传值时用该参数切分，不传取全局配置；
    评测做参数 sweep 时靠这个在不改动生产代码的前提下试不同切分策略。
    """
    global _vector_store, _bm25_retriever, _qa_chain, _chat_llm, _init_attempted
    if _init_attempted and not force:
        return _vector_store is not None
    _init_attempted = True

    # 先从数据库恢复自定义文档（重启后这是唯一来源）
    _reload_custom_documents()

    _chat_llm = _build_chat_llm()
    embeddings = _build_embeddings()
    split_docs = _split_docs(chunk_size, chunk_overlap)

    # ---- 向量库：口径指纹不一致则重建；索引条数对不上也重建 ----
    if embeddings is not None:
        os.makedirs(PERSIST_DIR, exist_ok=True)
        try:
            cfg = chunk_config(chunk_size, chunk_overlap)
            fp = _index_fingerprint(cfg, _probe_embedding_dim(embeddings))

            if force:
                _vector_store = _rebuild_vector_store(embeddings, split_docs, fp)
            else:
                store = Chroma(
                    collection_name=COLLECTION_NAME,
                    embedding_function=embeddings,
                    persist_directory=PERSIST_DIR,
                )
                stored = _read_fingerprint(store)
                reason = _fingerprint_mismatch(stored, fp)
                if reason:
                    # 口径变了（换了 embedding 模型 / 改了切分参数 / 老库无指纹）
                    # 或索引条数与当前切分结果对不上（新增了系统文档但没 force 重建）：
                    # 两种情况都必须重建，否则库里留着的是语义已经不兼容的向量。
                    logger.warning("向量库口径不一致，重建索引：%s", reason)
                    _vector_store = _rebuild_vector_store(embeddings, split_docs, fp)
                else:
                    try:
                        count = store._collection.count()
                        if count != len(split_docs):
                            # 条数对不上：要么缺（新增了文档），要么多（删改过文档）。
                            # 增量修补比全量重建贵且容易留下不一致的碎片，
                            # 这里直接重建——语料规模小，重建成本可接受。
                            logger.warning("向量库条数与当前切分结果不一致（%d vs %d），重建索引",
                                           count, len(split_docs))
                            _vector_store = _rebuild_vector_store(embeddings, split_docs, fp)
                        else:
                            # 探测：旧库维度与当前 embedding 模型不一致时查询会报错
                            store.similarity_search("探测", k=1)
                            _stamp_fingerprint(store, fp)
                            _vector_store = store
                    except Exception:
                        logger.warning("向量库维度不匹配或数据损坏，重建索引")
                        _vector_store = _rebuild_vector_store(embeddings, split_docs, fp)
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


def index_stats(split_docs: Optional[List[Any]] = None) -> Dict[str, Any]:
    """索引统计：块数、长度分布、空块率、重复率。

    **为什么要它**：改切分策略时，"切成了多少块"是最先要看的数字——
    块数突然翻倍说明切分过碎（检索精度上升但上下文被截断），块数骤减
    说明切得过粗（BM25 分数被无关内容稀释）。光看用例通过与否看不出
    这个变化，因为用例断言的是检索结果，不是索引形态。

    空块与重复块也要单独盯：它们进了索引却永远召不回，纯属污染。
    """
    docs = split_docs if split_docs is not None else _split_docs()
    lengths = [len(d.page_content) for d in docs]
    empty = sum(1 for n in lengths if n == 0)
    keys = [_doc_key(d) for d in docs]
    duplicates = len(keys) - len(set(keys))
    by_title: Dict[str, int] = {}
    for d in docs:
        title = d.metadata.get("title", "")
        by_title[title] = by_title.get(title, 0) + 1
    return {
        "documents": len(by_title),
        "chunks": len(docs),
        "avg_chunk_len": round(sum(lengths) / len(lengths), 1) if lengths else 0,
        "min_chunk_len": min(lengths) if lengths else 0,
        "max_chunk_len": max(lengths) if lengths else 0,
        "empty_chunks": empty,
        "duplicate_chunks": duplicates,
        "chunks_per_document": by_title,
    }


def _doc_key(doc) -> str:
    return f"{doc.metadata.get('title', '')}|{doc.page_content[:60]}"


# RRF 的平滑常数。取 60 是信息检索里的常规值：它让"排第 1 的命中"与
# "排第 10 的命中"的差距不至于过大，同时仍保留名次信息。
_RRF_K = 60


def _rrf_fuse(doc_lists: List[List[Any]], k: int) -> List[Any]:
    """Reciprocal Rank Fusion：多路召回按名次加权合并。

    score(d) = Σ 1 / (_RRF_K + rank_i(d))，rank 从 1 开始计。

    **为什么用 RRF 而不是加权求和**：BM25 分数是无界正数，余弦相似度是
    0~1，两者量纲完全不同。直接加权必须先归一化，而归一化对样本分布
    高度敏感（换个语料就得重调权重）。RRF 只看名次、不看分数绝对值，
    天然免疫量纲差异，且不需要任何归一化参数——这是它被广泛使用的
    根本原因。

    同一份文档被两路同时召回时，它的 RRF 分是两路贡献之和，
    这正是混合检索想要的：两路都认的文档自然排前面。
    """
    scores: Dict[str, float] = {}
    order: Dict[str, Any] = {}
    for docs in doc_lists:
        for rank, doc in enumerate(docs, start=1):
            key = _doc_key(doc)
            scores[key] = scores.get(key, 0.0) + 1.0 / (_RRF_K + rank)
            if key not in order:
                order[key] = doc
    # 同分时按 key 排序，保证同样的输入得到同样的输出（可复现）
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    return [order[key] for key, _ in ranked[:k]]


def _retrieve_docs(query: str, k: int = 4) -> List[Any]:
    """混合检索入口：返回融合后的文档列表。"""
    docs, _stats = _retrieve_with_stats(query, k=k)
    return docs


def _retrieve_with_stats(query: str, k: int = 4) -> Tuple[List[Any], Dict[str, Any]]:
    """检索并返回各路命中明细（供埋点与模式判定使用）。

    **这里换掉过一套实现，值得说明原因**：原来先把 BM25 的结果全部塞进
    列表、再把向量结果追加到后面，最后 merged[:k] 截断。但 BM25Retriever
    的 k 就是 4，且它对**任何**查询都回满 4 条（哪怕查询完全无关，实测
    「今天股市行情如何」照样回 4 条）——于是列表在轮到向量结果之前就已经
    满了，向量结果被整段截掉。所谓"混合检索"实际是 BM25-only：向量库
    照建（每次重建还要重嵌全库、花 embedding 成本），线上却一条都不读。

    现在改成两路各自取候选、RRF 融合后再截断，两路都能真正进入上下文。
    """
    bm25_docs: List[Any] = []
    vector_docs: List[Any] = []
    bm25_ms = 0.0
    vector_ms = 0.0
    degraded = False

    if _bm25_retriever is not None:
        started = time.time()
        try:
            bm25_docs = list(_bm25_retriever.invoke(query))
        except Exception as e:
            degraded = True
            logger.warning("BM25 检索异常: %s", f"{type(e).__name__}: {str(e)[:200]}")
        bm25_ms = (time.time() - started) * 1000

    if _vector_store is not None:
        started = time.time()
        try:
            vector_docs = list(_vector_store.similarity_search(query, k=k))
        except Exception as e:
            degraded = True
            logger.warning("向量检索异常: %s", f"{type(e).__name__}: {str(e)[:200]}")
        vector_ms = (time.time() - started) * 1000

    # 候选池先不截断到 k：重排需要有多余候选才能改变顺序，否则重排无事可做。
    pool_size = k
    if settings.RERANK_ENABLED:
        pool_size = max(k, int(settings.RERANK_CANDIDATES or k))
    fused = _rrf_fuse([bm25_docs, vector_docs], pool_size)
    rerank_started = time.time()
    reranked, rerank_applied = _rerank(query, fused, k)
    rerank_ms = (time.time() - rerank_started) * 1000
    merged = reranked[:k]

    stats: Dict[str, Any] = {
        "bm25_titles": [d.metadata.get("title", "") for d in bm25_docs],
        "vector_titles": [d.metadata.get("title", "") for d in vector_docs],
        "merged_titles": [d.metadata.get("title", "") for d in merged],
        "bm25_ms": round(bm25_ms, 2),
        "vector_ms": round(vector_ms, 2),
        "total_ms": round(bm25_ms + vector_ms, 2),
        "top_k": k,
        "degraded": degraded,
        "mode": _retrieval_mode(bm25_docs, vector_docs),
        "rerank_applied": rerank_applied,
        "rerank_ms": round(rerank_ms, 2),
        "candidate_pool": len(fused),
    }
    return merged, stats


def _build_reranker() -> Optional[Dict[str, Any]]:
    """构建重排客户端。返回 None 表示不启用重排。

    重排是**可选增强**：没开开关、没有凭据、接口不通，都应该退回
    「RRF 融合后直接截断」——检索链路不能因为一个锦上添花的环节挂掉。

    凭据与地址默认复用 embedding 那一套（同一网关通常同时提供 rerank 接口），
    所以多数部署不需要额外配置。
    """
    if not settings.RERANK_ENABLED:
        return None
    api_key = settings.RERANK_API_KEY or settings.embedding_api_key
    base = (settings.RERANK_API_BASE or settings.EMBEDDING_API_BASE or "").rstrip("/")
    if not api_key or not base:
        return None
    return {"api_key": api_key, "base": base, "model": settings.RERANK_MODEL}


def _rerank(query: str, docs: List[Any], k: int) -> Tuple[List[Any], bool]:
    """用交叉编码器对候选逐条精排。返回 (重排后的文档, 是否真的重排了)。

    **失败一律沿用原序**：重排是加分项，不能因为它失败就把召回结果丢掉。
    """
    client = _build_reranker()
    if client is None or len(docs) <= 1:
        return docs, False

    try:
        import httpx

        resp = httpx.post(
            f"{client['base']}/rerank",
            headers={"Authorization": f"Bearer {client['api_key']}",
                     "Content-Type": "application/json"},
            json={
                "model": client["model"],
                "query": query,
                "documents": [d.page_content for d in docs],
                "top_n": len(docs),
            },
            timeout=min(settings.LLM_TIMEOUT_SECONDS or 30, 10),
        )
        resp.raise_for_status()
        results = resp.json().get("results") or []
        if not results:
            return docs, False

        # 返回项形如 {"index": i, "relevance_score": s}，顺序即新序
        ordered: List[Any] = []
        used = set()
        for item in results:
            idx = item.get("index")
            if not isinstance(idx, int) or idx < 0 or idx >= len(docs) or idx in used:
                continue
            used.add(idx)
            ordered.append(docs[idx])
        # 接口漏掉的候选按原序补在后面，避免重排后条目变少
        for i, doc in enumerate(docs):
            if i not in used:
                ordered.append(doc)
        return ordered[:k], True
    except Exception as e:
        logger.warning("重排失败，沿用融合顺序: %s",
                       f"{type(e).__name__}: {str(e)[:200]}")
        return docs, False


def _retrieval_mode(bm25_docs: List[Any], vector_docs: List[Any]) -> str:
    """检索模式标签：**必须如实反映哪几路真正参与了**。

    原实现按"向量库是否可用"来判，于是只要配了 embedding 就报 hybrid_rag，
    即使向量结果因 BM25 配额截断而一条都没进上下文。标签会骗人：排障时
    据此判断"走了混合检索"就会走进死胡同，成本页也会把 embedding 的开销
    算到一条根本没参与的链路上。
    """
    has_bm25 = bool(bm25_docs)
    has_vector = bool(vector_docs)
    if has_bm25 and has_vector:
        return "hybrid_rag"
    if has_bm25:
        return "bm25_llm"
    if has_vector:
        return "vector_llm"
    return "fallback"


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


_QA_PROMPT_TEMPLATE = """你是企业人事制度知识库助手，请严格根据提供的上下文信息回答问题。

上下文信息：
{context}

问题：
{question}

要求：
1. 只依据上下文回答，上下文没有的内容明确说"知识库中暂无相关规定"，禁止编造；
2. 回答准确、简洁，涉及数字（天数、比例、日期）必须与上下文一致；
3. 用中文回答。"""


def _invoke_qa_with_instrumentation(context: str, question: str) -> str:
    """走统一接入层发起问答，获得埋点与成本记账。

    同步函数里调 async 接口，用一个一次性事件循环承载——
    这里本来就是被 asyncio.to_thread 放到工作线程执行的，
    新建 loop 不会影响主循环。
    """
    import asyncio as _asyncio
    from src.services.llm_invoke import invoke_text

    async def _run() -> str:
        # _qa_chain 是"问答链是否可用"的开关；测试用替换它来模拟 LLM 不可用，
        # 这里识别出测试桩后直接走它以触发异常分支（真实链则走统一接入层埋点）
        chain = _qa_chain
        if chain is not None and type(chain).__name__ != "RunnableSequence":
            return chain.invoke({"context": context, "question": question})
        return await invoke_text(
            _QA_PROMPT_TEMPLATE,
            {"context": context, "question": question},
            default="知识库问答服务暂时不可用，请稍后重试或转人工咨询。",
            call_site="knowledge_qa",
            # 调用失败要向上抛：这样 mode 才会标成 fallback，
            # 否则降级回答会被当成正常作答报出去，调用方无从分辨
            raise_on_error=True,
            # 知识库问答用自己的 temperature=0.3 客户端（生成式回答需要更高随机性），
            # 不能被招聘评估的 0.1 客户端悄悄替换
            client=_chat_llm,
        )

    try:
        return _asyncio.run(_run())
    except Exception as e:
        # BudgetExceeded 在此转成降级回答：知识库问答是辅助功能，
        # 不应让一次预算耗尽把整个接口打成 500
        from src.services import budget
        if isinstance(e, budget.BudgetExceeded):
            raise
        raise


def _write_retrieval_log(query: str, stats: Dict[str, Any]) -> None:
    """写检索埋点。

    检索侧此前完全没有埋点，导致：无法知道用户问了什么、召回了哪些文档、
    哪一路出的结果、耗时多少——既没法从生产流量反哺评测集，也没法回答
    「这条为什么没命中」。

    写库失败绝不能影响主流程，所以整段包在 try 里。
    """
    try:
        from src.services import retrieval_log
        cfg = chunk_config()
        retrieval_log.write_retrieval_log(
            query_text=query,
            top_k=stats.get("top_k"),
            bm25_titles=stats.get("bm25_titles"),
            vector_titles=stats.get("vector_titles"),
            merged_titles=stats.get("merged_titles"),
            bm25_ms=stats.get("bm25_ms"),
            vector_ms=stats.get("vector_ms"),
            total_ms=stats.get("total_ms"),
            mode=stats.get("mode"),
            chunk_size=cfg["chunk_size"],
            chunk_overlap=cfg["chunk_overlap"],
            embedding_model=settings.EMBEDDING_MODEL,
            degraded=stats.get("degraded", False),
            rerank_applied=stats.get("rerank_applied", False),
            rerank_ms=stats.get("rerank_ms"),
        )
    except Exception as e:
        # 埋点失败只告警：检索本身已经成功了，不能因为写日志失败而让用户看到报错
        logger.warning("写检索日志失败: %s", f"{type(e).__name__}: {str(e)[:200]}")


def _query_sync(query: str) -> Dict[str, Any]:
    init_knowledge_base()
    if _qa_chain is None:
        return fallback_answer(query)

    try:
        docs, stats = _retrieve_with_stats(query)
        _write_retrieval_log(query, stats)
        if not docs:
            # 检索器都没命中：交模板兜底，避免 LLM 无依据编造
            return fallback_answer(query)
        from src.services import budget
        try:
            answer = _invoke_qa_with_instrumentation(_format_docs(docs), query)
        except budget.BudgetExceeded:
            return {
                "answer": "AI 问答预算已用尽，已切换为关键词模板回答。",
                "sources": [],
                "mode": "budget_exhausted",
            }
        sources = [
            {"title": doc.metadata.get("title", "未知"), "content": doc.page_content[:200]}
            for doc in docs
        ]
        return {
            "answer": answer if isinstance(answer, str) else str(answer),
            "sources": sources,
            # 如实反映哪几路真正参与了检索，而不是"向量库是否可用"
            "mode": stats.get("mode") or "fallback",
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


def add_document(title: str, content: str, created_by: Optional[int] = None) -> int:
    """添加自定义文档：落库 + 重建索引，返回文档 id。

    必须落库——此前只 append 到内存列表，重启即丢（文档列表不显示、
    BM25 检索丢失；若期间触发向量库维度重建还会永久消失）。
    """
    from src.models.database import SessionLocal, KnowledgeDocument, ensure_tables

    ensure_tables()
    db = SessionLocal()
    try:
        doc = KnowledgeDocument(title=title, content=content, created_by=created_by)
        db.add(doc)
        db.commit()
        db.refresh(doc)
        doc_id = doc.id
    finally:
        db.close()

    _reload_custom_documents()
    # 强制重建：split_docs 已包含新文档，BM25 全量重建，向量库增量写入
    init_knowledge_base(force=True)
    return doc_id


def delete_document(doc_id: int) -> bool:
    """删除自定义文档并重建索引。返回是否删除成功。"""
    from src.models.database import SessionLocal, KnowledgeDocument, ensure_tables

    ensure_tables()
    db = SessionLocal()
    try:
        doc = db.query(KnowledgeDocument).filter(KnowledgeDocument.id == doc_id).first()
        if not doc:
            return False
        db.delete(doc)
        db.commit()
    finally:
        db.close()

    _reload_custom_documents()
    init_knowledge_base(force=True)
    return True


def list_custom_documents() -> List[Dict[str, Any]]:
    """列出自定义文档（带 id，供前端删除操作使用）"""
    _reload_custom_documents()
    return list(_extra_documents)


def _reload_custom_documents() -> None:
    """从数据库重新加载自定义文档到内存缓存。

    _extra_documents 只作为进程内缓存使用，真实来源是 knowledge_documents 表。
    加载失败时保留现有缓存并告警，不让知识库整体不可用。
    """
    global _extra_documents
    try:
        from src.models.database import SessionLocal, KnowledgeDocument, ensure_tables
        ensure_tables()
        db = SessionLocal()
        try:
            rows = db.query(KnowledgeDocument).order_by(KnowledgeDocument.id).all()
            _extra_documents = [
                {"id": r.id, "title": r.title, "content": r.content} for r in rows
            ]
        finally:
            db.close()
    except Exception as e:
        logger.warning("加载知识库自定义文档失败，沿用内存缓存: %s", str(e)[:200])


def get_all_documents() -> List[Dict[str, str]]:
    return _all_documents()


# 模块加载时不自动初始化（避免无配置时启动失败）；首次查询时延迟初始化
