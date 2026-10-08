# LLM 质量回归评测

**定位：防退化，不是精度验证。**

实测同一份简历连跑 4 次，文化契合给出 72/72/58/58——**数值本身就不稳定**。
所以这里只断言**硬性不变量**：

- 该出现的字段出现了吗（`skills` / `education` / `missing_skills`）
- 结构类型对吗（列表还是字符串、分数是不是数字）
- 文化评语**真的引用了该 JD 的原文价值观**吗（还是退回了写死的通用价值观）
- 未设限的维度**没有被当成 0 分**写进决策理由吗

分数、耗时、token、成本**全部只记录不判定**，多次运行看分布趋势由人工判断。

## 运行

```bash
# 全部用例
python tests/eval/run_eval.py

# 只跑指定用例（名称子串匹配）
python tests/eval/run_eval.py --cases 01 07

# 每条跑 3 次，观察数值波动
python tests/eval/run_eval.py --repeat 3

# 导出 JSON 报告
python tests/eval/run_eval.py --out report.json
```

**需要 `.env` 配置真实 LLM / embedding 凭据**——本运行器不 mock 模型，
mock 了就测不出真实退化。因此会产生真实 API 成本，**不进日常 CI**，
只在手动触发的工作流里跑。

## 目录结构

```
tests/eval/
  cases.json          用例集（含断言定义）
  run_eval.py         运行器
  resumes/            简历素材（PDF）
  jds/                岗位 JD 素材（纯文本）
  README.md           本文件
```

## 怎么加用例

在 `cases.json` 的 `cases` 数组里追加一项：

```json
{
  "name": "23-新场景",
  "resume_ref": "resumes/xxx.pdf",     // 或用 "resume_text": "内联文本"
  "jd_ref": "jds/xxx.txt",
  "position": "岗位名",
  "assertions": [
    {"kind": "education_not_empty", "note": "为什么加这条"}
  ]
}
```

素材放 `resumes/` 或 `jds/` 下，`resume_ref` / `jd_ref` 用相对该目录的路径。

## 可用的断言类型

| kind | 判据 | 拦的是什么 |
| --- | --- | --- |
| `key_present` | 结果含指定键（配 `node` + `keys`） | 结构化解析整体失效 |
| `education_not_empty` | 简历写了学历时必须解析出学历 | `parse_resume` 被跳过（历史 bug：`raw_text` 快照被误判为已解析） |
| `type_is_list` / `type_is_number` | 字段类型正确（配 `node` + `field`） | 模型输出结构漂移 |
| `culture_cites_jd` | JD 有 `culture_values` 时评语必须引用其中至少一个 | 退回写死的通用价值观 |
| `missing_skills_shape` | `matched_skills` / `missing_skills` 都是列表 | 逐条核对机制失效 |
| `no_zero_risk_for_unconstrained` | 未设限维度不得在决策里被写成 0 分 | 未设限维度被当 0 分（历史 bug） |
| `decision_shape` | decision 属于 `推荐录用/待定/不推荐/待人工复核` | 决策被污染或流程崩溃 |

断言实现见 `run_eval.py` 的 `ASSERTIONS` 字典。**新增断言时只判断"不变量是否成立"**，
不要写数值区间——那样会因为模型波动频繁误报，最后没人看。

## 怎么读报告

```
[PASS] 01-AI全栈简历 vs 实习生岗
    run1: skill=92 experience=88 education=95 culture=78 overall=84  决策=推荐录用

[FAIL] 07-金融简历 vs 实习生岗
    run1: skill=72 experience=86 ...
      ✗ missing_skills_shape: matched=None missing=None
```

末尾的「数值分布」区块给出每个维度的 min / median / max / 波动幅度。
**波动幅度本身是有用信号**：如果某维度波动从 5 分涨到 20 分，说明该维度的
评分不稳定度上升了，值得排查提示词或模型版本变化。

## 设计取舍

- **为什么不断言具体分数**：数值不可复现，锁死会天天误报，最终被无视。
  要提升稳定性应该从「固定模型版本 + 人工复核区间」入手，而不是把不稳定的数值写成断言。
- **为什么不进日常 CI**：每次全量评测要跑几十次真实 LLM 调用，有固定成本；
  且结果含随机性，不适合作为每次 push 的门禁。手动触发更符合实际使用节奏。
- **为什么用 JSON 而不是 YAML**：少一个依赖（项目未引入 pyyaml），
  且断言结构简单，JSON 足够表达。
