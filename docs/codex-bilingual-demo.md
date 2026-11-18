# Kite by Memoket 双语演示与内测

## 安装

对外版通过 Memoket 插件目录安装。先添加目录，再在 Codex 桌面版打开 **Plugins → Memoket → Kite by Memoket → Install**。已有目录可运行 `codex plugin marketplace upgrade memoket` 后重开 Codex。GitHub 分支提供已发布的版本。 [代码仓](https://github.com/memoket/memoket-kite) · [Codex 安装说明](codex.md#install)

```sh
codex plugin marketplace add memoket/memoket-kite --ref codex/agent-native-memory \
  --sparse .agents/plugins --sparse plugins/kite
```

内测新版本时，用本次构建的 Memoket 目录。解压后把 `/absolute/path/to/marketplace` 换成包含 `.agents/plugins/marketplace.json` 的绝对路径。若已注册同名远端目录，使用干净的 Codex 配置做包安装测试，或在 Codex 的目录管理里切换到本地内测目录。

```sh
codex plugin marketplace add /absolute/path/to/marketplace
```

维护者可从仓库根目录构建同样的包。首次安装需能下载运行时依赖。

```sh
uv build --wheel --out-dir ./artifacts/codex-build
python3 scripts/build_codex_marketplace.py ./artifacts/codex-marketplace \
  --wheel ./artifacts/codex-build/memoket_kite-*-py3-none-any.whl
python3 tests/packaging/codex_smoke.py --marketplace ./artifacts/codex-marketplace \
  --plugin-version 0.1.112
```

## 在 Codex 构建并翻译一份记忆

把获准用于演示的转写附到 Codex，保留在演示工作区。下面两段可直接复制。构建和翻译共用同一个工作区，传播范围以演示所选资料为准。

### 中文 Prompt

> 用 Kite by Memoket 把我附上的中文会议转写导入这个演示工作区。用 KITE 原生的 source-indexing 流程，按会话内容建立有层级的 Topic、Entity 和 Facet 索引，每个索引绑定实际的原文行。facts 留空。保留原文、说话人、时间戳和引用 ID。再用 KITE 的 memory-language prepare en 分批读取图谱条目，生成英文展示文本，并用 memory-language apply en 写入同一版工作区。翻译主题、实体、Facet、录音标题、片段标题和选定的引文，不改变专有名词、日期、负责人、提议、已确认或取消的状态。按 prepare 输出的 ID 返回译文，保持原引用不动。打开图谱，我要能在原语言和 English 之间切换。用 KITE 原生符号检索确认一条最新决定，并给我英文译文和可以打开的原文位置。

### English prompt

> With Kite by Memoket, index my attached meeting transcript into this demo workspace using the native source-indexing pipeline. Build hierarchical topics, entities, and facets from the conversation, with an original line behind every indexed span. Leave facts empty. Preserve speaker names, timestamps, and citation IDs. Use `memory-language prepare en` to page through labels and selected evidence, and `memory-language apply en` to attach the English display text to the same workspace version. Translate topics, entities, facets, recording and passage titles, and selected quotes. Preserve named products, dates, ownership, and the distinction between proposals and confirmed decisions. Open the graph, switch between English and the original language, and verify the latest decision through native symbolic search and its original citation.

### KITE Twin 自动构建与批量补译

桌面小人构建新记忆时，会用所选的 Codex 或 Claude 同时生成英文展示。打开 **设置 → 显示语言** 选择中文或 English，从小人打开的记忆图会沿用该语言。中文档显示每条记忆的原语言，英文档显示已生成的英文标题、索引和引文。

已导入的工作区可分批补齐。把 `kite-tool` 替换为 Codex 插件使用的 `bash "$KITE"` 也可以。先翻译用于演示的索引和引文，较大的工作区按类型和 `next_offset` 分批。每个批次最多 200 项。

```sh
kite-tool --workspace /absolute/path/to/memory memory-language prepare en --kind topic --limit 50
kite-tool --workspace /absolute/path/to/memory memory-language apply en --input /absolute/path/to/translations.json
```

`translations.json` 须包含 `workspace_id`、`version`、`language` 和 `translations`。前面三个字段从 prepare 输出原样复制，`translations` 是 `{图谱 ID: 译文}` 的对象。支持 `topic`、`entity`、`facet`、`source`、`passage` 和 `line`。如果另一个导入更新了版本，重新 prepare 当前批次。

界面和已有译文一起切换。没有译文的条目显示原始内容。译文在引文卡中标记为「译文」，展开「原文引用」即可看到同一个引用 ID 对应的逐字记录。语言切换只影响展示。检索仍使用现有的主题、实体、Facet 和原文索引。永久删除来源时，KITE 清除工作区的派生译文，便于从保留的数据重新生成。

## 演示顺序

1. 用中文打开记忆图，确认主题层级、实体、Facet 与原文路径出自同一份工作区。
2. 点 **English**。确认目标主题和片段标题切换，展开译文卡下的原文，核对同一个引文 ID。
3. 在「问题」模式提交“最新确认的发布日期是什么，谁负责？”看见编译反馈，再打开命中的原文和检索轨迹。
4. 在记忆图谱上方切换 **Kite by Memoket / 其他玩家**。「其他玩家」将同一工作区的原文按每段最多 600 字符切成等大的绿色立方体。放大时，画面空间足够的方块会显示对应切段的开头几字。选中方块可打开原文位置。检索结果页的切换沿用本次检索引用的来源。切回 Kite by Memoket 即可沿紫色分层的主题、实体、Facet 和引用查看关系。

「其他玩家」视图展示固定长度切段的基线。它用于比较两种记忆组织方式，检索质量和速度需用独立的端到端评测衡量。
