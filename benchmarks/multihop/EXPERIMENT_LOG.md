# KITE 多跳问答实验迭代记录

**当前结论：2Wiki同100题：最多2轮Compile为52.00/58.32，三轮51.00/58.09；两轮冷启动折算token减少25.1%（EM/F1，%）。**

本轮仅修改共享Answer提示词：同100题固定计划／证据对照 **56.00/62.89**（原52.00/58.32）；这是Reader对照，尚非新一轮完整端到端或独立验证。

最新500题：**49.60/59.27**；原100原样复用，新400完整执行同一原生方法。500题均为已暴露开发题，非盲测。

协议：Luna none单轮提取；Luna medium编译，每轮候选1；当前候选最多2轮，三轮版保留作对照；4o-mini回答一次。语料为论文表2的共享支持/干扰库。三组共用配置，无向量、无标签输入或数据集专用规则。单轮版保留作对照。

## 当前进度

最近提取快照：**北京时间 2026-11-01 14:10:17**。下表为当时计数；提取和原评测队列当前保持暂停，本轮只运行Answer对照。

| 数据集 | 已成功提取／总段数 | 已记录 facts | 实际状态 |
|---|---:|---:|---|
| MuSiQue | 9,760／11,656 | 48,349 | 已暂停（历史快照计数） |
| HotpotQA | 8,915／9,811 | 47,660 | 已暂停（历史快照计数） |
| 2Wiki | 6,119／6,119 | 29,183 | 完整 |

原提取／评测队列保持暂停；用户单独授权的2Wiki500并发评测已完成。恢复原队列前须核对其旧冻结Answer，不自动扩1000。

## 迭代摘要

EM/F1单位为%。小试均为开发集，不与论文1000题直接比较。

| 迭代／范围 | 主要改动 | 结果与决定 |
|---|---|---|
| 早期小池 | 修日期/source重复、scope截断、topic与字段契约；撤销误改的实体扩展 | 有改善也有回退；旧小池及向量混合结果不计作当前全库成绩 |
| 全库提取 | Luna改为none；与Mini同源抽查，沿用KITE字段与归一化 | 未见Mini稳定更优，保留Luna；不做语义后处理 |
| 2Wiki原百题基线 | 每题搜索完整6,119段共享库 | **23.00/26.23**；2题运行错误按0分，67题缺至少一个支持段落 |
| 短Compile候选，10题 | 1,093→403词 | **30.00/36.67**，同题原版40.00/40.00；不采用 |
| 单轮修正版，20题 | 恢复RRF参与打包、限制citation数组；Answer澄清所问类型 | **55.00/58.33**，同题旧基线30.00/30.00；零运行错误，扩百题 |
| 单轮修正版，100题 | 同一冻结方法，原样复用前20题 | **39.00/42.02**；零运行错误；较原百题基线提高16.00/15.78个百分点 |
| 最多3轮Compile，同20题 | 后续编译读取真实证据，修订完整原生计划 | **70.00/77.08**；零运行错误，扩百题验证 |
| 最多3轮Compile，同100题 | 同一冻结方法；复用相同首轮计划 | **51.00/58.09**；较单轮提高12.00/16.08个百分点，零运行错误 |
| Reader两项对照，同20题 | 显示已有who；把问题移到证据后，均固定原计划／证据 | **40.00/49.00；30.00/40.43**，低于同题45.00/56.50；均不采用 |
| 轮数成本审计，100题 | 离线重放已执行计划，不调用模型 | 1/2/3轮取齐支持段落**36/84/86**；据此小试减少轮数 |
| 最多2轮Compile，同100题 | 仅减少轮数；20题持平后扩百题，按完整请求缓存复用响应 | **52.00/58.32**；三轮51.00/58.09，零运行错误；冷启动折算token少25.1%，待跨组验证 |
| 提示词对照，另20题 | Compile原文查看提示84词；Reader比较指令180→168词，分别测试 | 原文提示**50.00/56.71**（对照50.00/56.50）；Reader **35.00/49.15**；均不采用 |
| Answer修复，同100题 | 180→167词，澄清最终类型、完整名称与简洁表达；固定计划／证据，20题过门槛后扩100 | **56.00/62.89**（原52.00/58.32），8题EM增益、4题回退；1次断连计0，作为共享Answer候选采用 |
| Answer单句变体，同20题 | 去掉比较指令的候选／yes-no并列表述 | **35.00/41.60**，低于第一版同题50.00/56.85；不采用、不扩100 |
| 扩展2Wiki至500题 | 冻结原生cap2＋Answer167词；原100全复用，新400以12进程并发，约14.6分钟 | **49.60/59.27**；原100为56.00/62.89，新400为48.00/58.36；2题运行失败计0，未改方法 |
| 原文展开修复，同40题 | 已检索来源完整显示一次，撤除旧400字符截断；原计划／证据行／Answer不变 | **42.50/53.63**，对照42.50/51.34；EM持平，不扩500；83项测试通过 |
| Answer逻辑候选1，同40题 | 共享162词，明确关系终点和比较对应 | **30.00/44.57**，3次连接失败计0；短答案约束减弱导致整句输出，不采用 |
| Answer逻辑候选2，同40题 | 156词，恢复严格短答案；共享关系方向／代际、比较对应与日期语义 | **47.50/56.70**，对照42.50/51.34；2题EM增益、零回退、零运行错误，通过门槛后冻结扩500 |
| Answer逻辑候选2，扩500题 | 原40全复用，剩余459次Mini＋1题旧Compile失败沿用；无新Compile | **45.60/55.11**，低于49.60/59.27；16题增益、36题回退，不采用156词候选 |
| 原文修复隔离，同100题 | 恢复原167词Answer；40全复用、固定新增60题 | **50.00/59.20**，对照48.00/57.38；4增益2回退，零运行错误，未达预设净增3题门槛，不再扩500 |
| 跨轮保留v1，同40题 | 固定历史两轮计划、40条预算、原167词Answer；阶段保留标记＋跨轮RRF | **50.00/57.18**，对照52.50/59.68；发现实现漏保留unit行，保留负面结果，补单测后同题复验 |
| 跨轮保留v2，同40题 | 修复漏unit，保留所有原生非count行；其余同v1，完整请求相同才缓存 | **50.00/57.18**，对照52.50/59.68；0增益1回退，零错误，不扩100/500；撤回主代码，仅封存实验 |
| Answer按来源分组，同40题 | 同计划／证据／167词提示词，只按unit组织原有内容；无额外检索 | **55.00/59.673**，对照52.50/59.682；亲属2增益、比较1回退，净增1未过预设门槛，不扩余60；主代码撤回 |
| Compile整词说明，同20题成对 | 固定首轮，第二轮共享完整反馈，Luna medium；Mini回答一次 | 两组均**25.00/33.00**；未生成词边界，未扩跑 |
| Compile整词语法，同20题 | 同一句改为通用JSON正则语法，词数1091不变；原对照全缓存 | 生成17个有效词边界、无占位词复制，仍**25.00/33.00**持平；不采用，不扩跑 |
| 实体身份优先，同100题 | 固定最终计划；直接／注册别名排在纯类别扩展前，原分数档内保序；100题候选与范围完全不变 | **50.00/59.20**持平；完整支持原文覆盖85→88。仅5个输入改变，API前改为一次冻结100，其余95严格缓存；未过门槛，撤回主代码，不扩500 |
| 单次Reader证据表v1，同40题 | 142词＋basis响应字段，计划／证据不变 | **40.00/48.39**，对照52.50/59.68；1增益6回退，不采用 |
| 单次Reader证据表v2，同40题 | 149词，先声明所问目标和答案类型 | **47.50/51.86**；3增益5回退；5道Wh问句误判boolean，不采用 |
| 单次Reader证据表最小对照v3，同40题 | 保留原167词全部检查／答案约束，仅加basis说明与字段顺序，181词 | **47.50/53.81**；2增益4回退，不采用，不扩跑 |
| 500题因果审计，冻结原方法 | 重放500，逐条归档252失分；32题表示对照、16题候选顺序、8题虚构、6项原生控制 | 498正常包完全复现；交换候选使12/16换答案，首项选择13→14/16；虚构8/8。117次Mini、56.13万tokens，0提取/Compile；原500分数不变，详见causal-audit-500-v1/REPORT.md |
| 实体引用＋identity，32题四组 | 隔离实现两项DSL能力；相同原生cap2、Mini167词与预算40 | 原版56.25/63.63；引用50.00/56.68；identity53.13/59.03；组合56.25/63.89，3增益3回退；组合任一轮引用25/32、最终16/32，identity6/32；不采用 |
| 同一语法说明修正，原顺序前8题 | 补齐允许字段及JSON语法、消除扩展说明矛盾；无新执行机制 | 组合4/8实际引用＋identity，类别扩展0；EM仍50%，F1低于原版。合计381HTTP、337.59万tokens、0未知；92回归及498旧包重放通过，保留隔离代码、不扩500 |
| Luna第二轮反馈语法冲突，同8题 | 固定旧第一轮证据，单句纠正实体引用说明；第二轮Compile后离线执行 | 新旧均5 stop、3查询；0题用新引用，未测Answer/EM；8次Luna、9.50万tokens，保留结果 |
| 原生scope过滤修复→Answer，同32题 | 固定16检索错题＋16原正确题、历史最终计划；原生放松按显式文档scope执行，再单独试167词Answer | 对照56.25/61.28→检索59.38/67.60，1增0退、原正确16/16；净增未达2题门槛。Answer53.13/61.43，0增2退，不采用；79次Mini、56.91万tokens、86测试通过，0新Compile/提取、不扩500；详见scoped-retrieval-repair-v1/REPORT.txt |

| Answer回退逐题复核，6题零API | 复现12个检索包、核对18份模型输出；修正标题误算源文的诊断bug | 实际完整来源15/32→19/32，分数不变；两回退为缺日期/引用错人拒答；恢复后2题比较错、1题长句且studied≠graduated；保持原Answer、不扩跑，详见scoped-retrieval-repair-v1/failure-review/REPORT.txt |

| title挤掉事实＋Compile末跳身份修复，32题 | 同库、原167词Answer；固定Plan只改3个输入；另新鲜两轮Compile对照 | 只打包覆盖19→22、EM/F1不变且原正确16/16；端到端50/58.90→50/56.60，2增2退、来源25→29，不扩500不切主线；195HTTP、164.80万tokens、91测试；详见delivery-identity-repair-v1/REPORT.txt |

| 交付后Answer与上下文离线审计，13题 | 13份实际提示逐字重放，沿Plan/词面通道查引用来源；零API、不改提示 | 主类6比较/3错主体/1谓词/2粒度/1口径歧义；平铺丢近邻身份和阶段结构，词面错人材料实际被采用；未证明呈现的独立因果，详见delivery-identity-repair-v1/reader-context-audit/REPORT.txt |

| 按来源/主体组织上下文＋原Compile结构，固定32题 | 原Plan/事实/原文/引用集合不变，Answer167词不变；两组Mini各32次 | EM53.13→53.13，F1 58.33→61.11，2增2退（一称号、一比较真退），历史正确15→14/16；97测试、50.27万tokens、输入+17.62%；候选保留不设默认，详见source-subject-context-v1/REPORT.txt |

| 冻结证据呈现，22题：6日期错题＋16历史正确 | 来源/主体分组对照，仅将完整原文后置，原Plan/Answer/每条证据及次数不变；44次新Mini | EM59.09→63.64、F1 61.36→65.91，1增0退；重点7日期题仍0/7，增益#88历史曾正确，未达门槛，不扩500；36.16万tokens，详见context-facts-first-v1/REPORT.txt |

| 双日期引用错题离线审计，7题14份原请求 | 导出完整提示/schema/原始响应/引用映射，零API、不改运行代码 | #384直接比较输出矛盾；#218缺连接引用，#275/#226混有生卒等属性；引用正确不等于实际取值/比较正确，不能唯一归因；详见context-facts-first-v1/comparison-citation-audit/REPORT.txt |

| Answer取值/比较诊断，冻结22题 | 同证据Plan，一次Mini新增observations/comparison；44新调用，不自动修答案 | EM68.18→86.36、F1 70.45→88.64，5增1退；#112取对1903/1902仍判older反向，#276新增拒答，部分字段对调/2023无来源；38.73万tokens，不扩量；详见answer-value-trace-v1/REPORT.txt |

| Answer共享规则修订，固定22题，254→251词 | 同证据/Plan/schema，修older、角色、年龄来源与非比较/关系支持，44新Mini | EM86.36→90.91、F1 88.64→93.18，#276恢复，1增0退；目标角色纠正，但#112仍older反向、#196仍2023年龄，诊断字段有新回退；41.31万tokens，不默认/不扩量；详见answer-value-trace-repair-v1/REPORT.txt |

| older/无依据年龄两题完整上下文审计 | 4份完整原请求/回执，零API，不改提示与证据 | #112上映年1942/1947与导演出生先后相反，近似片名/无关older材料实存；核心4文档外源文字符87.36%；#196无2023/73/62来源，年龄为无据外推；干扰因果未验证，详见answer-value-trace-repair-v1/context-interference-audit/REPORT.txt |

| 上下文整文档消融，同22题 | 冻结Compile/251词Answer，按问题与原文关系人工留文档、无gold筛选；引用枚举同步缩小，44新Mini | EM86.36→81.82、F1 88.64→84.09，1增2退；#112 older修复、#196不再编造年龄，但#226/#394日期正确仍比较反；历史正确16题无新增回退，输入−71.57%，26.82万tokens，不采用/不扩量；详见context-document-ablation-v1/REPORT.txt |

| 原文行预留＋Compile末跳范围，固定32题续查 | 固定首轮计划/最新251词Answer，补parallel lines既有预留，Compile仅改现有文档scope用法；128新HTTP | EM81.25→75.00、F1 86.46→79.69，0增2退；#252实体类别扩展＋grep漏原文使主体源在检索阶段丢失，#363证据在但答null；固定计划重放证明打包修复未影响本批证据；94检查、142.48万tokens，不采用/不扩量；详见delivery-identity-repair-v2/REPORT.txt |

| 实体扩展→文档范围边界，同32题冻结Plan | 不改Compile/Answer；供后续文档引用的实体facts用现有直接/别名提及限定units，再走原生放松；31输入不变共享新响应 | EM75.00→78.125、F1 79.69→82.81，#252恢复、0回退；范围62→2文档、来源28→29；101检查、33Mini/32.99万tokens，0Compile/提取；仅1题触发，诊断字段仍有错，不扩量；详见entity-scope-boundary-v1/REPORT.txt |

两轮版反馈触发100%，77题实际执行第二轮检索；每题一次Answer。冷启动折算213万token（三轮284万）；轮数对照实际新增31次Mini、17.97万token，无新Compile请求。原文提示与比较指令小试另用20次Compile、40次Mini，共44.48万token。

补充计分：官方2Wiki别名下，同一百题答案两轮为 **57.00/62.77**，三轮57.00/63.54；原计分保留，预测未改，不算模型提分。论文是否采用这些别名未核实。

## 问题与下一步

- 500题EM失分252题：24题官方别名可认可、57题缺标注支持段落、2题运行失败、169题覆盖支持段落仍错。169题逐题审查：87读错／答非所问、35表述或范围、22语义／标注歧义、19所问属性不足、3明确漏取、3混合归因不明。支持段落覆盖不等于完整证据链。
- 新400题明确Answer错误74/400（18.5%），原100题13/100；缺支持段落的错题50/400（12.5%），原100题7/100。样本扩大同时暴露取证与回答问题，当前不改机制或重建底库。
- 源文抽查6段34事实，发现topic缺失、event误标及infobox漏项；不回填或重建，先验证QA影响。LoCoMo／LongMemEval绑定保持不变。

本轮Answer两个候选共120次Mini请求（第一版100、被拒第二版20），已知66.90万tokens；1次断连用量未知，无重试、无新Compile。第一版新增8个EM命中中至少3个仍缺完整证据链／属性，不能全算推理修复；另有名称、答案范围和复合国籍粒度影响。

500扩展新增1,198次请求（Luna799、Mini399），已知855.87万tokens，3次连接中断用量未知；其中1题初次Compile失败，2题后续Compile失败沿用前轮证据。原100本轮零新增请求。拒答9/500（1.8%），与2题运行失败分开。57道缺段落错题中11题有缺失源进入候选池后被打包丢弃，46题更早缺失；这仍不是语义因果证明。

本轮源文与Answer试验共642次Mini请求，已知用量见最新结果索引；6次连接失败用量未知，无重试或新Compile。500候选36个EM回退中9个错答Yes/No、7个答案包含gold字面但带额外文本（不证明语义正确）、20个其他属性／方向／身份差异；没有EM回退来自运行失败。原167词源文修复复核：UCLA仍误选任教学校，Waspman病因恢复完整，Maurice父亲仍串错，未宣称Answer已修好。

轮间零API重放：历史100题中77题实际执行两轮，75题最终包缺少部分首轮行，不能据此断言必要桥接丢失。保留跨轮证据／按来源组织Answer输入仅为待验证提案，尚未实现。

跨轮保留审计：33题实际执行两轮，首轮行保留率63.86%→82.12%，阶段预留61/106→106/106；完整支持段落覆盖33/40→31/40，保留更多不代表保住语义链。抽查新增无关人物材料挤掉John Ernest II、Eduardo De Filippo文档；另1题支持仍在却由Indian变拒答。两版共42次Mini、37.03万已知tokens、38次完整输入缓存命中，0新Compile、0未知用量。冻结计划的打包消融，不作新端到端或盲测结论；主线恢复后83项测试通过，实验86项测试封存。

本轮分组＋Compile词边界试验合计133次新HTTP（Luna60、Mini73）、138.32万已知tokens、67次完整输入缓存命中，0未知、0重试、0提取。零API审计100题：15题实体类扩展候选多于直接命中，34题过滤放松（主要grep），4题有直接候选时类别候选仍进得分前二；这是排序风险，非全部错误的因果证明。分组比较回退引用1738与1986却选错较晚出生者；两道亲属新增命中中一道引用缺起始关系环节。所有预测冻结后独立评分，主代码／提示词恢复，83项回归通过。

本轮实体排序只新增5次Mini、55,746 tokens，0新Compile、0未知用量。新增覆盖的3题此前EM已命中，故没有新增正确题；另1道亲属错题由父亲改答丈夫，仍未到达岳父／公公这一最终关系目标。500题若所有403道段落已覆盖题都精确答对、其他答案保留，乐观EM/F1为88.4/89.4（含修复1道Reader连接失败）；不含该运行恢复则88.2/89.2。此为段落覆盖代理上限，不是事实链充分性证明。

纠正归因：原对照40题Who/Which答Yes/No为0，后续类型修补主要在修试验新引入的问题。原3道比较失分分别含明确年份比较选反、缺一方国籍来源、from-country口径歧义，不能统称Answer错误。前两版证据表改写原提示词有混杂，最小对照仍回退，未证明表格有益。本轮120次Mini、914,468已知tokens、0未知／0新Compile；原生极值计划仅离线预检，未启动付费。先复核原始控制的必要事实可见性，再做单因素因果对照。

## 资料

[500题结果与逐题审查](../../artifacts/results/multihop/native-answer-v1-n500-parallel/FINAL_REVIEW.json) · [本轮Answer对照与逐题审查](../../artifacts/results/multihop/reader-answer-contract-v1-n100/FINAL_REVIEW.json) · [详细结果索引](EXPERIMENT_RESULTS.json) · [完整历史存档](../../artifacts/results/multihop/record-archive/EXPERIMENT_LOG_DETAILED_20261101T044336Z.md) · [两轮百题调用与成本审计](../../artifacts/results/multihop/full-native-v8/n100-native-feedback2-v1/2wikimultihopqa/PAIRED_SCOPE_AUDIT.json) · [完整实验约束](README.md)

后续每次只补一行“改动—结果—问题／决定”；状态原位更新，进程、缓存、失败恢复和原始提示词留在artifact中。

- 剩余7错题联合32题对照：原生cap3／Answer观察契约单条修订（251→242词）／组合，EM/F1由新鲜对照78.13/85.59变75.00/81.08、75.00/81.77、71.88/77.95；参考来源覆盖29→31但7错题零EM修复，新增older与born-earlier回退；主体串线、同名亲属混淆仍在，3题含答案粒度差异。96次新HTTP、990,457 tokens、0未知；完整性核验通过，均不采用、不扩量，保留主体范围修复及原Answer。[完整对照](../../artifacts/results/multihop/remaining-errors-joint-v1/REPORT.txt)。

- 500题错题状态登记：逐题抽出原252个严格EM失分，分10主类；当前保留32题分支只覆盖其中16题（10恢复EM、6仍失分），236题未由该版本复测；检索／交付类10/55=18.18%仅是EM恢复进度，无整类已解决，不拼历史最好答案。零API；[全量清单与状态](../../artifacts/results/multihop/error-register-500-v1/index.html)。

- 当前保留分支完整500题复测：全部重新Compile+Answer，原配置/库/提示词冻结，无新机制；EM/F1 49.60/59.27→58.60/68.02，原252错题恢复82、原248正确回退37（净增45）；当前207错题已全量归类，旧10类解决状态全部更新。1500HTTP、14,392,561 tokens、0错误/未知/重试/提取；500份请求/证据重放与原始输出核验通过。[最新全量状态](../../artifacts/results/multihop/retained-native-n500-v1/index.html)。

- 500题失分环节复核，零API：207题最终计划离线重放逐行一致；37回退为21道Answer比较/关系/主体、3道取证交付、13道表达/别名/口径/来源不足；17道主要上游问题分10查询未产出、3标题无正文、3候选终包丢失、1查询排序截断。确认death字段为空被硬过滤、Frederick II/III身份变体、German词面排除Austrian等契约断点；未改机制/提示词/评分。[逐题环节报告](../../artifacts/results/multihop/retained-native-n500-v1/layer-audit/REPORT.txt)。

- 四项上游修复40题新鲜配对（17目标错题+23正确控制）：Compile替换两段规则、补selected-unit正文与引用，Answer冻结；EM/F1 72.50/73.21→62.50/64.88，1得5失、2道正确控制回退；标题交付代码缺口补齐但未单独证明提分，别名仍未连通，属性grep放松过头（59/66→13/71）。95测试与80份执行校验通过；240HTTP、2,273,637 tokens、0未知/新提取。未通过门槛，不采用候选Compile、不扩500、保留原版。[完整报告与逐题原始记录](../../artifacts/results/multihop/upstream-contract-repair-v1/REPORT.txt)。

- 回退上轮Compile提示词，仅单测unit正文与引用补丁：固定原500留存中的同40份计划/selected rows，两组各40次新Mini；EM/F1 60.00/60.45→62.50/62.95，#250恢复、0回退、23/23正确控制保持。4题新增9个source、其余36题请求完全相同；80HTTP、799,531 tokens、0新Compile/提取/未知。别名8题查明：库30497实体/0 alias，2题正确实体code已在桥接fact却没交给Compile，其余涉及未登记别名/缩写/重音/同名；未改库或提示词，不扩量。[正文单变量对照与别名审查](../../artifacts/results/multihop/unit-delivery-only-v1/REPORT.txt)。

- 已有实体字段交付40题：首轮plan/rows固定，两组保留正文补丁与原Compile/Answer，仅反馈附已选fact.entities（4KB/120代码上限；平均+1192、最多+1578 tokens，0省略）；EM/F1 70.00/72.38→67.50/68.67，0得1失(#52)，23正确控制全保留，来源覆盖30→26。8断连题0修复：2个正确code送达却未用于实体查询，其他6仍缺身份连接；另查明末跳entity+born硬过滤可因164条扩展命中掩盖目标缺失。101测试/80执行核验通过；160HTTP、1,964,245 tokens、0未知/提取。不启用此候选、不扩量，保留正文补丁。[完整记录](../../artifacts/results/multihop/compile-entity-delivery-v1/REPORT.txt)。

- 全500错题再归因（零API）：覆盖207严格EM失分（37回退+170旧错题），原65个Answer主类复核后为58个明确输出错误；8个共有国家判断单列。最终计划43题未返回参考文档，其中25题由其他通道补齐，揭示仅看最终来源覆盖会低估Compile问题；58个Answer错误中亦有11个此类上游缺口。29题所核查原文属性/关系依据不足、28题官方别名已接受；全部逐题与重叠边界保存，未改推理/评分/库、不扩量。[全207题复核](../../artifacts/results/multihop/all500-error-stage-audit-v2/REPORT.txt)。

- 末跳范围40题：17明确错题+23正确控制，固定第一计划，两组新Luna后续Compile+Mini。联合候选EM/F1 62.50/73.10→72.50/76.18，5得1失、23控制全保留；但#110/#387答对未修查询、#447语义回退，未采用Compile说明。仅代码固定计划消融62.50/73.10→65.00/75.60，#348根因恢复、0回退（37完全相同请求缓存+3新Mini）；保留末跳obj扩展范围代码修复，Compile/Answer不变、不扩500。102测试、120最终包重放；总163HTTP/1,895,956tokens/0未知与提取。[报告](../../artifacts/results/multihop/terminal-subject-scope-v1/REPORT.txt)。

- 用户决定保留854词Compile改写：当前候选切回terminal-subject-scope-v1/candidate/runtime（含末跳范围代码修复），Answer不变；40题72.50/76.18仍为已有小样本结果，未新增API或扩500。17目标题余11失分：7条链继续/范围问题、2主体属性混配、1同名身份、1Answer末跳；另#110/#387虽EM正确但计划仍有缺陷。此前不采用说明的决定保留为历史快照。[当前状态与未修项](../../artifacts/results/multihop/terminal-subject-scope-v1/REMAINING_CURRENT.json)。

- 全207错题共性复核，零API/不改推理：原500净增45正确中42来自日期比较，桥接属性70/199、复杂亲属18/51；14题units使用无效文档ID（11失分/3正确），43最终查询缺参考文档中25由其他通道补齐；末跳类别扩展代码问题在旧207最终查询中仅2题。全500正确组参照证明Answer诊断字段异常不能直接作因果；纠正#303为great-grandparent混用。逐题标签覆盖207，保留当前854词Compile及原Answer，不扩量。[共性报告与全量清单](../../artifacts/results/multihop/all500-common-patterns-v3/REPORT.txt)。

- ID/主体交付40题（24错+16正确）：保留854词Compile与251词Answer，ID归属解析＋复用来源主体分组反馈，新鲜配对EM/F1 42.50/57.30→37.50/54.20，0增2退；#353/#447语义回退，未采用反馈格式、不扩500。单独固定对照计划验证ID代码：39完整请求缓存＋1新Mini，42.50/57.30持平，16正确全保持；仅保留ID解析，未知ID不猜。联合110/保留107检查、120最终包精确回放；161HTTP、2,021,505tokens、0未知/重试/提取。末跳主体身份仍未解决。[完整报告](../../artifacts/results/multihop/id-subject-contract-v1/REPORT.txt)。

- #353/#447 stop原始请求审计（零API）：纠正“主体证据不足而早停”归因；两题完整关系链在Compile前已可见，且完整送达Answer。现有stop按证据充分性结束检索，并保留旧plan/rows；Answer分别错换人物/少走一代。4个Compile＋4个Answer完整HTTP原件导出，证据集合两组一致；未改提示词/代码/保留版本/评分、不扩量。[原始提示词、响应与审计](../../artifacts/results/multihop/id-subject-contract-v1/stop-audit/REPORT.txt)。

- 仅第二轮stop条件，同40题（24错+16正确）：31→52词，每请求+23tokens，其余代码/Compile模板/Answer冻结。新鲜配对EM/F1 37.50/53.78→42.50/56.17（#106/#362/#475增，#301拒答回退）；stop8→3、无主体范围计划3→0、参考内容查询26→29、原文交付33→34。#106/#475关系查询改善，#362仅正文补回；#353/#447两组本轮都成功，非独立收益。原正确15/16→15/16但错题换人，未通过无新增回退，候选封存不替换当前、不扩量。160HTTP、1,946,756tokens、0未知/重试/提取，80最终包重放一致。[完整结果](../../artifacts/results/multihop/compile-stop-scope-v1/REPORT.txt)。

- 用户改为先跑2Wiki完整1000题：冻结stop-scope候选（Answer及其余逻辑不变），跳过本轮3×50后续对照；正在生成的150份首轮计划并入全1000以免重复，所有1000答案新生成。题目为Hi-Q原顺序1000，旧retained-native-n500-v1严格对应前500，仅作历史累计回退比较。离线40回放与150首轮幂等检查均通过，已于15:12UTC启动8路并发；全部预测冻结后核验原始HTTP与证据交付再独立评分，尚无全1000成绩。[运行目录](../../artifacts/results/multihop/wiki2-1000-iterations-v1/iteration-01-stop-scope/full1000/STATE.json)。

- 冻结2Wiki全1000完成：EM/F1 **60.90/68.88**；前500较历史58.60/68.02→59.00/68.24，31增29退净+2，非单变量因果对照。391失分尚待全量新归因；5次首轮Compile失败、3次后续失败，2950HTTP含150本轮首轮复用，27,454,756tokens、0未知，0提取。[完整结果](../../artifacts/results/multihop/wiki2-1000-iterations-v1/iteration-01-stop-scope/full1000/REPORT.txt)。

- 编译契约修复：8异常分3引用/2pipe/1拼写/2超量；可选schema＋完整原生pipe格式，Answer/检索/预算不变、无修复重试。20请求新鲜配对两组均20/20合法，尚不能证明降低失败率或提分；3题仍查错主体来源、1题末跳属性未交付。首版混合stop回退已封存，累计78HTTP/682105已知tokens/2未知400/0Answer；候选不推广、不扩量。[原因、验证与决定](../../artifacts/results/multihop/wiki2-1000-iterations-v1/compile-contract-fix/REPORT.txt)。

- 全1000离线边界审计：995最终包精确重放＋5首轮失败；391失分中303参考原文全交付、83缺源、5无答案，不能将303直接归Answer。29历史回退逐题复核完成，362其余失分语义审查待完成；主要题型差距为关系组合EM38.01而普通比较89.75。0API/0推理修改，先完成归因再验证修复。[全量边界与29回退报告](../../artifacts/results/multihop/wiki2-1000-iterations-v1/iteration-01-stop-scope/full1000/layer-audit/REPORT.txt)。

- 全1000失分审查推进100/391（含29回退）：16篇候选丢失中14篇在原生查询已缺失；标题/正文实体断连出现66题（35错31对），17错题同时缺相应来源，尚不证明修复收益。纠正UCLA为未取源、#110为Answer混主体；0API/0推理改动，不扩量。[逐题100审查与剩余291登记](../../artifacts/results/multihop/wiki2-1000-iterations-v1/iteration-01-stop-scope/full1000/layer-audit/ALL391_REVIEW_REGISTER_OFFLINE.json)。

- 原生首轮反馈触发对照3×100（共享本轮完全相同请求响应）：EM/F1 56.00/64.65→56.33/64.98，1增0退；12题新增第二轮，288题输入输出完全不变，#56补齐导演配偶原文。604HTTP、6,815,707tokens、0未知；p=0.5，不扩量，错题审查29/132完成。[对照结果](../../artifacts/results/multihop/wiki2-1000-iterations-v1/compile-contract-fix/initial-feedback-check/GATE_ANALYSIS_OFFLINE.json)。

- 用户批准Answer观察结构小试3×50：删除每条candidate、允许实际中间关系，251→245词；冻结Compile/证据，EM/F1 56.67/64.51→60.00/67.78，7增2退，67增益/错题全部审查。祖父多走一跳及年份比较反仍回退；同题历史保留版60.00/67.53，p=0.0898未达预设门槛，候选封存不替换。300MiniHTTP、3,217,863tokens、0新Compile/提取/未知。[分类与决定](../../artifacts/results/multihop/wiki2-1000-iterations-v1/answer-observation-contract-proposal/trial-random3x50/DECISION.json)。

- 2026-11-02：固定51关系/主体错题+99原正确题，仅移动Compile计划至证据末尾；Mini150/臂，EM68.00→64.67，F172.81→70.65，4增9退；58题逐题复核完，不采用、不扩量。300请求/3,079,464tokens，0未知。

- 2026-11-02：冻结同150题Compile/证据/原布局，复验已批准短版Answer（251→245词）：EM/F1 68.00/72.81→74.00/77.41，关系0→9/32、主体4→11/19，但原正确98→91/99。16增7退、59题逐题审完；回退含1代码名、5比较/映射、1多走祖先，未过回退门槛，不采用不扩量。300Mini/3,080,110tokens/0未知、0Compile/提取。[报告](../../artifacts/results/multihop/wiki2-1000-iterations-v1/answer-source-relation-contract-focus-v1/REPORT.txt)。

- 2026-11-02：原7回退归因（3方向、1映射、1结论字段冲突、1代码名、1多一代）。同150三臂隔离candidate字段：原版66.00/71.72、短版76.00/79.87、恢复字段70.00/74.88；原7全对但目标51从20→8，62案例审完。再试字段可空/仅题目选项（249词），同期短版75.33/78.61→70.00/74.06，9增17退，63案例审完；目标51无一使用null，起点仍被当候选。两版均拒绝、不扩量，Compile/证据/主线冻结。750Mini/7,701,581tokens/0失败未知。[完整报告](../../artifacts/results/multihop/wiki2-1000-iterations-v1/answer-nullable-option-binding-v1/REPORT.txt)。

- 2026-11-02：仅comparison职责说明245→263词、schema/Compile/证据冻结，同150新鲜双臂EM/F1 72.00/77.10→69.33/73.87，9增13退；原正确99题90→86（4增8退）。88失分逐条归因：事实11→11、关系22→17、最终选择4→12；名称边界4→5、缺证据1→1，69对案例审完。新增6题正确比较/选项却最终Yes/No。候选拒绝、不扩量；300Mini/3,085,556tokens/0未知失败。[报告](../../artifacts/results/multihop/wiki2-1000-iterations-v1/answer-comparison-derivation-v1/REPORT.txt)。

- 2026-11-02：仅comparison末句263→264词，同150新鲜双臂，字段/Compile/证据冻结；EM/F1 70.00/73.10→74.00/77.01，13增7退，原正确85→90/99但4回退。重点5题2→3正确（2增1退，506/709未修），789映射及答案本轮正确；误答布尔9→3。25题审查覆盖所有增退；533/596虽EM正确仍有错误关系标签。不推广、不扩量；300Mini/3,087,493tokens/0失败未知。[报告](../../artifacts/results/multihop/wiki2-1000-iterations-v1/answer-selected-option-render-v1/REPORT.txt)。

- 2026-11-02离线复核506/709/679/229：前三题正确选项已生成但answer转答中间命题真假；229关系句正确却选较年轻导演的电影，无法仅凭日志区分胜者选错与电影映射反。44道电影选择题中7道布尔result有4道答对，故不能把布尔result当充分根因；证据/日期齐全、无截断/解析改写。0API/0代码提示词改动/0扩量。[完整输入输出与归因边界](../../artifacts/results/multihop/wiki2-1000-iterations-v1/answer-selected-option-render-v1/FOUR_PERSISTENT_FAILURES_AUDIT.txt)。

- 2026-11-02：仅result职责264→266词，字段/Compile/证据冻结，同150新鲜双臂EM/F1 72.00/74.91→70.00/73.90，8增11退；原正确88→87/99、7回退。重点506修回/709回退/679仍错/229两组对；31导演比较仅1题result明确导演。87失分全分类、54对审完：主体23→23、映射2→1、最终交付4→3、主体/映射不明1→4；其余单列。候选拒绝、不扩量；300Mini/3,087,865tokens/0失败未知。[报告](../../artifacts/results/multihop/wiki2-1000-iterations-v1/answer-result-selected-subject-v1/REPORT.txt)。

- 2026-11-02：同150冻结Compile/证据，测试selection+观察索引映射+唯一answer.value（266→270词），新鲜双臂EM/F1 70.67/74.37→63.33/68.15，10增21退；原正确88→75/99、17回退。重点7题旧5/7、新3/7，474答案对但selection仍错，591索引越界。67对/99失分审完；新11布尔误答、8整句比较。转换遗漏旧without explanation，故不是纯结构因果对照；映射字段在答案后生成。保留原始失败、不采纳/不扩量/不补句重跑。300Mini/3,091,618tokens/0失败未知。[报告](../../artifacts/results/multihop/wiki2-1000-iterations-v1/answer-selection-mapping-structure-v1/REPORT.txt)。

- 2026-11-02：用户指定264词版全1000（263词同期对照），冻结Compile/证据/字段。EM/F1 55.80/63.71→59.90/67.25，66增25退、净+41；10百题批9正1平。原诊断150仍74.00EM，其余850为57.41；误答布尔73→22。未超第一轮60.90/68.88，且第一轮与本轮834题证据行不同，不能当纯Answer历史对照。前100当前调用原样接续，无重复；1990Mini/20,828,107tokens/0新失败未知，5冻结Compile失败计零。91对增退输出审完，371对双错未重审；主线不覆盖、无后续自动任务。[报告](../../artifacts/results/multihop/wiki2-1000-iterations-v1/answer-selected-option-render-full1000-v1/REPORT.txt)。

- 2026-11-02：关系绑定/执行状态400新鲜双臂（200错+200正确；现有库、Answer冻结，最多2Compile+1Answer）：EM/F1 **56.50/65.08→49.00/56.71**，17增47退；原正确184→171/200。实际中间引用170/400，首轮失败2→15，来源覆盖330→301。64增退复核；目录/实体posting断连、过严绑定校验和末跳交付未约束是主要新增缺陷，Answer仍错用主体。12测试通过，不推广；含小试2401HTTP、22235464tokens、0未知、0提取。[报告](../../artifacts/results/multihop/wiki2-1000-iterations-v1/relation-state-v1-n400/REPORT.txt)。

- 2026-11-02：用户批准整段原文+LLM索引方向。20段来源小试（1当前示例+19固定哈希选文），Luna none仅实体/topic/facet，无QA输入。首轮topic契约失败，2/20入库；改成code/parent成对输出后18/20入库，余2为名称空格/标签数量错误。18段逐字保真、84原生实体查询和18范围查询交付通过；20段人工复核仍见别名断连、事件推断、限定/实体漏索引。仅benchmark适配，未改正式Compile/Answer/原库，未扩量、无EM/F1。含失败40HTTP/28,945tokens/0未知。[记录](../../artifacts/results/multihop/passage-index-v1/pilot20-contract-v2/REPORT.txt)。

- 2026-11-02：同20段修别名/标签/实体遗漏，4轮单轮索引验证（每轮每段1次，Luna none），80HTTP/74,369tokens/0未知。最新v6：18/20入库，32个表面名称检查23→31覆盖、15组名称原生连通1→15；按显式事件口径推断标签6→0，但10个明确事件覆盖8→6。两源仍因名称空格/消歧页错绑拒收；U.S.和4个事件标签遗漏，未过质量门槛。20源原文不变、无语义后处理、未拼最好结果、未接Compile/Answer/扩量；8测试通过，页面可切v2/v6。[记录](../../artifacts/results/multihop/passage-index-v1/pilot20-field-contract-v6/REPORT.txt)。

- 2026-11-02：同20段继续索引审查；仅保留原生norm_code兼容校验修复，同一组v6响应0API重验入库18→19，原文19/19逐字一致、115实体查询、10测试通过。提示词v7及消息角色v8均回退，撤回；本轮60HTTP/61,562tokens/0未知。保留版清单名称31/32、别名15/15、明确事件6/10；消歧错绑、U.S.及4事件漏标、角色别名风险未解决，不接Compile/扩量。[结果与边界](../../artifacts/results/multihop/passage-index-v1/retained-v6-native-validation/REPORT.txt)。

- 2026-11-02：用户授权当前整段索引扩2Wiki全量6119段后测1000题。冻结v6单轮Luna none，复用同输入20份响应、其余6099份并行；失败和跨文档别名冲突不自动补写/重试。后续最多2轮Luna medium Compile、1次Mini，沿用264词Answer，只适配整段存储说明及原文单次交付。尚在运行，不记作已有EM/F1；12项检查通过、原20段重放XML逐字一致。[运行状态](../../artifacts/results/multihop/passage-index-full-v1/CAMPAIGN_STATE.json)。

- 2026-11-02：整段索引2Wiki全量完成：6119源全部尝试、5346入库（435校验失败＋338别名冲突拒收）；1000题EM/F1 **51.50/57.81**，较最近59.60/67.32下降，69增150退，2编译失败计零。337题缺支持标题文档，150回退中101有此缺口（重叠非因果）；全量原文/原始答案核验通过。新增8974HTTP/24,119,201tokens/0未知；不自动采用或再跑。[完整报告](../../artifacts/results/multihop/passage-index-full-v1/REPORT.txt)。

- 2026-11-03：修复索引失败/别名冲突导致整篇原文未入库：元数据接受规则不变，失败只拒元数据，原文完整写入原生记录。复用6119份响应离线重建：6119/6119原文保真、773段恢复且标题/正文查询通过；旧5346索引及词表精确不变，15测试＋3子测试通过。0API/0新增tokens；元数据缺陷仍记录，尚无新EM/F1。[修复报告](../../artifacts/results/multihop/passage-source-persistence-v1/REPORT.txt)。

- 2026-11-03：用户授权修复后重测1000题；复用完整6119段修复库，QA运行时73文件、Compile/Answer提示词、schema、问题全部与上轮逐字一致，仅换库；32路并行，计划与答案全新生成，不重提、不重试。运行中，尚无新EM/F1。[进度](../../artifacts/results/multihop/passage-source-persistence-v1/EVAL_STATE.json)。

- 2026-11-03：原文持久化修复后同1000题完成：EM/F1 **57.60/64.65**（原51.50/57.81），118增57退、净+61，2编译失败计零。原缺支持标题337题EM28.49→47.18，原齐全663题63.20→62.90；支持标题全交付578→773。运行时/提示词冻结、1000首轮HTTP输入逐字相等，响应全新；完整原文/原始答案核验通过。0重提，2893HTTP/18,248,695tokens/0未知；回退归因待做，无新任务排队。[全量结果](../../artifacts/results/multihop/passage-source-persistence-v1/QA_REPORT.txt)。

- 2026-11-03：修复库1000题离线失分审计，0API。重放998成功题最终行/来源集合全部吻合；424失分分层：2执行失败、139候选池缺支持原文、14入池未交付、269原文齐全待语义细分。57回退对应1/23/0/33；27名称题答Yes/No，其中20内部候选已正确。40行平均21.27来源，确认整段fact/source预算重复；具体关系跳转、亲属少跳、比较方向/映射错误已记录，未改推理或评分。[分析](../../artifacts/results/multihop/passage-source-persistence-v1/error-audit/REPORT.txt)。

- 2026-11-03：424严格EM失分全筛，141近似候选原文复核；保守认可112（80同义/别名/消歧＋32有据具体/完整），29边界候选不计。人工诊断正确率68.80%、将认可题F1置1后70.48%；正式57.60/64.65不改，答案等价不等于推理有据。57回退中4题属此类。0API、无推理改动。[逐题清单](../../artifacts/results/multihop/passage-source-persistence-v1/error-audit/ANSWER_EQUIVALENCE_REPORT.txt)。

- 2026-11-03：排除112语义认可题，重点11入池未交付＋170原文齐全失分。离线来源去重计数51题（含40原正确）恢复9/11支持来源、控制组无原有支持丢失；未测Answer/未采纳pack改动。170全主分类，46关系/主体，19布尔误答（14内部候选正确），17其他数值比较，余项与标注边界单列。5次Compile失败（2首轮、3二轮）实现benchmark可选结构化契约，91测试及1890合法归档输出通过；真实15调用小试待原API配置，0新API，Answer/原评分不动。[报告](../../artifacts/results/multihop/passage-source-persistence-v1/error-audit/focused/REPORT.txt)。

- 2026-11-03：Compile 结构契约真实 Luna medium 小试，5 历史失败＋10 原正确首轮，15/15 合法且原生执行成功，0 stop/0 新编译异常；历史失败组支持全交付仅2/5，88/437/739仍缺末跳，来源引用不等于实体跳转。10控制首轮支持集合与历史相同，未验证答案回退。15HTTP/113,572tokens/0未知/0Answer；未扩量，保持显式opt-in。[报告](../../artifacts/results/multihop/passage-source-persistence-v1/error-audit/focused/compile-probe/REPORT.txt)。

- 2026-11-03：结构Compile契约端到端同15题新鲜双臂（5历史失败+10原正确），EM/F1 **86.67/86.67→73.33/73.33**，0增2退（99/299），控制均9/10；二轮编译错误1→0、支持全交付11→8。99合并主体grep过滤吞掉无born字样的日期原文（离线只拆查询即恢复）；299漏末跳且不触发二轮，旧答案虽对但引用无据。保留历史二轮说明、Answer/打包不动，不推广/不扩量。85HTTP/582,576tokens/0未知。[报告](../../artifacts/results/multihop/passage-source-persistence-v1/error-audit/focused/compile-e2e15-v1/REPORT.txt)。

- 2026-11-03：同20段单轮Luna none补现有who/kind/t索引，旧文档索引冻结、原文20/20不变；新增97原文摘录索引，12时间中11正确、1虚补月份（Linda2013→2013-01），另Vassar/Yale合并行为主体错绑。原生固定来源属性查询严格命中0/13→13/13，同时2负例误命中；非QA指标。发现原生t不支持仅年、who别名不自动连通及复合主体问题；不推广、不扩量。20HTTP/13,840tokens/0未知/0Compile或Answer。[报告](../../artifacts/results/multihop/passage-index-v1/subject-time-pilot20-v1/REPORT.txt)。

- 2026-11-03：同20段修年份/主体索引，保留原生YYYY支持及opt-in已注册who别名（62/62），117测试通过；4轮提取均未过语义门槛，v3原文20/20不变、17年+11日精度保留，但13属性查询13→9，Yale提议漏提及kind/主体仍错。v4/v5枚举被端点拒绝（双引号限制）已撤回；不接Compile/Answer/扩量。81HTTP/54,385已知tokens/11无usage，未拼最佳。[报告](../../artifacts/results/multihop/passage-index-v1/subject-time-fixes-review-v1/REPORT.txt)。

- 2026-11-03：离线核对kind/event消费链路：两者独立倒排、跨字段AND，非自动父子关系；撤回“event非空必为kind=event”全局约束建议。998成功最终计划kind=0、event=56；13→9仅手写索引检查，不是QA退分。明确每段覆盖全部显式关系、每条绑定一谓词，类型语义须与Compile消费共定义。0API/0推理改动。[审计](../../artifacts/results/multihop/passage-index-v1/subject-time-fixes-review-v1/FIELD_SEMANTICS_AUDIT.txt)。

- 2026-11-03：统一提取/Compile字段定义，同20段Luna none单轮覆盖全部明确关系，冻结旧entity/topic/facet；18段新索引合规、113条，2段引用/主体表面改写拒收，原文20/20保留。145来源清单严格匹配84→94（25增15退，含分类边界，非语义正确率/EM）；原生探针确认Enrique出生及Vassar全名排名漏查，另4处跨主体借年份。索引未过关，不接真实Compile/Answer、不推广；120测试，20HTTP/17,312tokens/0未知。[报告](../../artifacts/results/multihop/passage-index-v1/semantics-unified20-v1-review/REPORT.txt)。

- 2026-11-03：同20段修主体/时间/漏提，三轮单次提取＋1无输出HTTP400技术重试；最终19/20新索引合规。修复who声明实体未登记native entities倒排，冻结124条联查0→124且语义字段/原文不变；13属性控制13→13，Enrique/Kim修回，4借年份消失及正确对象保留，9时间正负例全通过。仍有Vassar拒收、版本错绑、事件标签推断和嵌套关系漏提，索引未过关不接Compile/Answer。125测试，61HTTP/174,440已知tokens/1未知。[报告](../../artifacts/results/multihop/passage-index-v1/subject-binding20-v1-review/REPORT.txt)。

- 2026-11-03：同20段查Vassar/版本/嵌套/event。修源地址与身份混淆、明确参与实体倒排、统一源引用及既有名字位置；当前20/20合规，13属性控制13→13、既有名称91→104/104，Vassar/Yale恢复。嵌套仅1/3，版本仍错绑、剧情借电影2007新错，未过门槛不接后续。4组Luna none＋同输入mini诊断，共100HTTP/357,680tokens/0未知；132测试。已询问是否允许冻结同20做Luna low诊断，尚未执行。[报告](../../artifacts/results/multihop/passage-index-v1/grounded-relations20-v1-review/REPORT.txt)。

- 2026-11-03：获准仅同20冻结输入做一次Luna low诊断，未改代码/提示词/结构，单轮无重试。20/20合规，13控制及104名称均保留；嵌套1→2/3，球队主体和剧情借年份改善，但版本2016仍绑父电影、系列隶属漏提；Vassar创办event漏标回退、短引用及片段who仍有问题。索引不通过、不推广/不接Compile或Answer。20HTTP/74,845tokens（reasoning3,888）/0失败未知；none70,795。真实请求除effort外low还省略temperature。原始输出→原生记录全部一致。[诊断](../../artifacts/results/multihop/passage-index-v1/grounded-relations20-luna-low-review/REPORT.txt)。

- 2026-11-03：同20段独立验证who单引用(A)与单断言列表(B)，Luna none单轮无重试。A20/20合规，B19/20；原13属性均13→11，A长短名断连、B生卒错标identity。Vassar两组均同条保留founding/1861/创办者；版本A旧名Cinema未连Final Cut，B仍绑父电影。B一段反向源引用拒收，原文40/40不变；303入库断言逐项原始→原生一致。未过门槛、不推广/接Compile/Answer/扩量；40HTTP/130,545tokens/0未知。历史none对照，未额外重跑。[报告](../../artifacts/results/multihop/passage-index-v1/minimal-structure20-review/REPORT.txt)。

- 2026-11-03：三例回退离线全请求/原始响应审计。Lolo日期对但identity+只引姓名；断言组连带删event定义birth/death（正文规则仍在），不能归因纯合并列表。Linda4个反向mention均She位置→France末尾、entity正确；regex合法而区间非法，整段拒收放大损失。版本已输出Cinema→Final Cut改名关系但未登记身份别名，区别于原父电影错绑。三例正确表示均能通过现有结构，0API/0提示词或预测改动。[证据](../../artifacts/results/multihop/passage-index-v1/minimal-structure20-review/root-cause-audit/REPORT.txt)。

- 2026-11-03：三独立补丁，Luna none同20。恢复birth/death：Lolo两项修回，但Archduke两项退、Enrique及剧情时间等退，13控制11→11；别名边界：目标版本仍未连通，13控制11→11、1段未登记参与实体拒收。范围语法147,131离线组合通过，首请求HTTP400即停，余19未跑；未猜测具体端点限制。两完整组均19/20，原文全保留、257断言原始→native一致；无拼接/推广/Compile/Answer。41HTTP/129,154已知tokens/1未知/0重试。[报告](../../artifacts/results/multihop/passage-index-v1/contract-patch20-review/REPORT.txt)。

- 2026-11-03：只读深查同20库。Archduke长名生卒仍对，原始aliases漏标题短名；输入身份提示因更早空格校验失败一直为空。版本发行who=Hindi version未登记、其他实体空，发行倒排空；改名关系/原文可查，不能等同QA必错。原样重放1个失败请求取得400正文：$ref旁description被端点禁止（5处），是schema构造兼容bug；旧provider未记error正文。0评测重跑/0代码及预测修改；1诊断HTTP400/1未知usage。[定位](../../artifacts/results/multihop/passage-index-v1/contract-patch20-review/deep-audit/REPORT.txt)。

- 2026-11-06：核验来源分组已测未重复API；修$ref+description兼容与HTTP错误正文日志，保留描述/约束，20份schema展开等价。完成此前中止的同20源单轮Luna none，20/20合规、原文全保留、131断言原生一致；历史属性11/13→13/13、版本主体修回，但Enrique出生索引漏取、嵌套仍1/3，语义未过不接Compile/Answer/扩量。136测试；20HTTP/63,351tokens/0未知或重试。[记录](../../artifacts/results/multihop/transport-schema-fix-20261106-v1/REPORT.txt)。

- 2026-11-06：同20源5版独立单轮Luna none验证嵌套/生卒，未串联修补/拼最佳。年精度出生/死亡均恢复；完整v4保留13/13及原正负控制，嵌套1→2/3，仍漏中间链接；其他版有主体、别名或类型回退。源文全保留，1项虚构原生表示测试通过，主线不切换/不扩量。100HTTP/324,937tokens/0未知或重试；同20 low诊断已冻结但未发起，等待用户确认原none偏好是否可变。[记录](../../artifacts/results/multihop/assertion-coverage-20261106-v1/REPORT.txt)。

- 2026-11-06：获准同20冻结输入单轮Luna low诊断，提示词/schema/原文不变（low依原provider省略temperature）。相对none：合规20→19/20、原控制13→11/13、嵌套2→2/3；生卒年精度保留，1段未登记Tuscany导致元数据整段拒收，短摘录仍在。无收益，不采用/扩量/接Compile或Answer。20HTTP/71,741tokens（含reasoning5,720；比对照+9.36%）/0未知重试；本任务累计120HTTP/396,678tokens。[诊断](../../artifacts/results/multihop/assertion-coverage-20261106-v1/low-diagnostic/REPORT.txt)。

- 2026-11-06：沿用none，同20源6版单轮验证嵌套/引用，无输出修补/重试/拼最佳。完整句/谓词说明/字段后置未过；直接原文引用使裸名降0，但仍有原文拼接、别名/实体拒收及主体错绑，去地址编码后最终17/20合规、原控制13→10/13、嵌套仍2/3。均不采用，原none及底库不变，不扩量/接QA。120HTTP/387,883tokens/0reasoning未知重试；4项引用契约测试+4负例子测试通过。[记录](../../artifacts/results/multihop/nested-evidence-none-20261106-v1/REPORT.txt)。

- 2026-11-06：暂停提示词迭代，只读核验Legend原文/完整实际请求/schema/原始输出。两实体已识别，13条fact全部原生保真、正常stop/959输出tokens；小说—系列边在6版及旧none原始输出均缺失，信息仅留在外层引用。既有schema/validator/decoder能表示此边，不能证明none或某句提示词为唯一根因；2/3仅三项定点源探针。0新API/0提示词或预测改动。[完整证据与定位](../../artifacts/results/multihop/nested-evidence-none-20261106-v1/case-inspection/FULL_EVIDENCE.txt)。

- 2026-11-06：同一Legend原文3次none单轮最小对照，预冻结且互不反馈。A原样提取仍漏小说—系列；B无候选局部释义明确is part of；C同schema仅加名词短语范围仍漏。支持局部理解能力存在而结构化枚举/绑定未稳定输出；C范围有语法歧义，不能锁定具体字段或倒推历史内部状态。0生产改动/不扩量重建；3HTTP/5,993tokens/0reasoning未知重试。[记录及完整请求响应](../../artifacts/results/multihop/of-minimal-contrast-20261106-v1/REPORT.txt)。

- 2026-11-09：同一 Legend 原文两次 none 单轮对照。A 原样提取 10 条仍漏小说—系列。D 同 schema 仅追加语法 of 范围说明，两端实体仍在但 facts=[]。结合此前释义正确，聚焦范围仍未恢复结构化边。D 明示可返回空列表，具体原因未隔离。2 HTTP / 4,848 tokens / 0 重试。[记录](../../artifacts/results/multihop/of-attachment-only-20261109-v1/REPORT.txt)。

- 2026-11-09：2Wiki 整段库固定历史首轮，五漏证据＋两正确对照。末跳复核三版全支持交付 2→1→5/5，第二版有 3 次非法 pipe，最终明确原生语法及标题 units 直交原文。8/8 末跳原文进入冻结模板的 Answer 输入，2 对照逐字保留；units 计数修正离线重放一致。21 Compile/202,378 tokens，0提取/Answer，137测试。独立固定计划的同源折叠恢复12/14，40对照无支持丢失，输入估算3241→5888 tokens。[核对](../../artifacts/results/multihop/passage-source-persistence-v1/error-audit/focused/terminal-subject-review-v3/REPORT.txt)。
- 2026-11-09 2Wiki 整段库末跳复核全量 1,000 题。官方 v1.1 EM/F1 62.50/68.36 → 65.10/71.05，原始答案 57.60/64.65 → 59.80/67.15。完整支持交付 773→920，EM 错题中检索漏证据 132→51、交付漏证据 14→5。第二轮 994 次 Compile，808 次实际检索，539 题新增支持交付。无新提取、无去重实验合并。2,988 HTTP/19,282,032 tokens/0 未知。隔离守卫误拦最后校验，原预测离线验证冻结，无重跑。见 `artifacts/results/multihop/passage-terminal-subject-review-v1-n1000/REPORT.txt`。
- 2026-11-09 整段库 Compile 失败修复。首轮 6 例为两次 queries 超量、各一次引用漏投影、排序格式、阶段名及坏 JSON，二轮 5 例均为冻结运行时的阶段名规则。末跳复核默认接现有严格 JSON Schema，原生校验与轮数不变。11 历史失败加 8 控制，19/19 合规、17 个计划原生执行、2 个 stop。写预测时计算冻结 SHA-256，实际守卫下 1,000 条离线模拟冻结成功。139 测试；19 Compile/169,867 tokens/0 未知、0 Answer 或提取。见 `artifacts/results/multihop/passage-compile-schema-repair-v1/REPORT.txt`。
- 2026-11-09 末跳复核全量错题离线复盘。官方 EM 未命中 349 题，287 支持原文已齐、51 检索漏、5 交付漏、6 首轮失败。23 道比较题内部候选已命中但最终输出 Yes/No，基线有 20 道同类。67 道回退中 53 道支持原文齐，17 道原文及顺序完全相同但计划和 Answer 请求不同。固定种子抽查 48 道全支持错题，14 粒度、16 标注/证据边界、9 关系/主体/属性、7 比较/选项映射、2 拒答或输出不完整。51 检索漏中 17 缺 raw-only 文档，离线复现两道标题括号限定导致漏取。0 新模型调用，见 `artifacts/results/multihop/passage-terminal-subject-review-v1-n1000/ERROR_ANALYSIS_OFFLINE.json`。
- 2026-11-09 冻结 Answer 选项输出对照。23 道已知选对但输出 Yes/No 的题，原提示词重跑 9/23，通用单行规则 20/23，内部候选对却输出 Yes/No 12→0；40 道原正确对照 40→39，#171 将 Yes 写为 same。固定 Compile/原文/schema，0 后处理。候选保留，未替换默认。126 次 4o-mini Answer / 500,971 tokens / 0 未知。见 `artifacts/results/multihop/passage-answer-choice-output-v1/REPORT.txt`。
- 2026-11-09 选项输出规则全量冻结 Answer。2Wiki 994 新 Answer、6 原 Compile 失败保留零分。官方 EM/F1 65.10/71.05→65.60/71.95，47 增 42 退；目标 23 修回 19，同类冲突 23→0。非二值比较 298→319、真伪 83→63、开放 270→274。真伪退步 20 中 19 为非二值输出。4,113,939 tokens、0 新失败或未知、0 Compile/提取。未替换默认。见 `artifacts/results/multihop/passage-answer-choice-output-v1-n1000/REPORT.txt`。
- 2026-11-09 补齐全量 Answer 对照中 6 道旧 Compile 失败。复用六个修好的首轮计划、运行现有第二轮和候选 Answer，6/6 结束、5/6 官方 EM。合并原 994 条后官方 66.10/72.45，原始答案 60.70/68.21。#126 支持齐但用影片年份代替导演生年。新增 6 Compile + 6 Answer / 104,790 tokens / 0 未知。见 `artifacts/results/multihop/passage-answer-choice-output-v1-n1000-fill-six/REPORT.txt`。
- 2026-11-09 2Wiki 真伪输出修复全量。固定 1,000 份上一版实际 Answer 请求（含补跑 6 道）的计划、证据和 schema。官方 EM/F1 66.10/72.45→67.40/73.68，40 增 27 退。真伪 63→81/110、非二值输出 23→0；非二值比较 322→323/369，开放 276→270/521。原 23 道修回 20；6 补跑题均对。1,000 Answer/4,151,168 tokens/0 失败或未知，0 Compile/提取/后处理。见 `artifacts/results/multihop/passage-answer-choice-output-v2-n1000/REPORT.txt`。
- 2026-11-09 开放题终值单句小试。30 道全支持错题、30 道正确控制，固定计划/证据/schema 双臂 4o-mini。EM 命中 29→29，2 增 2 退，F1 64.65→63.78，拒答 4→6。两道新增命中为属性粒度，两道回退为亲属链。未接入或扩量，479 道其他题 0 调用。120 Answer/507,043 tokens/0 失败或未知。见 `artifacts/results/multihop/passage-answer-open-terminal-v1-pilot60/REPORT.txt`。
- 2Wiki 开放题答案粒度配对。随机 120 题小试候选 F1 +1.97 pp（对新鲜原版），补跑剩余 401。521 开放题 F1 历史 63.87、新鲜原版 62.97、候选 64.13。冻结其余 479 条的离线总分 67.40/73.68→67.30/73.81，历史 F1 +0.13、新鲜对照 +0.60，未达 +1，不接入。1,042 Answer/4,356,837 tokens/0 失败未知。见 `artifacts/results/multihop/passage-answer-open-specificity-v1-extension401/REPORT.txt`。
- 最新全量 1000 题明确错题离线审计。326 EM 失分中 272 支持齐、49 检索漏、5 交付漏。原文独立时间核算 109 题，95 与正确原答一致，14 原答错且原文核算均命中（诊断潜力 +1.40 F1，0 正确题误报）。13 字符串 null 和 1 选择题 Yes 单列。0 API、未替换预测或修改流程。见 `artifacts/results/multihop/passage-answer-choice-output-v2-n1000/obvious-errors-audit/REPORT.txt`。
- 2Wiki 日期比较 14 错+95 正配对。目标历史 0、原版重跑 5、日期规则 10/14，四道寿命题全修回。控制历史 95、原版重跑 92、候选 90/95；相对历史退 5，相对新鲜对照 4 退 2 增。109 题 95→97→100，候选有回退，未替换或扩量。218 Answer/882,434 tokens/0 新失败未知。见 `artifacts/results/multihop/passage-answer-date-comparison-v1-pilot109/REPORT.txt`。
- 2Wiki 日期规则全量 1000 固定 Answer。官方 EM/F1 67.40/73.68→67.40/73.60，40 增 40 退。目标 14 修回 11，原正确日期 95→88，其余 891 答对 579→575。原始口径 61.70/69.46。1,000 Answer/4,192,360 tokens/0 失败未知。未替换现版。见 `artifacts/results/multihop/passage-answer-date-comparison-v1-n1000/REPORT.txt`。
- 2Wiki 日期比较改为 Answer 前从已交付原文核算，模板、计划、证据和 schema 固定。14 道原错题修回 14，95 正确对照保留 94（第 122 题模型抄对日期后写反顺序），同输入新跑原版分别 4/14、92/95。离线从题面及证据复现 109 触发，合并 891 历史预测后开发集官方 EM/F1 67.40/73.68 → 68.70/74.98。218 Answer/895,724 tokens/0 失败未知。无答案后处理，候选未接默认，未验证跨数据集泛化。见 `artifacts/results/multihop/passage-answer-date-evidence-v1-pilot109/REPORT.txt`。
- 2026-11-09 冻结 2Wiki 整段库开发集 1,000 题，官方 v1.1 别名 EM/F1 为 68.70/74.98（109 道重跑、891 道复用）。保留第 122 题回退，登记 28 个文件的 SHA-256，暂停 2Wiki 实验。见 [冻结清单](2WIKI_FREEZE.json)。
- 2026-11-09 MuSiQue 整段库前 20 题。11,656 段原文全保留，9,500 段复用原生索引、2,156 段纯原文，0 新提取。EM/F1 40.00/40.65，20 题均实际执行第二轮检索。12 道 EM 错题中 8 道检索缺支持（13 个缺失实例均有元数据）、0 道证据交付漏、4 道支持段落齐。60 HTTP/493,429 tokens/0 执行失败或未知。见 `artifacts/results/multihop/musique-shared-passage-v1-n20/REPORT.txt`。
- 2026-11-09 MuSiQue 全量整段索引和 1,000 题。沿用 2Wiki 的 Luna none 元数据提示与 schema，从 11,656 段原文各调用一次，旧 facts 标签复用 0；10,151 段接纳元数据，1,505 段纯原文（891 校验、614 全局别名冲突）。Luna medium 两轮上限与 4o-mini 单次 Answer，EM/F1 31.20/41.50。688 道 EM 错题，423 检索漏支持、57 有支持但未完整交付、205 支持齐全、3 Compile 失败。957 题执行第二轮检索。14,650 HTTP / 35,218,128 已知 tokens / 1 未知用量。2Wiki 冻结已核验。见 `artifacts/results/multihop/musique-shared-passage-metadata-v1-n1000/REPORT.txt`。
- MuSiQue 修复放入 `benchmarks/multihop/musique/`。可传输的互斥根/子主题 schema 单轮小试，旧根主题失败 7/8 恢复接纳、8/8 对照保留，剩余一段别名类型不一致。两阶段别名接纳离线恢复 614 段，保留原 10,151 段索引和 4,541 个别名绑定。固定整段库和 Answer、当前共享 QA 运行时首轮结构化，小试 3/3 Compile 失败恢复执行、2/3 EM，3/3 正常对照仍执行。50 HTTP / 199,110 已知 tokens / 16 schema 400 无用量回执。2Wiki 冻结校验通过，1,000 题未重跑。见 `artifacts/results/multihop/musique-structure-repair-v1/REPORT.txt`。
- MuSiQue 修复后全量应用户要求暂停在建库 5,368/11,656，270 单段校验失败，24 个已发请求暂无落盘响应，QA 尚未开始。固定 33 段覆盖 7 个失败原因，实体、别名和地点的单表候选通过 28/33，剩 2 地点超上限、2 名称未逐字出现在原文、1 跨组同名。独立对照封存未运行，全量不放行。68 HTTP / 88,005 tokens / 0 未知（仅两版新诊断）。见 `artifacts/results/multihop/musique-index-contract-gate-v4/REPORT.txt`。
- MuSiQue 将无原文依据的名称、歧义别名和超限地点 facet 按字段拒收，原始模型响应和最终元数据分开记录。离线恢复原失败 5 段，原通过 28 段元数据逐项一致；新跑 33/33 历史失败样本和 72/72 独立对照均入库。全量 11,656 段从原文新建，0 段纯原文，旧标签复用 0。1,000 题 EM/F1 31.20/41.50→34.90/44.97，110 增 73 退，0 执行失败。完整支持交付 424→522；651 道 EM 错题中 313 漏检、89 漏交付、249 支持齐仍失分；四跳 EM 15.66→13.86。全量 14,656 HTTP / 43,802,775 tokens / 0 未知。2Wiki 冻结校验通过。见 `artifacts/results/multihop/musique-shared-passage-repaired-v3-n1000/REPORT.txt`。
- MuSiQue 三轮。空答案 schema 小试 32 原异常 2→5 EM、20 对照全保留。末跳保留在固定 token cap 补齐 5/89、89 对照证据无变化，6 变化题 2→4 EM。第二轮桥接指令固定首轮双臂 48 题 EM/F1 27.08/36.88→35.42/42.08。第三轮组合全量 34.90/44.97→35.50/45.41，未达 45.00/57.20，完整支持 522→536，空/占位 32→0，三跳 EM 下滑、四跳提升。2,308 HTTP / 25,251,478 tokens / 0 未知，0 新索引，2Wiki 冻结复核通过。见 `artifacts/results/multihop/musique-three-round-v1-n1000/ITERATIONS.txt`。
- MuSiQue 三跳回退与 Answer 对照。28 道回退中 14 道丢失旧版已交付的支持段，仅 3 道可从首轮证据补回全部丢失段，固定预算保留未新增完整证据题。36 题给第二轮 Compile 加分阶段原文片段，完整支持 15→15，EM/F1 38.89/47.78→30.56/47.31。开放问句 schema 对照，17 异常题 2→5 EM，17 正确对照均 16/17（一进一退）。Answer 证据排序对照，24 道支持齐全的历史错题 0→3，16 道对照 14→13。均未接默认，未扩量或重建。评分口径维持原样，10 道数字写法失分另计。292 HTTP / 2,410,253 tokens / 0 未知，2Wiki 冻结复核通过。详见 `musique-bridge-retention-v1`、`musique-stage-provenance-v1`、`musique-open-answer-contract-v1`、`musique-answer-order-v1` 四组结果目录。
- MuSiQue 全量 645 道 EM 错题逐题分类。修复同一原文的索引行和来源行重复占用交付名额。固定计划和检索池离线重放 1,000 题，72 道交付漏证据中 35 道补齐、3 道部分补回。完整支持 536→577，已有交付段落全部保留；本地 Answer 提示词计数平均 4,171→6,229 tokens。321 道候选池缺支持中，29 道有缺失段已在原生子阶段命中。0 新 API、无新 EM/F1，2Wiki 冻结复核通过。146 测试通过。见 `artifacts/results/multihop/musique-error-bug-audit-v1/ERROR_TAXONOMY_OFFLINE.json`。
- MuSiQue 后续 bug 定位。29 道子阶段命中但检索池丢失的错题，冻结计划按得分顺序做最小对照，5 道至少恢复一个缺失段，2 道补齐检索池；尚未改合并实现。1,000 份第二轮 Compile 输入均重复序列化整段正文并携带占位日期，重复正文平均约 3,483 本地 tokens；134 份至少有一个 unit 只显示标题。第 128 题实际将 `$s3.date` 展开为占位值 `2000-01-01`。0 新模型调用。见 `artifacts/results/multihop/musique-error-bug-audit-v1/` 中的 `STAGE_LOSS_OFFLINE.json` 和 `FEEDBACK_AUDIT_OFFLINE.json`。
- MuSiQue 优先修复 Answer 与证据交付。252 道全证据失分中有 22 道拒答、28 道的观察字段与参考值精确一致（信号可重叠）。已接入经过 34 题配对小试的开放问句 schema，异常组答对 2→5，正确对照两组均为 16/17。去重后仍有 37 道交付不全，缺段均排在不同段落的前 40 名之外。修复 Compile 正文重复、unit 只传标题、占位日期绑定和阶段合并顺序。冻结计划回放 1,000 题，第二轮 Compile 文本输入平均 13,280→8,648 tokens。相对同一去重交付入口，完整支持池 623→624（3 增 2 减），完整交付 574→575（2 增 1 减），原答对题无完整支持回退。149 项测试通过，0 新 API。见 `artifacts/results/multihop/musique-error-bug-audit-v1/BUGFIX_REPLAY_OFFLINE.json`。
- MuSiQue 修复版端到端重跑同一批 1,000 题。冻结原文索引，首轮和反馈轮均重新 Compile。EM/F1 35.50/45.41→37.70/48.57，89 题由错变对、67 题回退。完整证据 536→562。EM 错题中检索池缺支持 321→308，查到但未交齐 72→41，证据齐仍失分 252→274。开放问句裸 Yes/No 17→0，旧异常组 4 题答对。反馈 Compile 的实测输入平均 15,410→10,644 tokens，827 题实际执行第二轮检索，3 道反馈计划失败后用首轮证据完成 Answer。2,000 Luna Compile、1,000 4o-mini Answer、27,727,097 tokens、0 未知用量、0 题执行失败。2Wiki 冻结校验通过。见 `artifacts/results/multihop/musique-bugfix-n1000/AUDIT_OFFLINE.json`。
- MuSiQue Answer 目标字段配对试验。60 道固定计划、原文和顺序，两臂各一次 4o-mini，只有开放题候选改为 target、observations、missing_link、evidence、answer。35 道历史错题同输入原版→候选 EM 命中 8→11，F1 37.48→49.85，末跳子组 5→3。18 道原本正确对照 17→14；53 道合计 EM 25→25，F1 56.82→62.65。6 道证据不足对照拒答 3→1，#32 混用续集，#370 把配偶写成母亲。候选四次拒答中三次 missing_link 为 null。真伪对照两臂请求逐字一致。120 HTTP / 940,490 tokens / 0 执行失败或未知用量。候选留在实验目录，2Wiki 冻结校验通过。见 `artifacts/results/multihop/musique-answer-target-structure-v1/SCORED_OFFLINE.json`。
- MuSiQue 全群 Answer 证据使用复盘。232 道候选逐题纳入诊断，另列 42 道已确认等义写法。139 道 Answer 错题对照全部 288 道全证据正确题。标注段正文含参考值的子集中，至少引用一段含参考值的标注段为 79/138 对 249/283。末跳组 43/60 引用此类段落仍错，绑定组 23/37 未引用此类段落。引用是可观察信号，不能证明内部读取或语义支持。两轮都全证据的 37 道回退题中，完整引用 19→11；25 道修回题完整引用 13→12。下一轮按整组验证证据交付和 Answer 推理两层的假设，单题复现留作回归。0 新 API。见 `artifacts/results/multihop/musique-bugfix-n1000/ANSWER_POPULATION_AUDIT_OFFLINE.json`。
- MuSiQue 全群计划暴露因果对照。全部 139 道 Answer 错题配 139 道按跳数、支持段位置及请求长度匹配的正确对照。固定问题、交付原文、顺序、Answer 指令及 schema，仅删原生计划块。原版→去计划，错题 14→18 EM、F1 27.66→27.40，正确对照 132→109 EM、F1 96.04→82.10；合计 146→127 EM、F1 61.85→54.75。末跳组 6→7。正确对照 26 道回退中，9 道不再引用含参考值的标注段，15 道仍引用。11 道错题新增命中中 7 道开始引用此类段落。556 Answer / 4,343,497 tokens / 0 执行失败或未知，2Wiki 冻结校验通过。保留现有计划输入。见 `artifacts/results/multihop/musique-answer-plan-exposure-v1/SCORED_OFFLINE.json`。
- MuSiQue Answer 阶段命中标记配对。沿用同一 139 错题加 139 正确对照。离线冻结原生执行器回放，验证原版请求逐字一致，仅加 4,241 个已交付段落的命中标记。268 道分阶段，10 道并行结果按组标记。绑定错组 EM 3→6（4 增 1 退），F1 25.91→30.36；末跳组 EM 6→4（0 增 2 退），F1 18.99→15.85。139 正确对照 129→130（7 增 6 退），278 合计 140→142，F1 60.19→60.04。绑定组含参考值段落引用 15→14，末跳两道回退仍引用含参考值的标记段。556 Answer / 4,419,674 tokens / 0 执行失败或未知。候选未接入，2Wiki 冻结核验通过。见 `artifacts/results/multihop/musique-answer-stage-provenance-v1/SCORED_OFFLINE.json`。
- MuSiQue 末跳取值指令配对，原 139 错题和 139 正确对照。保留原计划、原文顺序及 Answer schema，只替换开放题最终取值说明。60 道末跳组 EM 5→7（3 增 1 退），F1 16.86→18.80。54 道三次原版均错中修回 2，分别是合唱对象和演员角色。绑定组 2→1（0 增 1 退），正确对照 128→133（7 增 2 退）。278 合计 EM 138→145，F1 59.55→60.88。556 Answer / 4,396,903 tokens / 0 执行失败未知，2Wiki 冻结通过。候选留在实验目录。见 `artifacts/results/multihop/musique-answer-terminal-value-v1/SCORED_OFFLINE.json`。
- MuSiQue 末跳取值指令扩展至同一开发集 1,000 题。核验并复用 278 对，剩余 722 题两臂重跑。固定原计划、原文、段落顺序及 Answer schema。全量原版重跑 EM/F1 37.40/48.65，候选 38.30/49.28，28 增 19 退，配对精确检验 p=0.243。新增 722 题 EM/F1 32.69/44.46→32.96/44.81，16 增 14 退。全证据 562 题答对 287→295，非全证据 438 题 87→88。1,444 新 Answer / 11,384,789 tokens / 0 执行失败未知，2Wiki 冻结通过。见 `artifacts/results/multihop/musique-answer-terminal-value-v1-n1000/SCORED_OFFLINE.json`。
- MuSiQue 末跳取值全量候选剩余 617 道 EM 错题。证据齐全 267、交付漏段 38、最终检索池漏段 312。证据齐全的错题有 255 道两臂都错，144 道历史运行及两臂的归一化错答一致。按此前诊断仍有 53 道末跳、37 道绑定、30 道属性取值问题。350 道缺证据离线复放，38 道交付漏段的缺段全部排在前 40 个不同段落之外。312 道最终漏段中，19 道缺段曾出现在最终原生子阶段，36 道曾在首轮池中，25 道首轮池包含其全部最终缺段。0 新 API。见 `artifacts/results/multihop/musique-answer-terminal-value-v1-n1000/REMAINING_ERRORS_OFFLINE.json`。
- MuSiQue 255 道证据齐全且两臂均失分的错题复核。沿用此前归类，末跳 52、绑定 36、属性值 29。末跳组 33 道两臂均引用含参考值的标注段，新版仅 9 道在观察中完整写出参考值。绑定组 36 道中 22 道两臂均未引用含参考值的标注段，属性组 15 道两臂均引用仍错。确认重复缺陷包括观察漏写所问关系、候选实体和时间串线、原文日期与不确定限定丢失。0 新 API。见 `artifacts/results/multihop/musique-answer-terminal-value-v1-n1000/PERSISTENT_FULL_SUPPORT_255_OFFLINE.json`。
- MuSiQue 末跳关系观察配对。保留末跳取值候选，在原观察说明加两句，固定计划、原文、顺序和 schema。52 末跳题 EM 0→3，F1 12.57→14.27；盲序人工观察复核，所问关系有记录 18→28，角色和值匹配且有原文依据 3→6。三个 EM 修回中仅 #279 同时补齐了证据链，另两道最终正确而观察仍有误。36 绑定题 0→1，29 属性题 1→2（2 增 1 退），117 正确对照 106→108（3 增 1 退）。234 合计 107→114，F1 57.36→59.35。468 Answer / 3,751,373 tokens / 0 失败未知。2Wiki 冻结校验通过，候选留在实验目录。见 `artifacts/results/multihop/musique-answer-terminal-observation-v1/SCORED_OFFLINE.json` 和 `OBSERVATION_AUDIT_OFFLINE.json`。
- MuSiQue 末跳关系观察扩展至 1,000 道。复用 234 对，766 道两臂重跑。固定计划、原文、顺序和 schema，两臂均保留末跳取值指令。原版重跑 EM/F1 36.70/48.55，候选 37.90/49.22，38 增 26 退，配对精确检验 p=0.169；新增 766 道 33.94/45.85→34.60/46.13，29 增 24 退。净增 12 道全来自两跳，三跳和四跳 EM 持平。上一版已保存分数 38.30/49.28。新增 1,535 次调用，12,164,563 已知 tokens，2 次读取超时用量未知，1 次输出截断。三个原请求各补跑一次成功，均未命中参考答案。2Wiki 冻结通过。候选留在实验目录。见 `artifacts/results/multihop/musique-answer-terminal-observation-v1-n1000/SCORED_OFFLINE.json`。
- MuSiQue 末跳关系观察版全量离线审计。621 道 EM 失分逐题记录，270 道标注支持段全交付，40 道只在交付阶段缺段，311 道最终检索池缺段。351 道缺段题复用 342 条同计划、同缺段回放并新增 9 次离线回放。36 道首轮池曾有至少一个最终缺段（25 道有全部最终池缺段），20 道最终原生子阶段曾命中，去重共 52 道；40 道只交付缺段的缺段全部排在前 40 个不同段落之外。12 道开放题输出 Yes 或 Yes. 均符合原 schema（9 道未启用取值约束、3 道被标点绕过）。270 道全证据失分中，沿用既有诊断有末跳 49、绑定 36、属性 27、等义/合理写法候选 55、题面或标注疑点 66；人工抽核复现了关系限定遗漏、作者/演唱者混淆、选中实体却输出比较数值。三跳及四跳错题缺段分别为 142/214、113/134。0 API，未更改推理、原库或分数。见 `artifacts/results/multihop/musique-answer-terminal-observation-v1-n1000/REMAINING_ERRORS_OFFLINE.json`。
- MuSiQue 首轮证据保留离线对照，1,000 题固定计划、整段库及 40 段预算，原检索行和交付顺序全部逐题复现。827 道最终计划发生变化，实验仅将首轮原生符号命中作为现有 RRF 的第三路输入，词法一路和交付逻辑不改。候选池全证据 618→646，36 道首轮命中后丢段题全部找回至少一段入池（25 道池内缺段全找回），9 道交付至少一段，仅 4 道补齐全链。实际全交付 562→538（13 增 37 退），379 道原正确中 17 道丢支持段，12 道由齐变缺，其中 7 道丢了正确 Answer 实际引用的段。755 道交付顺序或内容变化。候选不接入。0 API、无新 EM/F1，2Wiki 28 项冻结校验通过。见 `artifacts/results/multihop/musique-first-round-retention-offline-v1/COVERAGE_OFFLINE.json`。
- MuSiQue 修复第二轮覆盖首轮候选。两次替换对照均冻结同一 1,000 题及 40 段预算。保护第二轮命中后强制补首轮优先段，全交付 562→559（0 增 3 退）；改为按原生融合排序竞争后 562→549（3 增 16 退），8 道原正确由齐变缺，其中 5 道丢了原 Answer 实际引用的段。两种替换均撤回。实际合入的修复只将首轮独有候选追加至最终候选尾部，供原交付逻辑补空位。全量候选池支持段齐全 618→646，36 道首轮命中后丢段题全部找回至少一段（25 道找回全部池内缺段），1,000 题实际交付文本及顺序与原版完全一致，562 道全交付和 379 道原正确无掉段。150 测试与 74 子测试通过，2Wiki 28 项冻结校验通过，0 API、无新 EM/F1。见 `artifacts/results/multihop/musique-first-round-fallback-offline-v1/COVERAGE_OFFLINE.json`。
- MuSiQue 原生子阶段命中丢段定位及修复。20 道全部在 80 行阶段合并上限处丢失独立阶段的命中，14 道为 unit、6 道为 fact。5 道缺段已排在所在阶段前两位，均为 unit，被排除在现有保留名额之外。仅 MuSiQue 启用 unit 使用原生每阶段两个保留名额。固定同 1,000 题两轮计划和 40 段预算离线回放，5 道目标全部交付找回的段，3 道补齐全链；全量候选池全覆盖 646→649，实际全交付 562→569（7 增 0 退），379 道原正确无支持段丢失。1 道原本缺段的错题额外丢 1 支持段，251 道交付集合或顺序变化。151 测试与 74 子测试通过，2Wiki 28 项冻结核验通过。0 API、无新 EM/F1。见 `artifacts/results/multihop/musique-stage-merge-offline-v1/COVERAGE_OFFLINE.json`。
- MuSiQue unit 保留修复的 Answer 配对小试，7 道新增齐证据（6 历史错＋1 历史对）和 21 道证据有变化的原正确（2/3/4 跳各 7，固定哈希选取）。两臂使用同一冻结 Compile、Answer 指令、40 段和 gpt-4o-mini 温度 0，仅换证据与引用枚举。7 道组 EM 1→1（1 增 1 退），F1 30.95→25.71；对照 EM 17→17（2 增 2 退），F1 84.13→86.90。合计 EM 18/28→18/28，F1 70.83→71.61。#166 答案字串只在新加入段内，但模型引用错段；#879 旧答混绑两个 Springfield，候选拒答；#92/#743 段落集合完全相同、仅顺序不同，模型选择了同段中的 Lexington。13 道换段 5→6，15 道仅顺序变化 13→12。56 次 Answer、461,984 tokens、0 失败或未知用量；2Wiki 冻结通过，未扩量。见 `artifacts/results/multihop/musique-stage-merge-answer-pilot-v1/SCORED_OFFLINE.json`。
- MuSiQue 同一 28 题稳定顺序配对。固定 Compile、Answer 指令、40 段和 gpt-4o-mini 温度 0。13 道仅在原移除位置补新段，15 道两臂输入逐字节相同。7 道补齐组 1→2（1 增 0 退），#166 保住且正确引用新增传记；#879 本轮两臂均对。21 道对照 17→15（0 增 2 退），#81 换段后退，#118 相同输入仍退。#92、#743 本轮两臂均错，未复现先对后错。13 道实际换段 EM 6→6、F1 51.28→51.28，15 道相同输入 EM 12→11。合计 EM 18→17，F1 70.49→69.05。56 Answer、461,490 tokens、0 失败未知，2Wiki 冻结通过，未扩量或接入稳定顺序方案。见 `artifacts/results/multihop/musique-stage-merge-stable-order-answer-pilot-v1/SCORED_OFFLINE.json`。
- MuSiQue 跨文档主体全群诊断与第二轮 Compile 配对。逐题回放 283 道候选池缺段题，154 道无可观察范围或命中、123 道至少一段实际命中后被阶段预算、pipe 或合并丢失、6 道有范围或直接实体 posting 线索但仍未命中。按跳数和诊断固定 36 缺段＋36 原答对，冻结整段索引、首轮计划与证据、原生 schema 和 40 段预算，只替换现有第二轮主体查找段。缺段组完整交付 2→8（6 增 0 退，原缺段实际交付 6→14），无范围组 0→2，命中后丢组 1→5，过滤未命中 1→1；保守标题/实体主体命中信号 0→1。正确对照完整交付 31→28（1 增 4 退），其中 3 退已在新候选池缺段，1 退是入池未交付。未过预先门槛，不跑 Answer、不接入默认。144 Luna medium、1,802,264 tokens、0 失败未知，2Wiki 冻结通过。见 `artifacts/results/multihop/musique-compile-grounded-subject-pilot-v1/DIAGNOSIS_OFFLINE.json` 及 `SCORED_OFFLINE.json`。
- MuSiQue 123 道命中后丢段和 379 道原正确全量离线回放，原候选与交付逐题吻合。按缺段计，126 段在单阶段预算丢失（125 段每次命中都在第 80 名之后，最佳排名中位 318.5，116 段是 facts），20 段在 80 行阶段合并丢失，11 段在 head(2) 丢失。题级分别 96/20/11，有重叠。第二轮从归档原模型结果保守投影为单条新查询，冻结首轮已有查询。原生计划内追加或替换空跳可执行 47/123，另 73 道满位且无空跳；独立执行单条可覆盖 118/123，保护首轮符号段，只占原 40 段内的空位或词法尾部名额。两版 123 道均无完整交付新增；独立版补齐候选池 4 道。379 对照相对现有两轮原版完整交付 293→232（1 增 62 退）和 293→251（1 增 43 退）。两版均不接入，未新跑 Compile 或 Answer，不记新 EM/F1，2Wiki 28 项冻结通过。见 `artifacts/results/multihop/musique-one-hop-preservation-v1/` 的 `LOSS_BOUNDARIES_OFFLINE.json`、`ONE_HOP_SCORED_OFFLINE.json`、`SEPARATE_SCORED_OFFLINE.json`。
- MuSiQue 低排名查询的单跳主体约束候选。第二轮结构化只给一名可见主体和同一关系的短语，生成现有 DSL 查询，保留首轮其余查询。24 道缺段、24 道原答对，固定首轮、索引、预算和 Answer；96 次真实 Luna medium。实体约束及优先精确标题两版复用同一模型决策，完整交付均为缺段组 3→1（1 增 3 退），对照 15→10（2 增 7 退）。#646 手工离线验证，Nanjing 从原第 307 到精确页面+关系查询第 4，仍未进入 40 段。两版均未过门槛；候选运行时代码已移到实验目录，默认路径未改。1,044,242 tokens、0 失败未知，0 Answer，无新 EM/F1。见 `artifacts/results/multihop/musique-scoped-relation-feedback-v1/SCORED_OFFLINE.json`。
- MuSiQue 固定现有第二轮计划的单查询范围离线对照。123 缺段＋379 原答对逐题复现最新候选池和交付，固定索引、首轮和 80/40 预算；仅在计划已显式给出主体且首轮原文可核验时，允许原位收窄一条事实查询，其他节点原样保留。119 缺段题有有效第二轮，其中 69 有未限定主体的事实查询，最终 2 道缺段和 2 道对照可改。#646 Nanjing 新进阶段前 80、候选池和最终交付，缺段组全交付 0→1；对照 #659 掉一段阶段命中但仍交付，#928 有一段仍在候选池却未交付，对照全交付 293→292。证据门槛未过，未跑模型或 Answer、无新 EM/F1，2Wiki 冻结复核通过。见 `artifacts/results/multihop/musique-inplace-scope-replay-v1/SCORED_OFFLINE.json`。
- MuSiQue 第二轮宽查询逐题复核 69 道：严格漏绑 3、仅关键词 22、相关主体已使用 12、计划漏关系 5、首轮证据不足 27。用同一标准另在正确对照定位 5 道，冻结 8 条单查询离线修订。123 缺段题前 80 支持段 208→209、实际交付 182→183（2 增 1 丢）、全交付 0→0。379 原答对题前 80 支持段 810→810、实际交付 823→822（1 增 2 丢）、全交付 293→292。8 条有 7 条尝试放宽 grep，2 道正确对照的原文含主体但缺对应实体倒排。0 API，证据门槛未过，2Wiki 28 项冻结校验通过。见 `artifacts/results/multihop/musique-second-round-subject-audit-v1/REPORT.md`。
- MuSiQue 22 道主体只写入关键词的题，固定两轮计划离线逐题复现。41 个未交付支持段中，26 个在子阶段前 80 前丢失、5 个在阶段合并丢失、4 个入池未交付、6 个没有观察到原生命中且未入池。就同一主体和对应关系，8 道原文给足关系但第二轮查询不匹配，另 2 道有间接关系线索。16 个缺段在正文明确出现本题主体，1 个无该主体倒排，属于后续赛季属性段。3 道字面查询和倒排都命中但排在前 80 外，其中 2 道命中对应关系，另一道只命中作者名。0 API，2Wiki 28 项冻结通过。见 `artifacts/results/multihop/musique-literal-subject-audit-v1/REVIEW.tsv` 和 `COUNTS_OFFLINE.json`。
