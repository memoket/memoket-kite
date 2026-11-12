# KITE 多跳评测交接

更新于 2026-11-12。代码在 `multihop` 分支，基于原 KITE 的 `c92e803`。目前继续推进 MuSiQue，2Wiki 已冻结。完整的迭代历史见 [EXPERIMENT_LOG.md](EXPERIMENT_LOG.md)。

## 成绩和口径

| 数据集与版本 | EM | F1 | 口径 |
| --- | ---: | ---: | --- |
| 2Wiki 冻结版 | 68.70 | 74.98 | 1,000 道开发题，官方 v1.1 别名和 demonym；109 道重跑 Answer，其余 891 道复用冻结预测 |
| MuSiQue 整段库修复版 | 37.70 | 48.57 | 同一批 1,000 道开发题完整重跑 Compile 和 Answer |
| MuSiQue 已保存最高分 | 38.30 | 49.28 | 固定计划和证据，末跳取值提示对照；候选留在实验目录 |
| MuSiQue 最近用于错题审计的结果 | 37.90 | 49.22 | 末跳关系观察候选，固定计划和证据；621 道 EM 错题 |
| MuSiQue 当前目标 | 45.00 | 57.20 | 目标尚未达到 |

2Wiki 用 6,119 段共享整段库，MuSiQue 用 11,656 段共享整段库。这些 1,000 题已用于开发。近期 502 题和 22 题回放只验证检索与交付，没有新的 EM/F1。HotpotQA 留有早期 facts 索引和小试记录，本交接没有同口径的全量整段问答结果。论文设定及可比范围见 [多跳评测 README](README.md)。早期 BGE 混合检索分数属于独立实验。

## 接手先看这里

1. [MuSiQue 建库](musique/index.py) 从段落标题和原文用 Luna reasoning none 每段调用一次。索引保留整段原文，只提取用于检索的实体、别名、主题和 facet。当前库未复用旧 facts 标签。
2. [MuSiQue QA 入口](musique/qa.py) 使用原生符号检索和现有计划 schema。Luna medium 每轮编译一个计划，最多两轮。第二轮反馈包含首轮计划、执行结果和去重后的原文；建库的占位日期不参与绑定。原生子阶段预算 80，Answer 最多 40 个不同段落，用一次 GPT-4o-mini。
3. [原生计划执行](../../src/memoket_kite/core/algebra.py) 和 [Compile 反馈与候选](../../src/memoket_kite/pipeline/retrieve.py) 是排查顺序的起点。已接入的修复包括按原文去重交付、首轮独有候选追加到候选尾部、允许整段命中使用已有阶段保留名额。最后一项在同一批 1,000 题离线回放中把支持段完整交付从 562 提到 569，379 道原答对题无掉段。
4. [2Wiki 冻结清单](2WIKI_FREEZE.json) 记录 28 个本机产物的校验值和复现口径。最后一次 MuSiQue 审计已验证全部 28 项。LoCoMo 和 LongMemEval 的代码入口没有改动。

## MuSiQue 当前瓶颈和下一步

最近对 123 道原生命中后丢段题和 379 道原答对题做了两次离线对照。逐题查出 69 道的第二轮存在无主体范围的事实查询。严格确认漏绑的有 3 道，另 22 道只有关键词中的主体。对 3 道漏绑题和 5 道符合相同条件的对照，只收窄现有第二轮的一条查询后，123 道全证据交付仍为 0 道，379 道对照从 293 降到 292。8 条中 7 条没有主体与关系共同命中，执行器尝试放宽 grep。这些修改只在诊断回放里执行。

22 道进一步查出 41 个未交付支持段。针对同一主体和对应关系，8 道原文明示关系但查询不匹配，另有 2 道间接线索。8 道中 3 道漏同义、别名或反向关系，2 道把多个条件强制合取，3 道只搜最高级措辞而漏掉可直接比较的数字。16 段正文明确提到主体，仅 1 段缺实体倒排，且该段实际由首轮召回、在合并时丢失。2 道真正同时命中所问关系和主体倒排，第二轮仍排在第 2,516 和第 220。全 41 段按原生阶段上限、阶段合并、入池未交付、未观察到命中计，分别是 26、5、4、6 段。

优先修第二轮已有查询的关系表达。先验证表示同一关系的自然表述能否在原文中命中，并检查原生 trace 是否在命中前放宽了 grep。以冻结的 123 道缺段题和 379 道原答对题回归，分别统计进子阶段前 80、交给 Answer、原已交付支持段掉段。证据改善且对照无掉段后，才固定模板用 4o-mini 测 EM/F1。排到 80 名后的真实命中另看查询选择性。此次诊断没有显示重建全库有优先收益。

标准答案、支持段、标准分解和真实题目样例留在离线评分与诊断侧。推理只使用题面、实际检索原文和模型生成的计划。新增约束要能跨题和跨数据集使用。先用小批量和正确对照验证，再考虑全量。保留每次回放的结果和失败记录。

## 复现与移交文件

从仓库根目录用 Python 3.12 建环境。测试和现有冻结数据回放均可离线执行。

```sh
python -m pip install -e '.[benchmark,dev]'
python -m pytest -q
```

[MuSiQue README](musique/README.md) 给出建库、问答、评分入口。新跑模型需要接手人自己的 provider 配置。复现现有问题从冻结库和归档计划开始。

Git 提交包含代码、测试、实验记录和这份交接。下面的索引、题面、预测、支持标注和实验脚本在当前机器的 `artifacts/results/multihop/`，该目录由 Git 忽略。交给其他机器复现时，按原相对路径另行复制所需产物。

| 路径（相对于 `artifacts/results/multihop/`） | 内容 |
| --- | --- |
| `musique-bugfix-n1000/` 下的 `index/{index.xml,freeze.json}`、`ANSWER_SCHEMA.json`、`qa/individual/*/retrieval.json` | 冻结索引、schema 和两轮实际计划 |
| `musique-shared-passage-repaired-v3-n1000/AUDIT_OFFLINE.json` | 离线支持段标注 |
| `musique-one-hop-preservation-v1/LOSS_BOUNDARIES_OFFLINE.json` | 123 道缺段边界 |
| `musique-second-round-subject-audit-v1/` | 69 道逐题审计、123+379 对照清单、冻结回放和报告 |
| `musique-literal-subject-audit-v1/` | 22 道诊断，含逐题结论、计数、原文与原生执行记录 |
| `musique-answer-terminal-{value,observation}-v1-n1000/SCORED_OFFLINE.json` | 两组 1,000 题已保存结果 |
| 见 [2Wiki 冻结清单](2WIKI_FREEZE.json) 的 `sha256` 字段 | 2Wiki 可核验快照 |

带上上述 MuSiQue 产物后，可在仓库根目录运行 22 题诊断。脚本校验索引摘要，并逐题比对前 80、候选池和实际交付与冻结基线是否一致。

```sh
runs=artifacts/results/multihop
audit="$runs/musique-literal-subject-audit-v1"
PYTHONPATH=src:. python "$audit/trace.py" "$runs" "$audit"
python "$audit/summarize.py"
```

`summarize.py` 同时校验 2Wiki 冻结清单，因此这一条也需要清单列出的本机文件。每道题的文字核对在 `REVIEW.tsv`，已汇总数字在 `COUNTS_OFFLINE.json`。
