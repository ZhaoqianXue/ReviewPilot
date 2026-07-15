# ReviewPilot 内测质量迭代计划

## Goal

基于 3 个真实使用示例，对 ReviewPilot Web App 进行端到端多轮使用、问题发现、实现优化与回归验证，使其达到可供正式内部测试的质量，并显式记录验证结果与剩余风险。

## Formal-release extension — canceled

用户于 2026-07-14 取消正式发布目标。分支已精确回退到正式发布调整前的内测验收提交 `e5189d5`；正式发布设计、计划和首个实现切片均不再属于当前产品状态。

## Success Criteria

- 3 个经确认的真实示例均可完成核心端到端流程。
- 每轮发现的问题都有复现证据、优先级、修复或明确延期理由。
- 关键错误可理解、可恢复，运行状态不会误导用户。
- 自动化检查与针对性回归通过；未运行或跳过的检查明确列出。
- 形成可执行的内测说明、已知限制和剩余风险清单。

## Phases

| Phase | Status | Exit condition |
|---|---|---|
| 1. 项目勘察与现状基线 | complete | 明确架构、运行方式、现有测试、三个示例候选和当前风险 |
| 2. 需求澄清与方案设计 | complete | 用户批准范围、三个示例、迭代策略和内测门槛 |
| 3. 设计文档与实施计划 | complete | 设计文档经用户审阅，实施计划可逐项执行 |
| 4. 示例 1 使用—修复—回归 | complete | 示例 1 达到约定验收条件 |
| 5. 示例 2 使用—修复—回归 | complete | HCI r3 端到端通过；空 Schema 门控与小样本分类过度细分已修复，701/701 回归通过 |
| 6. 示例 3 使用—修复—回归 | complete | 空间创作 r1 端到端通过；单样本文案已修复，702/702 回归通过 |
| 7. 跨示例回归与内测收口 | complete | 三例联合状态、21 个导出、702 项全量回归与内测交接全部通过；GO 决策已提交 |
| 8. 正式发布现状与范围审计 | canceled | 用户取消正式发布目标；审计不再驱动实施 |
| 9. 正式发布设计与实施计划 | canceled | `daf40ff` 与 `f049e86` 已随分支回退移除 |
| 10. 发布工程与安全门禁 | canceled | 首个元数据实现 `26e36fe` 已随分支回退移除 |
| 11. 三示例发布候选复验 | canceled | 未启动，保持内测候选证据边界 |
| 12. 正式发布审计与交接 | canceled | 未启动，不作正式发布声明 |

## Current Decisions

- 设计获批前只做只读勘察和流程文档，不修改产品实现。
- 以真实端到端使用证据驱动优先级，不做与三个示例无关的重构。
- 保持现有代码风格，所有显著修改都需要针对性验证。
- 视觉伴侣未获确认，后续使用纯文本澄清与设计；如用户之后明确同意，可再启用。
- 用户未反对推荐的示例选择，因此采用三类差异化代表场景：生物医学、HCI/计算机科学、跨学科空间设计；具体输入必须从现有真实项目证据中恢复。
- 依据“真实使用”的明确目标，允许调用现有配置中的真实检索与 LLM 服务；首轮每平台固定 5 篇，稳定后的确认轮最多 10 篇，禁止无界批量调用，显著扩容需重新确认。
- 正式内测门槛：3–5 名内部用户在本机 Chrome 中，无开发者介入完成建项至分类；P0 为 0；失败可理解、可重试；数据不被静默覆盖；常见桌面尺寸可用。公网部署、账号系统和多人协作不在本轮范围。
- 采用“纵向场景闭环 + 跨场景回归”：每个示例独立完成真实使用、缺陷证据、最小修复、自动化回归和再次真实使用，最后三例联合验收。
- 实施方式为 Subagent-Driven：每个任务必须通过独立规格审查和代码质量审查；Critical/Important 未清零不得进入下一任务。
- failed-only retry 的跨进程临界区以稳定的项目目录 inode advisory lock 保护 cooperating processes；外部重命名/替换整个项目根目录属于本机单用户内测威胁模型之外，扩大到多 worker/hostile same-user filesystem 前必须重新设计 ownership 与 root pinning。
- 接受 4A2c1b 的三个非阻塞 Minor：target ledger 时间戳未校验单调顺序、项目根目录被外部替换会分裂锁域、marker link 前硬崩溃可能留下孤立临时文件；三者均不改变当前 authority、abort recovery 或 record-only 控制决策。

## Errors Encountered

| Error | Attempt | Resolution |
|---|---:|---|
| 创建目标失败：线程已有未完成目标 | 1 | 读取现有目标；确认其与本次请求一致，继续使用原目标 |
| `pytest --collect-only -q` 无输出且进程进入不可中断等待 | 1 | A/B 对照定位为 x86_64 `.venv` 在 arm64 主机的 Rosetta/pytest 核心加载阻塞；实施阶段重建原生 arm64 venv，禁止用测试超时掩盖 |
| 最终计划自审误判重复行，补丁上下文不存在 | 1 | 确认为两个重叠 `sed` 区间重复显示边界行；未重试无效修改，文件本身无重复 |
| `git check-ignore .worktrees` 对不存在目录返回 1 | 1 | 改查 `.worktrees/placeholder`，确认 `.gitignore:47` 规则生效后创建工作树 |
| 原生基线首轮 collection 缺少 `config.py` | 1 | 仅链接主工作区 ignored `config.py`；collection 从 207/11 errors 恢复为 286/0 errors |
| 原生全量测试 2 failures | 1 | systematic-debugging 定位 eager OpenAI init 与 ignored scratch 测试；分别 TDD 修复和清理，最终 286 passed |
| 任务 4 初版刷新恢复在导航与聊天交错下可产生陈旧覆盖、双轮询或丢回复 | 4 | 每轮以独立规格/质量审查发现可复现竞态；分离任务与导航所有权，加入可执行 Node 时序测试，最终 296 passed 且双审查批准 |
| ExtractionAgent 原子临时文件仍可能在 writer 异常被业务 try 捕获后提交错误行 | 1 | 将所有 PDF/Web output write 移出领域异常捕获区；一次性 writer 故障双路径回归证明异常逃逸、旧文件字节不变、临时文件清零 |
| 场景协议最初要求目录与持久化配置整文件相等，且 provenance 只引用未跟踪本机文件 | 1 | 改为应用 schema 字段投影；提交去敏快照/manifest，定义确定性运行 ID、冲突中止和敏感信息负向校验 |
| 生物医学 r1 Search Setup 点击 Create project 后表单重置且无请求 | 2 | 真实浏览器前后截图、服务日志和缺失输出确认 P1；根因定位为 root click 在默认 submit 前 paint 替换 DOM，已创建子 TDD 计划并预留全新 r2 |
| 生物医学 r2 对话框 Max/source=5 持久化为每源 10，平台顺序也偏离冻结输入 | 1 | 创建前/后截图、一次 POST 和去敏持久化投影确认；collection 前停止，建立 P1 数值同步 + P2 默认顺序两个独立 TDD 任务，预留 r3 |
| 生物医学 r3 schema UI 要求 Finalize，但助手指向 Categorization 且 next_actions mapping 指向 download | 1 | 配置/collection/screen/download 均通过后在 schema 停止；截图、task result、chat artifact 一致复现，建立确定性 schema 路由 TDD 计划并预留 r4 |
| 生物医学 r4 category suggestions 被助手误报为已完成且 extraction 回复泄露绝对路径 | 1 | 完成至 extraction 后在 Confirm 前停止；去敏 task summary 记录 UI/任务冲突，建立 action-aware routing 与 user-facing path redaction 两个独立 TDD 任务，预留 r5 |
| 路径脱敏正则无法可靠判断带空格/逗号文件名边界，且遗漏 file URI、冒号前缀和重复 POSIX 根路径 | 3 | 放弃自由文本路径边界解析；检测到任一绝对路径标记即丢弃整条模型回复，并按真实 stage contract 从结构化结果生成安全摘要；覆盖 Unix/Windows/UNC/file URI/`//`/`///`/`////`，最终 325 passed 且双审查批准 |
| 生物医学 r5 使用聊天建项导致 API id 与预留 id 不一致 | 1 | 身份协议立即中止，未启动任何工作流任务；保留意外项目作审计，不复用 r5，改用助手头部 setup 表单预留 r6 |
| 生物医学 r6 七个导出产物均存在，但最终 UI 无下载入口 | 1 | 核心流程全部通过后定级 P1；记录完成态截图和去敏任务摘要，建立 allow-list 下载路由 + Export Package UI 的 TDD 计划，修复后使用 r7 |
| Export allow-list 初版仍允许同项目内 symlink 指向非白名单文件 | 1 | 质量审查实际复现 200 + secret；逐组件拒绝 project root、最终文件和中间目录 symlink，保留单用户本机 TOCTOU 取舍，最终 328 passed 且双审查批准 |
| zsh 轮询脚本使用只读变量名 `status` | 1 | 改用 `task_status`/`task_state`，后续轮询完成；非产品故障 |
| Task 3 第三版全量测试通过但规格复审仍发现 2 个 artifact 对账缺口 | 3 | 不以绿色测试代替规格门禁；新增 collection 全 source JSONL 对账和 retrieval 明细列表对账的第四轮 TDD，Task 4 继续冻结 |
| Task 3 多轮审查超过单任务 4,000-token 预算 | 1 | 显式记录执行纪律偏差；完成本修复闭环后把 failed-only retry 作为独立 Task 4 重新切分，不在 Task 3 扩张范围 |
| Task 4A1 初版测试全绿但规格审查发现 4 个事实边界缺口 | 1 | 严格重算 structured outcome、拒绝六层 symlink、继续安全字段回退并补齐负向测试；不以绿色测试代替规格门禁 |
| Task 4A1 `frozen=True` 未冻结嵌套回滚事实 | 1 | 质量审查复现原地污染；递归冻结 JSON 树并提供隔离 mutable-copy API，最终双审查清零 |
| Task 4A1 初始实现/首轮修复超过单任务 4,000-token 预算 | 2 | 如实记录执行纪律偏差；后续聚焦修复控制在 4,000 内，4A2 继续作为独立任务并限制输出规模 |
| Task 4A2a 实现与测试补强超过单任务 4,000-token 预算 | 2 | 显式披露；将剩余 4A2 继续拆为 staging target 与 durable publication 两个独立检查点，禁止混入 Web/UI |
| Task 4A2a 初版绿色测试未锁定 ordered confirmation 与 exact error/alias parity | 1 | 规格审查以可存活 mutation 证明盲区；新增双 ID 反序和 parser/error mutations，最终双门禁通过 |
| Task 4A2b1 初版真实 DownloadAgent 输出被 source mutation 校验拒绝 | 1 | 用真实 `DownloadAgentContract`、仅 patch 底层 downloader 的集成测试复现；显式列出真实派生字段，未知前缀字段仍拒绝 |
| Task 4A2b1 多轮审查发现 staging facts/cleanup/path/schema 边界缺口 | 4 | 每轮按 reviewer 最小探针 RED→GREEN；最终 primary facts 对称 canonical、分类表重建、全边界 path-free 清理，双审查 Critical/Important 清零 |
| Task 4A2b1 实现与三轮修复持续超过单任务 4,000-token 预算 | 4 | 全部 fail loud 记录；停在 clean committed checkpoints，4A2b2 作为独立纯 merge-target 子任务，不混入 publication/Web |
| Task 4A2b2a 绿色纯 merge 未重验 revision/source/schema/provenance/current counts | 3 | 规格审查以 mutation 和生产形状探针逐层击穿；合并入口重验全部当前事实，旧 authoritative legacy counts 仍按批准合同非权威处理 |
| Task 4A2b2a provenance 初版与真实 DownloadAgent optional identity 默认值冲突 | 1 | 测试 fake 改为生产形状；仅允许 source 缺 key 时 report 使用严格空字符串，非空伪造和 present `None` 错配均拒绝 |
| Task 4A2b2a success method 仅做相等比较，非字符串同值可发布 | 1 | 参数化 dict/list/bool/int/null/空/空白攻击，先校验非空文本再校验 row/report aliases 精确一致 |
| Task 4A2b2a 初版与早期审查修复超过单任务 4,000-token 预算 | 3 | 如实记录；后两轮 provenance/method 聚焦修复均控制在预算内，下一步继续拆为独立 filesystem publication-plan 子任务 |
| Task 4A2b2b 仅凭 source path 无法识别 staging 后合法 PDF 内容替换 | 1 | staging success 冻结 size/SHA-256，publication planning 流式重算并精确比较，4A2c copy 前后继续复验 |
| Task 4A2b2b 多态相等与恶意 MappingProxy backing 可绕过 exact binding | 2 | 精确类型边界后进一步单次物化全部 frozen facts并重建 trusted dataclass；后续 merge/equality/FS 决策不再读取原 proxy |
| Task 4A2b2b 相对 project 可被 backing mapping `chdir` 重绑定 | 1 | 不可信物化前拒绝入口 symlink/non-directory并 strict resolve；所有后续权威读取只用同一绝对 project |
| Task 4A2b2b 多个实现/审查子任务超过 4,000-token 预算 | 6 | 全部显式报告并停在 clean commit；最终聚焦与全量由控制端复验。4A2c 再拆 marker/recovery 与 Web lifecycle，禁止合并成单任务 |
| Task 4A2c1a marker 编码、preparation 信任、hardlink 与 exact schema 边界被审查击穿 | 4 | whole-before canonical blob；before只取fresh current；完整preparation单次物化；fixed files nlink=1；version精确int |
| Task 4A2c1a marker 编码失败发生在 active 注册后且清理域外 | 1 | data/deepcopy/encode/atomic write统一放入post-reservation try，任何异常释放active并返回path-free错误 |
| Task 4A2c1a 生产恢复正确但测试未覆盖完整 marker-last 承诺 | 2 | test-only六故障点矩阵、full ledger+unknown fields、owned cleanup、双项目active与同项目重复begin；三类production mutation实证失败敏感性 |
| Task 4A2c1a 多个实现/审查/测试子任务超过 4,000-token 预算 | 7 | 如实记录；每轮停在clean commit并由控制端复验，最终519+392全绿。4A2c1b继续独立切分 |
| Task 4A2c1b target record 的绿色测试被 provenance、exact type、ledger、source commitment、authority generation 与 marker ABA 连续击穿 | 18 | 每轮只修复已证实边界并保留 record-only；最终将 target 绑定到 fresh current facts、完整 source set、canonical delta、exact ledger、PDF/authority identity 与 marker generation |
| Task 4A2c1b inode 校验仍存在 compare→mutation TOCTOU | 3 | begin 使用原子 no-clobber publish；CAS/restore/remove 全部置于项目目录 inode 的跨进程 flock 临界区，新增真实子进程窗口测试 |
| Task 4A2c1b no-clobber publish 崩溃可留下 nlink=2 marker，且首版 lockfile 可被替换、fork 可继承伪 reentrancy | 3 | recovery 仅归一化唯一严格命名且同 inode 的 temp link；reentrancy 绑定 PID；锁载体改为项目目录 inode，真实 crash/fork probes 通过 |
| Task 4A2c1b 实现与独立审查多次超过单任务 4,000-token 预算 | 20+ | 全部显式披露并记录为执行纪律偏差；最终停在 clean `5b4630c`，控制端复验148 retry、584 full、0 skipped；4A2c1c重新独立计量 |
| Task 4A2c1c1 初版 exclusive copy 仅以文件名/hash 推断幂等所有权，abort 可删除 foreign collision | 2 | publication temp 先在 staging fsync，marker CAS ordered ownership receipt（destination/temp inode+hash），再 no-clobber link；abort 只处理 receipt inode，unreceipted candidate 永久视为 foreign |
| Task 4A2c1c1 receipt cleanup 的 forged inode、validate→unlink、quarantine crash/collision 与 qdir removal 状态被连续击穿 | 5 | no-follow fd 全量复验；receipt-owned 0700 qdir/固定槽持久化 identity；rename→validate→delete；严格 fsync 顺序；缺 qdir 只在全部 receipt paths 已清空时视为完成 |
| Task 4A2c1c1 实现与部分审查超过单任务 4,000-token 预算 | 5 | 全部显式披露；每轮独立 commit 与双 reviewer probe，最终 `6e15c48`、92 transaction、600 full、0 skipped、C0/I0/M0；4A2c1c2重新独立计量 |
| Task 4A2c1c2a readiness 初版未枚举 staging exact set | 1 | 规格 probe 证明 foreign sibling 可被接受；新增四层 direct hierarchy、精确 allowed-set、zero-source 与两轮漂移校验，最终 `4e5238f` 双审查 C0/I0/M0 |
| Task 4A2c1c2 marker CAS 初版缺少 power-loss durability 与新 inode 绑定 | 2 | `0c8f898` 加入 temp file fsync、旧 generation 复验、replace、project fsync、回读；`ecfdbaa` 再把回读 identity 绑定到仍打开的已 fsync temp fd，并补齐 fdopen cleanup 与顺序敏感测试 |
| Task 4A2c1c2 marker durability 实现超过单任务 4,000-token 预算 | 1 | 首轮 durable CAS 略超预算并已显式报告；后续 inode/fd 修复控制在预算内，最终 109 transaction、617 full、0 skipped、C0/I0/M0 |
| Task 4A2c1c2b1 apply decoder 若直接复用 abort optional-set 会接受不完整 commit marker | 1 | `c2e25a7` 明确 abort 四个有序状态、apply 唯一完整状态与 full receipt cardinality；rollback/reconcile 在 roll-forward 接入前对 apply 零修改 fail-closed，双审查 C0/I0/M0 |
| Task 4A2c1c2b2a classifier 初版信任 caller 可变 derived marker facts | 2 | reviewer 伪造 target/before 后可把 foreign authority 误判为 target；`4686f7d` 仅用同一 raw/inode generation 的 fresh disk decode 分类，最终 C0/I0/M0 |
| Task 4A2c1c2b2a 初版实现超过单任务 4,000-token 预算 | 1 | 如实记录；I1 聚焦修复在预算内，后续 durable writer 再拆单 authority primitive 与三 authority coordinator |
| Task 4A2c1c2b2b1 late `lstat→replace` hostile injection 与既定边界冲突 | 1 | 规格复核确认所有 cooperating retry writers 受同一 project-directory flock 串行，Web 另受 task mutex；绕锁 same-user writer 属明确非目标。可移植 POSIX inode-CAS 不存在，扩大防御需 marker receipt/backup 新状态机 |
| Task 4A2c1c2b2b1 expected snapshot 完整性缺少 mutation-sensitive test | 1 | test-only `cb6270a` 伪造非当前 authority kind/identity，证明 writer 首写前拒绝且目标 bytes/inode 不变、无 temp |
| Task 4A2c1c2b2b1 实现超过单任务 4,000-token 预算 | 1 | 如实记录；test debt 修复在预算内，三 authority coordinator 保持独立子任务 |
| Task 4A2c1c2b2b2 final double-classify 缺少 mutation-sensitive test | 1 | test-only `8f6cf3a` 在第一次 final snapshot 后换入相同 target 字节的新 inode；第二次 classify 必须因 identity 不同失败并保留 foreign/marker |
| Task 4A2c1c2b2b2 实现超过单任务 4,000-token 预算 | 1 | 如实记录；最终 test debt 在预算内，`92177be`+`8f6cf3a` transaction 134、双审查 C0/I0/M0 |
| Task 4A2c1c2b3a/b3b1 验证实现分别超过单任务 4,000-token 预算 | 2 | 如实记录；分别停在 clean commit `0963b58`、`f22989c`，双审查均 C0/I0/M0，后续实际 cleanup 继续独立 |
| Task 4A2c1c2b3b2 staging absent 重入遗漏 project fsync | 1 | quality probe 证明 rmtree 后首次 fsync失败，第二次会错误直接成功；`228f509` 在 absent 分支执行 committed validation→project fsync→marker generation，最终 C0/I0/M0 |
| Task 4A2c1c2b3b2 实现超过单任务 4,000-token 预算 | 1 | 如实记录；期间一次定向测试方法名写错产生 loader error，修正后 `152/152` transaction 通过，I1修复在预算内 |
| Task 4A2c1c2b3c1 marker-last integration tests 两次定位不敏感 | 2 | 先将 writer `AssertionError` 被 generic wrapper 吞掉，再把 final ABA 误注入 cleanup fsync；`faad993`+`a350961` 用 Mock.assert_not_called 与第2 eligible fsync定位，mutation probes证明删关键防线必红 |
| Task 4A2c1c2b3c1 实现超过单任务 4,000-token 预算 | 1 | 如实记录；test-only完整性修复均在预算内，最终 transaction 160、C0/I0/M0 |
| Task 4A2c1c2c public zero-PDF apply 被手工 marker 测试掩盖 | 1 | real production target.pdfs=[] 不会进入 `_publish_one_pdf`，缺 empty published receipt；`375f51b` 在 publication 边界 durable CAS canonical empty receipt，真实全失败 public apply通过 |
| Task 4A2c1c2 final audit 发现 apply 未绑定 expected transaction id | 1 | `6fe4b04` 将 public apply 精确绑定 caller transaction id，并用 stale A→abort→B ready→A apply 回归证明旧调用不能提交新事务 |
| Task 4A2c1c2 apply marker unlink 后确认失败缺少 public commit verifier | 1 | `6fe4b04` 新增只读 exact authority/PDF verifier；Web 仅在 verifier 证明 exact target 已提交时把 acknowledgement failure 解释为成功 |
| Task 4B Web failed-only retry lifecycle | 1 | `347d059` 接通 revision+ordered-ID 二次确认、专用后台任务、staging/merge/publication/apply、pre-commit rollback 与 post-commit recovery；9 个 Web retry 测试通过 |
| Task 4B recovery frontend 与真实浏览器验收 | 2 | `8e897ec` 提供安全多选恢复面板、任务所有权与 revision conflict 刷新；真实浏览器另发现单复数和失败项误列为 recently retrieved，均以 TDD 修复 |
| HCI r1 descriptive domain keyword 截断 | 1 | r1 在 collection 前停止；`5cd9c53` 调整平衡括号清理顺序并新增可编辑事实一致性回归，697/697 全量通过 |
| HCI r2 真实 failed-only retry 被 agent sidecars 阻断 | 1 | r2 证明 7/8 partial、duplicate/refresh/rollback 正确；`96da8dc` 在 sources commit 前仅验证清理已知 DownloadAgent sidecars，未知项继续 fail closed，699/699 |
| HCI r2 真实重放与修复超过单任务 4,000-token 预算 | 1 | 如实记录执行纪律偏差；修复停在 clean commit，r3 作为独立全新场景运行重新计量 |
| HCI r3 空 Schema 被误报为可定稿 | 1 | `86e43fd` 显式区分 missing/draft/finalized；空态只提供 Generate Schema，侧栏提示与权威状态一致 |
| HCI r3 七篇论文生成七个分类 | 1 | 真实 UI 先通过强制人工确认收敛为四类；`71c0465` 将建议上限改为样本量感知并禁止 paper-specific 类别，701/701 通过 |
| 空间创作 r1 真实工作流与单数文案修复超过单任务 4,000-token 预算 | 1 | 如实记录执行纪律偏差；分别停在 clean `b8ab180` 代码提交和 `b4cedc7` 证据提交，联合发布审计作为独立收口任务重新计量 |
| 正式发布审计命令读取了 ignored 本机配置内容 | 1 | 立即停止复制或引用任何值；后续只检查文件存在性、模板与环境变量合同，禁止诊断命令输出配置/凭据内容；把安全配置迁移列为发布门禁 |
| 正式发布产品形态未获设计批准 | 3 | 已完成只读差距审计、比较本机单用户/团队自托管/公共 SaaS 三种方案，并两次提交推荐的本机单用户 v1.0 边界；brainstorming 强制门禁禁止在批准前实施 |
| 书面规格确认尝试调用 `request_user_input` | 1 | 当前 Default mode 明确拒绝该工具；不重复调用，改为等待用户直接回复批准或修改意见 |
| 已提交书面规格未获确认 | 3 | `daf40ff` 已自审并提供可点击文档；连续三轮明确请求确认仍无用户回复，按 brainstorming 门禁和 blocked audit 停止自动循环 |
| macOS bundle 实施计划首次补丁格式错误 | 1 | shell 示例的一行遗漏补丁 `+` 前缀；apply_patch 原子拒绝且未创建文件，修正补丁后重新写入 |

## Open Questions

- 正式发布设计文档已提交，等待用户完成 brainstorming 要求的书面规格确认；确认后不再保留产品范围问题。
