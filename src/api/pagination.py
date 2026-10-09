"""列表接口的统一分页参数。

**为什么需要**：`/positions` `/questionnaires` `/evaluations` `/talent-pool` `/reviews`
此前都是 `query.all()`——没有上限，数据涨起来会把整表塞进一次响应；
而 `/candidates` `/interviews` 又有 `skip/limit`，口径不一致。

`le` 上界是必须的：只给默认值不加界，调用方仍可以传 `limit=10_000_000`，
等于没分页。

默认 100 与既有的 `/candidates` `/interviews` 保持一致。**这是一次行为变更**：
原先这 5 个接口返回全量，现在上限 100——本项目当前数据量（个位数到几十）下
不产生可见差异，但如果有环境超过 100 条，界面会只显示前 100 条（需要翻页）。
"""
from typing import Annotated

from fastapi import Query

# 单次最多返回条数上限
MAX_PAGE_SIZE = 1000
DEFAULT_PAGE_SIZE = 100

SkipParam = Annotated[int, Query(ge=0, description="跳过前 N 条")]
LimitParam = Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE,
                                  description=f"单次最多返回条数（上限 {MAX_PAGE_SIZE}）")]
