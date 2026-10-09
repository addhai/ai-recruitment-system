"""评分口径指纹：由代码与运行时配置算出，用来判断两次评分是否可比。

## 解决什么

同一个候选人在不同时间跑出不同分数，可能是三种原因：供应商换了底层模型版本、
采样参数变了、或者我们自己改了提示词/权重/门限。前两者能靠埋点里的
`model_served` 与配置快照看出，**第三类此前完全靠人记**——`settings.SCORING_VERSION`
是一个手工维护的字符串，改了提示词却忘了改它，历史分数就会被静默当成可比的。

这里把「口径」换成算出来的：只要决定评分的那几个模块的**语义**变了，
指纹就会变。手工字符串继续保留（它承载人能读懂的含义），但不再承担可比性判断。

## 为什么用 AST 摘要，而不是把提示词抽成常量表

曾经考虑把 10 处内联提示词抽成模块级常量、再对常量取值做哈希。放弃了：
那样就又多了一份**需要人记得同步**的清单——新写一处内联提示词、忘了登记，
指纹照样不变，等于把同一种病换个地方再犯一次。

对源码做 AST 摘要没有这个缺口：新增的提示词、挪动的权重、改掉的门限，
只要在模块里，就一定进摘要。代价是它对**与评分无关的改动也会敏感**，
这个方向的误差是刻意选的——多报一次"可能不可比"只是让人复核一遍，
漏报却会把不可比的分数当成可比的。

注释与缩进不影响摘要（AST 里没有它们），所以纯格式调整不会产生假警报。

## 覆盖范围与不覆盖的部分

进指纹的：
- `_SCORING_MODULES` 三个模块的 AST（提示词、`OVERALL_WEIGHTS`、各阶段门限）；
- 运行时口径：模型名、base_url、temperature、timeout、`SCORING_VERSION`。

**不进**指纹的：排序不稳定的外部依赖（供应商底层版本，用埋点的 `model_served` 看）、
JD 与简历内容本身（那是输入，不是口径）。所以指纹相同**不等于**分数必然相同——
大模型采样本身不可复现，指纹只能把"口径是否变了"这件事变成确定性的判断。
"""
import ast
import hashlib
import importlib
from pathlib import Path
from typing import Dict, Optional

from src.config import settings
from src.logging_setup import get_logger

logger = get_logger(__name__)

# 决定评分口径的模块。JD 解析也在内：它产出的 jd_profile 是各评分节点的输入，
# 改它等于改口径。llm_json 里放着客户端参数（temperature/max_retries/timeout 接线）。
_SCORING_MODULES = (
    "src.workflow.recruitment_graph",
    "src.services.job_parser",
    "src.services.llm_json",
)

# 只取这些键进配置摘要。**刻意不含 api_key**：它是凭据，不该进摘要输入，
# 而且换密钥本来也不改变评分口径。
_DIGEST_CONFIG_KEYS = ("model", "base_url", "temperature", "timeout_seconds")


def module_sources() -> Dict[str, str]:
    """读取参与指纹的模块源码。任何一个读不到都只跳过并告警，不抛。

    读不到极少见（源码被打包进 zip 等），此时指纹会退化为"只覆盖运行时配置"，
    调用方据返回值是否为 None 判断，不要把降级结果当成正常指纹。
    """
    sources: Dict[str, str] = {}
    for name in _SCORING_MODULES:
        try:
            mod = importlib.import_module(name)
            path = getattr(mod, "__file__", None)
            if not path:
                logger.warning("评分指纹：模块无源文件，跳过",
                               extra={"module": name})
                continue
            sources[name] = Path(path).read_text(encoding="utf-8")
        except Exception as e:
            logger.warning("评分指纹：读取模块源码失败，跳过",
                           extra={"module": name,
                                  "error": f"{type(e).__name__}: {str(e)[:200]}"})
    return sources


def code_digest(sources: Dict[str, str]) -> str:
    """源码摘要。纯函数，便于用合成源码做单测。

    按模块名排序后逐个 AST 化，保证结果与文件读取顺序无关。
    解析失败时退回原文哈希——宁可更敏感，也不要因为解析不了就静默漏掉。
    """
    h = hashlib.sha256()
    for name in sorted(sources):
        h.update(name.encode("utf-8"))
        h.update(b"\x00")
        try:
            tree = ast.parse(sources[name])
            h.update(ast.dump(tree).encode("utf-8"))
        except SyntaxError:
            h.update(sources[name].encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def config_digest(cfg: Optional[Dict] = None) -> str:
    """运行时口径摘要。cfg 传空时自行向 llm_config 取当前生效配置。"""
    if cfg is None:
        from src.services import llm_config
        try:
            cfg = llm_config.get_effective_config()
        except Exception as e:
            logger.warning("评分指纹：读取模型配置失败，仅用版本号计算",
                           extra={"error": f"{type(e).__name__}: {str(e)[:200]}"})
            cfg = {}
    h = hashlib.sha256()
    h.update(f"scoring_version={settings.SCORING_VERSION}".encode("utf-8"))
    for key in _DIGEST_CONFIG_KEYS:
        h.update(f"|{key}={cfg.get(key)!r}".encode("utf-8"))
    return h.hexdigest()


def fingerprint() -> Optional[str]:
    """返回 16 位十六进制指纹；源码完全读不到时返回 None（表示口径不可判定）。

    故意取短：它是要落进 `WorkflowRun.results` 并给人看的，
    16 位足以区分口径变化，碰撞概率可忽略。
    """
    sources = module_sources()
    if not sources:
        logger.warning("评分指纹：未能读取任何评分模块源码，指纹不可判定")
        return None
    h = hashlib.sha256()
    h.update(code_digest(sources).encode("utf-8"))
    h.update(config_digest().encode("utf-8"))
    return h.hexdigest()[:16]
