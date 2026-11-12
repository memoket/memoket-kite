# MuSiQue 整段检索

MuSiQue 入口复用 multihop 的建库、KITE 原生检索、原文交付和离线评分。输入索引模型的是标题和整段原文，输出只有实体、别名、主题和 facet。Luna 每段一次，reasoning 为 none。问答使用最多两轮 Luna medium Compile、每轮一个计划和一次 4o-mini Answer。

- [index.py](index.py) 将根主题和子主题定义为互斥的 schema 分支。模型把同一实体的主名、别名、类型和地点标记放在一组，索引层将有效字段投影到原生索引。缺少原文依据或归属冲突的字段单独拒收；地点标记超限时保留可检索的地点实体。原始响应、拒收记录和最终入库字段分别保留。全局别名冲突在普通注册完成后处理，保留已有绑定。
- [qa.py](qa.py) 将现有结构化 Compile 接到首轮和反馈轮。阶段引用必须显式投影，grep 由现有原生校验器检查。首轮和反馈轮共用这套格式，反馈轮允许停止。
- Answer 交付按原文段落计数。同一段的索引行和来源行共用一个名额，释放的名额按现有检索排序补齐。已有段落与顺序保留，聚合计划和显式 token 上限沿用原交付方式。
- 开放问句的 Answer schema 要求具体值，真伪问句允许 Yes 和 No。第二轮 Compile 每个命中只看一次完整原文，建库占位日期不参与跨阶段绑定。

全量评测前使用固定的历史失败样本和独立原文对照验证。运行时从 KITE checkout 根目录选择新产物路径。

```sh
PYTHONPATH=src:. python -m benchmarks.multihop.musique.index \
  --corpus "$corpus" --output "$run/index" --env-file "$provider_env" --workers 16
PYTHONPATH=src:. python -m benchmarks.multihop.musique.qa \
  --root "$run" --env-file "$provider_env" --workers 16
PYTHONPATH=src:. python -m benchmarks.multihop.passage_score \
  --root "$run" --references "$scoring_references"
```

QA 沿用共享运行契约。将仅含 `id`、`question`、`ordinal` 的 `QUESTIONS.json`、冻结的 Answer 模板与 schema、以及 `QA_CONTRACT.json` 放入运行目录。契约需记录题数、题面摘要、`runtime_hashes`、索引和 Answer 文件的摘要，以及 `passage_qa.CONTRACT_NOTE + profile.COMPILE_PROMPT` 的 SHA-256。标准答案只在预测冻结后传给评分入口。

第二轮计划执行成功后，首轮独有的候选留在第二轮候选之后。交付时只有第二轮候选无法补齐去重释放的名额，才会使用这些备用候选。
多阶段合并时，整段 `unit` 命中可以使用现有的每阶段两个保留名额。阶段顺序、检索预算和 Answer 段数预算仍沿用原设置。

2Wiki 继续以 [冻结清单](../2WIKI_FREEZE.json) 为准。
