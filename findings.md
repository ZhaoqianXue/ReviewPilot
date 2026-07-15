# Findings

## 2026-07-12 — Initial repository scan

- 工作区：`/Users/zhaoqianxue/Desktop/UA/ReviewPilot`。
- Git：当前位于 `main`，与 `origin/main` 同指向提交 `cc62138`；初次检查未见未提交改动。
- 最近工作集中在抽取 schema/workflow、lead agent 检索、UI 状态拆分和 PDF 下载流程。
- 根目录可见 `README.md`、`requirements.txt`、`frontend/`、`agents/`、`memory/`、`output/`、`design/` 和 `docs/`。
- 存在大量基准目录和真实运行产物，主题包括 LLM 生物医学、HCI、城市规划和 AI 辅助 3D/空间创作；这些是三个真实示例的候选证据，但尚未确认是否代表当前产品支持的正式场景。
- 未发现根目录 `AGENTS.md`，本轮遵循用户消息中提供的 AGENTS.md 指令。
- 初次扫描前没有 `task_plan.md`、`findings.md` 或 `progress.md`。

## Evidence still needed

- README 中的产品定位、启动方式和依赖要求。
- Web App 的实际入口、页面结构与状态流。
- 测试覆盖、外部服务依赖和可复现的本地运行命令。
- 现有三个示例是否有固定输入、预期输出和历史缺陷记录。

## 2026-07-12 — Product and architecture baseline

- 当前产品不是 Streamlit；正式入口是 `web_app.py` 的 Starlette 单体应用，静态前端位于 `frontend/`，通过 `.venv/bin/uvicorn web_app:app --host 127.0.0.1 --port 5602 --reload` 启动。
- 核心用户旅程有六阶段：项目配置、文献收集、LLM 筛选、全文下载、结构化抽取、语义分类。
- 前端是零构建 JavaScript，后端把 `output/{project}/` 投影为 `window.RP_DATA`，并通过项目状态、工作流 action、后台 task 三类 API 驱动。
- 项目依赖真实学术检索平台、LLM API 和 PDF 获取；端到端验收必须区分“代码缺陷”和“外部平台/凭证/访问策略限制”。
- 自动化测试覆盖 Web API、前端契约、状态投影、任务运行器、各 pipeline agent、下载、抽取和分类，具备做针对性回归的基础。
- 最近一次大迁移删除旧 Streamlit/CLI 主入口并加入 Starlette Web App，代码量变化很大；内测风险更可能集中在跨层契约、真实数据长任务、失败恢复和 UI 状态一致性，而非单个纯函数。
- 2026-07-12 最新提交新增 extraction schema、相关测试和 workflow 修复；本轮需要把它纳入真实场景回归，而不是默认其已被真实 UI 充分验证。

## Candidate real examples from existing runs

- `llm-biomedicine-survey`：生物医学综述场景。
- `llm-for-human-computer-interaction-survey` 或 `HCI-AI-3D-Design`：计算机科学/HCI 场景。
- `how-llms-support-urban-planning` 或 `ai-assisted-3d-spatial-authoring-vr-ar-mr`：跨学科/空间设计场景。

这些仅是基于目录名的候选，不能当作最终三个示例，必须查看其配置并由用户确认选择原则。

## 2026-07-12 — Workflow and reproducibility scan

- 快速使用说明与 README 一致：创建会话、填写研究主题/检索词/平台/日期，然后逐阶段执行；未记录正式的内测验收表。
- 当前设计目标是三栏 Ledger UI：左侧项目历史、中间工作台、右侧 ReviewPilot 助手，顶部展示五个可导航工作流步骤。
- Web API 同时支持项目创建、状态刷新、聊天、setup 更新、action 提交与后台任务轮询；这意味着真实验收应覆盖“聊天建项”和“表单编辑”两条入口。
- 前端 `app.js` 是约 1,500 行的单文件零构建实现，包含本地快照恢复、状态归一化、任务轮询和五阶段画布；它是跨状态 UI 回归的高风险集中点。
- 本地 `config.py` 与 `secrets.txt` 均存在；为避免泄露，未读取任何凭证内容。真实外部服务测试在授权范围内具备可行性，但仍需确认是否允许消耗额度。
- 现有 `memory/runs/*/config.json` 使用的字段与当前 README 描述存在差异：抽查时 `research_question`、criteria 和 stage 字段为空，说明这些历史 run 不能未经核对直接作为当前端到端测试 fixture。
- 初次用函数级正则统计测试返回 0，但测试文件数量很多；该统计方法显然不适配当前测试结构，不能据此判断测试为零。下一步应使用 `pytest --collect-only` 获取可信数量。

## Initial quality-risk hypothesis

1. P0：用户无法从建项走完六阶段，或后台失败后 UI 仍显示进行中/成功。
2. P0：状态投影、前端快照与输出目录真实状态不一致，导致错误步骤、错误计数或错误操作门禁。
3. P1：真实平台部分失败时缺少可解释性和可恢复操作。
4. P1：抽取 schema、全文缺失 fallback、分类配置在真实项目中发生跨步骤契约错配。
5. P1：大结果集或长任务轮询导致交互冻结、重复提交或丢失用户上下文。

## 2026-07-12 — Real example evidence and test discovery issue

- 生物医学候选的真实查询来自 `output/llm-biomedicine-survey/search_conditions.json`：LLM/foundation model 与 biomedicine/healthcare/EHR 的布尔检索，平台为 PubMed、arXiv、OpenAlex，起始日期 2023-01-01。历史输出包含多轮 collected、screening 和大量 PDF，说明它确实触发过真实流程。
- HCI 候选来自 `output/llm-for-human-computer-interaction-survey/search_conditions.json`：HCI/UX/interactive systems 与 LLM 的布尔检索，平台为 PubMed、arXiv、OpenAlex，起始日期 2020-01-01；现有输出已到 extraction schema，但未见完整分类产物。
- 空间设计候选来自 `output/ai-assisted-3d-spatial-authoring-vr-ar-mr/search_conditions.json`：AI-assisted/generative AI/LLM 与 3D/VR/AR/MR/XR 的宽泛布尔检索，平台为 PubMed、arXiv、OpenAlex，起始日期 2020-01-01；有多轮 collected、screening、PDF 和质量审计产物。
- 上述三个项目均可提供真实输入，但正式验收必须新建独立 project id 并把每平台上限降至 5–10，不能把历史产物当作当前版本通过证据。
- `.venv/bin/python -m pytest --collect-only -q` 超过 90 秒无任何输出并进入 macOS `U` 状态；普通 Ctrl-C/TERM 未结束本轮 PID 84467。系统中另有一个约 24 分钟前启动的同命令 PID 65658，也处于相同状态。测试数量当前仍未知。
- 该现象可能是测试导入、pytest 插件初始化、文件系统或解释器层面的阻塞；尚无根因证据，禁止直接归因或修复。

## 2026-07-12 — Pytest hang root-cause isolation

- 主机架构为 arm64；仓库 `.venv/bin/python3` 直接链接到 `/Users/zhaoqianxue/anaconda3/bin/python3`，该解释器是 x86_64，并通过 Rosetta 运行。
- `.venv/bin/python -S` 能立即运行，说明解释器启动本身正常。
- 在仓库现有 pytest 9.1.1 代码下，原生 arm64 Python 3.12 使用 `PYTHONPATH=.venv/lib/python3.11/site-packages` 且禁用插件，收集 `tests/test_architecture_cleanup.py`：0.01 秒成功收集 2 项。
- 使用仓库 x86_64 `.venv`，即使设置 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` 且只收集同一个纯 Python 测试文件，仍在约 0.34 秒 CPU 后进入长期 `U` 状态。
- 这排除了测试文件规模和第三方 pytest 插件为必要条件。当前根因定位为：仓库测试环境使用与 Apple Silicon 主机架构不匹配的 x86_64 Python/Rosetta 运行时，并在 pytest 核心加载路径上发生不可中断阻塞。置信度：高；更底层的 Rosetta 内核等待点因 `sample` 无法附加而未知，但不影响修复方向。
- 正确修复方向是重建原生 arm64 虚拟环境并按锁定依赖安装，而不是修改测试代码或增加超时。该修复将在设计获批后的实施计划中作为第一个环境门禁。

## 2026-07-12 — Implementation mapping baseline

- 现有 `requirements.txt` 只有下限约束，没有锁文件；`.gitignore` 忽略 `.venv/`，因此可先并行创建原生环境并验证，无需删除现有环境。
- shell 中的 `python3` 和 `uv` 均来自 x86_64 Anaconda，不能作为原生环境入口；可用的已验证原生解释器是 Codex workspace runtime 的 arm64 Python 3.12。
- `TaskRunner.submit()` 当前总是创建新 task id；项目锁仅让任务排队，不会拒绝或复用重复提交。这与已批准的“禁止重复外部工作”约束直接冲突，是场景运行前可由代码静态证明的 P0/P1 风险。
- `TaskRunner` 只在进程内保存 task 状态，页面刷新能通过 task id 轮询，但服务器重启后任务状态会丢失；本轮先以本地进程内测边界验证，不提前设计分布式持久队列。
- `web_app.project_action()` 当前统一返回 200 + `running`；重复项目 action 没有冲突或 existing-task 语义。
- 工作流 action 集合已覆盖 collect、screen、download、schema、extraction、category；实施应复用该边界，不新增平行路由。
- `tests/test_task_runner.py` 只验证同项目任务串行、成功结果和失败错误；没有重复提交复用、队列状态或 active-task 查询测试。
- 前端 `postAction()` 对所有非 2xx action 只显示 `Action failed: <status>`，没有读取后端 `detail`；这会丢失可行动的冲突或校验信息。
- 前端按钮能在单页面内立即进入 pending 并禁用，但刷新后没有从后端恢复 active task id；因此“刷新恢复正在运行任务”尚无实现证据。
- 核心输出写入广泛使用直接 `Path.write_text()`，包括 extraction schema、categorization 和 sub-agent 归一化产物；批准规格中的原子替换当前没有统一基础设施。
- `LeadAgent.save_search_setup()` 会覆盖 `search_conditions.json`，但不比较配置、不标记或清理 collected 之后的下游产物；修改上游后 UI 仍可能显示旧下游结果。这是可由静态路径证明的数据陈旧风险。
- `state_projection._current_step()` 和 `_is_done()` 主要根据目录或文件是否存在判断阶段完成；空目录、部分文件或失败报告都可能推进步骤。显式 `partial`/`failed` 状态尚不存在。
- `ExtractionAgent` 在开始时直接清空正式 `extraction_results.jsonl`，随后逐行追加；若进程级异常逃逸，旧有效结果已经丢失且部分文件可能被投影为完成。这是需要事务式临时输出路径的高优先级完整性缺陷。
- `utils.jsonl_handler` 是多数 agent JSON/JSONL 写入的共享边界；新增一个 `reviewpilot_core.atomic_files` 原语并让该工具层复用，可用最小范围覆盖大多数阶段产物，同时避免散落重复实现。
- 前端异步状态不能只用单一“任务代次”表达所有权：任务生命周期变化与项目导航是两类独立事件。最终实现将任务监控所有权与聊天导航所有权分离，否则同项目任务完成会错误丢弃稍后到达的有效聊天回复。
- 仅做前端源码字符串断言无法验证异步交错；本仓库可由 Python 测试调用现有 Node 22，执行生产导出的纯所有权控制器和轮询注册表，无需引入前端构建链或下载浏览器。
- 聊天 API 和轮询 API 必须返回同一权威项目投影；使用缺少 `activeTask` 的局部状态会在任务运行中静默清除前端监控。
- “写到临时文件”本身不足以保证事务语义：如果输出 writer 位于宽泛业务 `try` 内，磁盘/序列化异常会被错误转换成业务 error row，随后仍提交半有效文件。输出调用必须位于领域异常捕获区之外。
- 原子写入采用目标同目录临时文件和 `os.replace`，保证单文件替换及异常清理；它不提供多文件组事务。当前新文件/替换文件权限为 `mkstemp` 默认 0600，适合单用户本机内测，若未来进入共享或多用户部署需明确规范化权限。
- 场景目录字段不能与应用保存的 `search_conditions.json` 做整文件相等比较：应用会生成 `search_queries`、`date_range`、路径、模型和派生字段。可复现验证必须使用显式 catalog→persisted projection，并忽略列明的系统生成字段。
- 仅记录未跟踪历史文件路径和 SHA 不足以让干净检出复现输入；仓库需要去敏不可变快照和可重新计算的 manifest，同时把本机历史原件 SHA 明确标注为外部来源元数据。
- 运行项目编号必须同时扫描 output 项目和已有报告，取最大 `N+1`，并在应用返回 slug 与请求 ID 不一致时中止；否则应用自动去重后会让项目名、目录 ID 和证据报告互相错位。
- 真实浏览器证明根级委托 click 处理器不能把“没有 data-act”解释为“需要整页重绘”：表单 submit 的默认激活发生在 click 派发后，同步替换 DOM 会让 submit 事件完全消失，并静默丢弃尚未同步到 state 的输入值。
- 全局 `Max/source` 与每源 map 同时存在时，payload 不能无条件以旧 map 反推 max；对话框值发生变化应同步到全部已选来源，值未变化时则必须保留用户已有的差异化 per-source limits。
- 冻结场景的 `platforms` 是有序配置；产品新项目默认顺序与历史权威输入冲突时必须选择一个权威模式。选择较新、可验证的冻结目录顺序，旧默认模式单独修复，不按集合相等混过去。
- LLM 可以撰写阶段摘要，但不能决定工作流路由。`prompt_extraction` 被遗漏在提示策略后，模型把下一步错误推断为 Categorization；同时代码 mapping 又返回 download。草稿/已 finalized 的下一步必须由代码分别确定为 Finalize Schema/Information Extraction。
- 已知 P3：前端 source-limit 变化比较使用 `Number()`，接受后端严格整数解析拒绝的 `1e2`、`75.0` 等格式；不影响本轮合法正整数场景，需在统一输入校验任务中收口。
- `suggest-categories` 与 `categorize` 不能共享同一个 stage-only 路由：前者只是可编辑建议，必须指向 Review/Confirm/Apply；后者才是完成且无下一步。action 是确定性路由所需的最小区分信息。
- 机器 artifact 路径可以保留在结构化任务结果中，但送入摘要 LLM 和显示给用户的副本必须递归脱敏，并在最终回复上再次防御性清理，避免用户名/工作树路径进入 UI 或证据。
- 自由文本中的绝对路径边界本质上不可由通用分隔符正则可靠恢复：逗号、空格、括号既可能属于文件名也可能属于后续结果文本。安全设计应只做路径“存在性检测”，一旦命中就丢弃整条生成回复，再从可信结构化结果按阶段契约重建摘要。
- 安全回退不能使用通用计数字段列表拼接；各 agent 的真实返回契约不同。Collection 需要去重 `total`/`total_papers`，Filtering 使用 `included`/`excluded`，Download 的 fallback candidates 位于嵌套 `stats`，Extraction 使用 `processed`/`errors`，Categorization 使用 `categories`/`rows` 且无下一 canvas action。
- “产物存在于服务器状态”不等于“用户完成了导出”。正式内测用户不能依赖本地 filesystem path；最终画布必须通过 project-scoped、allow-listed 下载端点提供存在的 export items，并把路径完全留在服务端。
- 新项目有两个不同入口：聊天入口会从主题自动派生 project id，助手头部 setup 表单才允许显式指定 project name。确定性证据运行必须先打开 setup 表单，不能把聊天建项用于需要预留 id 的协议。
- 导出 allow-list 必须验证“精确物理文件”，仅做 resolve 后的项目根 containment 不够：同项目 symlink 仍可把固定 export key 指向其他私有文件。单用户本机边界下采用逐路径组件 `is_symlink()` 拒绝；若未来进入多用户/不可信本地写入场景，需要 descriptor-level no-follow serving 消除校验到打开之间的 TOCTOU。
- 阶段真相不能继续由文件存在性推导：显式 `workflow_state.json` 已成为新项目与一次性旧项目迁移后的唯一阶段事实源，并以 `not_started/ready/running/partial/failed/completed` 六态、attempt、safe error、counts、stale 与 last-valid 元数据表达生命周期。
- “原子替换”只能防半文件，不能防并发 lost update；账本的 load/migrate/read-modify-write 必须共享 resolved project path 级 RLock。并发回归测试必须把子线程异常传播到主线程，timeout 只能作为 liveness guard，不能决定写入顺序。
- 旧项目迁移是不可逆决策，不能只检查 artifact key 存在；collection 计数、screening/extraction 身份与空结果统计、retrieval canonical counts、categorization mapping key/value/category membership 都必须语义有效并保持阶段连续性。
- setup 与 ledger 是跨文件事务；仅用 pending 标记 fail-closed 不足以恢复。最终协议必须 abort-first：先持久化 current/target、ledger snapshot 与 `phase=abort`，setup 和 stale ledger 均成功后才晋升 `apply`，从而让 promotion 前崩溃回滚、promotion 后崩溃幂等 roll-forward。
- stale 是终态结果有效性，不应遮住同阶段刚生成的 schema/category suggestions。无需扩张账本 schema，可用 ready status 的当前 attempt 相对 terminal last_valid attempt 判断新鲜中间产物，同时继续屏蔽旧 terminal result、activity 与 export。
- stale 阻断必须覆盖主摘要、辅助 activity、旧 canvas-action chat、磁盘 glob 计数、quiet actions 和兼容步骤状态；只清顶层 payload 会被后续 helper 重新从文件系统读回旧数据。
- partial/failed 的终态判定必须依赖精确、相互一致且能与正式 artifact 对账的 agent contract；缺字段、根/嵌套别名冲突或计数与产物不一致都必须 fail loud，不能用默认零值把未知结果伪装成完成。
- 结构化全失败报告仍是当前尝试的可信事实，因此当前阶段保持非 stale 且可展示；逸出异常没有可信新报告，当前及有材料的下游必须保持 stale。用户界面必须区分这两类失败，不能都显示 `0 completed · 0 failed`。
- 上游重跑的 stale 失效必须发生在 `start_action` 持久化 running 状态时、任何领域写入之前；若等完成或异常后才失效，会形成“新上游产物 + 旧下游可导出”的竞态窗口。服务重启后的 orphan reconciliation 必须保留同一规则。
- 下载目录现有 PDF 总数 `pdf_count` 与本次下载成功数不是同一语义，不能作为相等别名参与当前尝试的严格契约；extraction 统计同样只能把成功/legacy 行计入 processed，显式 error 行必须单独计数与展示。
- 严格“行数对账”仍不足以建立 artifact 边界：source key 必须是安全单文件组件，agent 返回的 artifact root 必须与项目权威根一致，根目录和正式文件不得是 symlink；否则 `../`、Windows 路径或安全文件名 symlink 都可把项目外 JSONL 带入后续筛选。
- ExtractionAgent 对零 papers 会原子提交存在但为空的结果文件并返回 processed=0/errors=0，因此规范空文件是合法零结果；真正应 fail loud 的是结果文件缺失、根/文件链接、任一非空行 malformed/non-object，或不属于 legacy空/success/error/failed 的未知状态。不能把严格解析与禁止合法空结果混为一谈。
- JSON falsy 不能借由 `or ""` 冒充 legacy 空状态：extraction_status 只有字段缺失或 string trim 后为空才是 legacy success；字段存在但为 null/bool/number/list/object 必须 fail loud。严格契约必须先验证类型，再做规范化。
- failed-only retry 的可重试性不能相信 ledger 标签本身：必须先严格验证 download report 的整数计数和明细数组，再用统一 structured outcome 重算 partial/failed，并与 ledger counts、stale 和精确 terminal error 全部对账。
- 重试身份使用冻结优先级 `id → doi → url → title` 的规范化 SHA-256；用户界面只接收 opaque ID 和经过安全筛选的 label/failure class，任何不安全高优先级显示候选必须继续尝试安全低优先级候选。
- `dataclass(frozen=True)` 只冻结字段绑定，不冻结嵌套 dict/list。事务回滚基线必须递归不可变；目标合并只能通过显式方法取得每次互不共享的普通容器副本，否则 revision 与被污染的旧事实会分离。
- 二次确认不是布尔开关，而是对服务器当前 revision 与“有序选择列表”的精确绑定；挑战和确认都不能排序、去重或按集合比较，否则用户看到的选择与实际执行对象可发生错配。
- retry workflow 的 terminal result 必须使用合并后总体 report counts，而不是本次 staging 子集 counts；否则仍有未选失败项时会把 aggregate partial 错报为 completed。
- 外部下载器的输出不能因为计数正确就进入事务：staging 必须同时对账 selected identity/order、included row flag/path、report detail、PDF direct-child/symlink/header、result root/nested counts，并把成功/失败字段规范为互斥 schema。
- 下载派生字段必须显式 allow-list。宽泛 `pdf_*` 或 `web_search_fallback_*` 豁免会把未知内部 metadata 绕过 source deep-equality 后带入正式候选产物。
- staging outcome 的 primary facts 可规范化补齐缺失项，但显式冲突必须 fail loud；分类列表只能从已清洗的 failed details 重建，不能同时保留 row/report 两套互相矛盾的状态。
- 纯 merge 不能只相信 staging 阶段已经验证过的 dataclass：revision、selected source、当前 staged schema、report detail provenance 和 aggregate counts 必须在合并入口重新验证，同时不得触碰文件系统；文件存在性与指纹属于后续 publication planning 边界。
- 真实下载器对缺失身份字段使用 `.get(key, "")`，因此 provenance 兼容必须精确表达“key 缺失可对应空字符串”；不能把 source present 的 `None`、非空伪造值或其他 metadata 也平均成空值。
- row/report 两处值相等不等于 schema 有效；方法字段可同时被注入相同 dict/list/bool。成功下载方法的权威合同必须先要求非空文本，再检查 aliases 与 row `pdf_method` 精确相等。
- 仅保存 staged PDF 路径无法检测“仍有合法 PDF header 但内容已替换”；staging outcome 必须当场冻结流式 size/SHA-256，publication planning 与实际 copy 前后均需复验。
- `type(proxy) is MappingProxyType` 不能证明其 backing mapping 是普通 dict；安全边界不能验证原 proxy 后继续使用它。必须单次物化为新建普通容器、重建 trusted dataclass，并只使用可信副本。
- 绝对路径绑定必须发生在任何可执行用户对象方法之前。相对 project 即使已经计算过 resolved path，若后续仍保留原相对对象，自定义 mapping 的 `items()` 改变 CWD 后仍可让权威读取与目标路径指向不同项目。
- 回滚快照若需隐藏绝对路径，逐字段 sentinel 转换不是双射且遗漏 JSON key；应把完整 canonical JSON 作为一个 UTF-8 blob 整体编码，并在恢复时验证 Base64 与 canonical JSON 都唯一。
- durable marker 的可信 before facts 必须来自重新读取的 current authority，而不是已确认请求对象；request/preparation 只负责 revision 与 ordered selection 授权，即使其嵌套对象被伪造也不能影响回滚内容。
- marker-last 只有在每个前置步骤故障都留下 marker、且二次恢复验证完整 ledger 与 owned cleanup 时才有测试意义；只测一次 included 写失败和 retrieval stage 局部相等会让错误的提前删 marker/局部恢复实现逃过套件。
- inode/bytes generation 校验若与 path replace/unlink 分成两步，仍存在真实 compare→mutation 窗口；cooperating processes 的 marker 创建、CAS 与删除必须共享同一个稳定的跨进程临界区。可替换 lockfile 不是稳定锁域，项目目录 inode flock 更符合当前本机项目模型。
- 原子 no-clobber publish 不能只看“竞争时不覆盖”：`link(temp, marker)` 后到 `unlink(temp)` 前崩溃会留下完整但 nlink=2 的 marker。recovery 可且只能在目录锁内识别唯一保留名、同 device/inode/nlink 的发布 temp，删除后 fsync 并复验 marker generation；其他 hardlink 形状继续 fail closed。
- 内存 reentrancy 状态跨 fork 会被子进程继承；跨进程锁的嵌套缓存必须绑定 PID，child 只能关闭自己继承的 descriptor copy 后重新获取锁，不能把父进程的 depth 当成本进程 ownership。
- 当前 retry 锁保护的是 cooperating process 对稳定 project root 的单次临界操作，不是通用多 worker ownership 协议。若支持外部 project rename/replace、hostile same-user filesystem 或跨进程长事务，必须增加 root identity pinning 与 durable owner/lease，不能把本轮 advisory lock 直接宣传为分布式事务。
- record target 在写 marker 前必须完成可恢复性等价验证：source commitment、target PDF provenance、before→target exact fact delta、terminal ledger、fixed authority/PDF baseline 和 marker generation 缺一不可；“decoder 能读”不等于“apply 后事实正确”。
- destination hash 相同不证明事务所有权，candidate basename 更不能作为 abort 删除授权。可恢复 publication 必须在 destination 可见前，把 transaction-owned staging temp 的 inode/size/hash 和目标绑定进 marker receipt；abort 只处理 receipt 证明的 inode，未收据 candidate 即使名称完全匹配也必须保留。
- direct O_EXCL copy 后再补 receipt 存在无法判属的 crash gap；先在 marker-owned staging 完成 temp copy/fsync，再 CAS receipt，再 hard-link destination，可把 CAS 前垃圾交给 staging cleanup、CAS 后状态交给 receipt recovery，同时保持 destination no-clobber。
- cleanup 的 `validate(path) → unlink(path)` 仍会被最后窗口替换。必须先把路径原子移动到 receipt-owned quarantine slot，再对移动后的 inode/content 做验证；foreign 被移动时要恢复或在 durable recorded slot 中保留，不能让后续 staging cleanup 猜测并删除。
- 随机但未持久化的 quarantine 名不是 recovery 状态；进程在 rename 后崩溃会让下一轮看不到文件。quarantine directory 的 path-safe name/device/inode 必须在 publication receipt 中预先提交，固定槽位 collision/unknown child/identity drift全部 fail closed。
- crash recovery 的每个删除状态都要可重入：owned file 删除、qdir rmdir、staging rmtree、marker unlink 之间分别需要目录 fsync。marker 尚存但 qdir 缺失只有在该 receipt 所有 original/q-slot path 都已清空时才能解释为完成，否则是损坏。
- apply readiness 不能只逐个验证 marker 已声明的 source/receipt；必须枚举整个 staging hierarchy 并与 committed exact set 对账，否则未声明的 foreign sibling 会绕过校验，zero-source 状态尤其容易退化为空循环。
- durable marker CAS 的提交点不只是 `os.replace`：新 marker temp 必须先 file fsync，替换前再核对旧 generation，替换后 fsync 项目目录并回读；回读还必须绑定到仍打开的 temp fd identity，单独比较 transaction id 与 raw bytes 无法拒绝 same-bytes/new-inode ABA。
- raw marker generation 校验不能替可变 derived facts 背书：只要 caller 保留合法 raw bytes 与 inode identity，就能单独伪造内存中的 before/target。所有 authority 分类与写入决策必须使用同一 generation 重新从磁盘严格解码得到的 fresh facts。
- 固定 authority 的 `lstat→os.replace` 在可移植 POSIX API 上没有 compare-by-inode 原语；当前正确性边界依赖所有 ReviewPilot writers 共享 project-directory flock 与 Web task mutex。若威胁模型扩大到能绕锁的 same-user/hostile filesystem writer，必须设计持久 backup/receipt 状态机；不能把 PDF candidate 的 foreign-ownership合同不加区分地套到事务获授权替换的固定 authority 上。
- `rmtree` 成功不等于目录删除已 durable；若紧随其后的 project fsync 失败，重入看到 staging 缺失时仍必须再次 fsync project，并在 fsync 后复核 marker generation，不能把“路径当前不存在”直接当作持久完成。
- apply commit 采用 write-ahead 决策更简单：durable `phase=apply` 是唯一不可逆提交点，marker 之前所有状态 rollback，marker 之后 authorities 从任意 B/T 组合 roll-forward。无需在 abort phase 预写 target authorities并承担额外 mixed-state rollback分支。
- zero-success publication 仍需要显式 durable receipt boundary：空循环不是“已发布零项”的持久事实。必须写入 canonical `published={pdfs:[]}`，否则 strict apply decoder无法区分“合法零PDF完成”与“publication尚未开始”。
- public apply 不能只依赖进程内 ACTIVE reservation 存在；caller 必须携带 expected transaction id 并在 readiness、promotion 和 release 三处精确比较，否则旧调用在 A abort、B begin 后可能错误提交 B。
- durable commit 的 API acknowledgement 不是提交事实。marker-last apply 在最终 marker unlink 后可能因目录 fsync 报错；Web 只有通过独立、只读、exact authority/PDF verifier 证明目标事实已提交，才能避免把已提交事务错误回滚或向用户报告失败。
- 恢复面板中 opaque record ID 既不能进入 DOM attribute，也不能写入 sessionStorage；UI 只用服务器顺序索引绑定 checkbox，提交时再从当前权威 recovery projection 取 ID，避免持久化或 HTML 可见面扩大标识暴露面。
- retrieval aggregate 的 `downloaded` 与正式 included rows 中历史 `pdf_downloaded=true` 不是同一集合；Recently retrieved 必须优先按本次 report downloaded details/flags 投影，只在 legacy report 缺少明细时使用 success-count fallback，否则失败项会被错误显示为已完成。
- recovery 请求中的 confirmation 是 revision 与 ordered opaque IDs 的联合承诺；revision conflict 后必须让服务器权威状态同时赢过本地 retry selection 和异步旧响应，不能只刷新画布文本。
- 文本清理若在识别平衡 parenthetical 前调用字符集 `strip("()")`，会把合法右括号先吞掉，使后续正则留下未闭合可编辑标签；结构化展示应先处理平衡结构，再清理真正的外层符号。
- 真实 Sub Agent 的正式产物合同与其运行 sidecars 必须区分：DownloadAgent 除 `filtered/`、`pdfs/` 外还写 `download_stats.json` 与 agent state/log。事务 exact-tree 不能放宽接纳这些 sidecars；应在来源提交前验证并清理固定 allow-list，未知项继续拒绝。
- all-failure retry 是最容易暴露 sidecar 漏测的生产形状：业务上应提交 aggregate partial 并保持失败项可重试；若 apply readiness 因辅助文件拒绝，abort-first 仍能保护权威事实，但用户会得到错误的 task-level failure。

## 2026-07-12 — Native baseline failure evidence

- 隔离工作树首次收集：207 tests、11 collection errors，全部是缺少被 Git 忽略的本地 `config.py`。仅链接主工作区 `config.py` 后：286 tests collected、0 errors，证明工作树本地配置注入是直接原因。
- 原生全量测试：284 passed、2 failed、0 skipped、7 warnings，103.68s。测试环境已不再发生 Rosetta 挂起。
- 失败 1：`test_benchmark_runner_can_create_optimized_downloader` 直接动态导入 `.benchmark_step3_download/benchmark_step3_download.py`，但整个目录被 `.gitignore` 排除；干净检出不可能满足该测试。紧随其后的生产 factory 测试已经覆盖 tracked `utils.pdf_downloader.create_pdf_downloader` 的默认 optimized 行为，因此 benchmark 测试当前验证的是本地 scratch 工具而非可发布产品契约。
- 失败 2：完整 Web contract smoke 已 patch 多个 LLM 边界并传入 `fake_llm`，但某条调用仍落入真实 `utils.llm` 密钥读取；链接 `secrets.txt` 会掩盖测试隔离缺陷并可能产生费用，禁止作为修复。
- 下一步需用单测完整 traceback 找到第一处未注入的 LLM 边界，再用失败测试证明修复；不能简单扩大 secrets 权限。
## 2026-07-14 — HCI r3 findings

- `schemaWorkbench.status=missing` must be a first-class frontend branch. Treating every non-finalized state as a draft creates a dangerous false affordance: users can attempt to finalize zero fields while the assistant independently claims the schema is ready.
- Workflow context labels must use the same structured artifact fact as the action gate. Step number alone cannot distinguish “ready to generate” from “draft ready for review.”
- A fixed request for 5–10 categories is structurally wrong for small reviews. For seven papers it induced seven near paper-specific labels. The suggestion target now scales with sample count, caps at five, and asks for materially fewer broad reusable categories than papers.
- Mandatory editable confirmation was not cosmetic: it prevented a low-quality LLM taxonomy from becoming the final artifact and allowed a four-theme correction without abandoning the run.
- Multiple-mode category counts legitimately exceed paper count; release evidence must state the selected mode so the distribution is not misread as duplicate rows.
- HCI r3 retrieval succeeded 7/7, so retry was not artificially forced. The real partial/retry evidence remains HCI r2, while the repaired exact transaction paths remain enforced by the 701-test candidate suite.

## 2026-07-14 — Spatial and joint-release findings

- Count-aware language is product logic, not polish: unconditional plurals made a correct one-paper result look machine-generated and reduced trust at the exact point users judge the final deliverable. The count formatter and LeadAgent pronoun now share explicit cardinality behavior.
- A one-paper spatial result is truthful evidence for empty/singular boundaries but is not sufficient evidence for multi-row analysis quality. The seven-paper HCI run supplies that independent cardinality coverage; the three examples should be evaluated as a portfolio rather than averaging their sample sizes.
- A release audit must reread authoritative state, not merely trust individual reports. The joint check proved that all 15 stage records remained completed/non-stale, all Schemas remained finalized, and all 21 exports were still downloadable after later code iterations.
- “Ready for formal inner beta” and “validated by internal users” are different claims. Engineering evidence supports the former with high confidence; only a 3–5-person unassisted cohort can establish the latter.
- The 1440 × 900 viewport requirement remains an explicit pre-invitation human smoke check. The earlier recovery flow was exercised at both supported sizes, while the final three-example evidence is primarily 1280-wide; this distinction must remain visible in the release record.

## 2026-07-14 — Formal-release gap findings

- “Formal release” is currently ambiguous between a packaged local tool, an authenticated self-hosted team service, and a public SaaS. These are different products, not deployment variants; selecting one is the first release decision.
- The existing repository has no Dockerfile, compose manifest, packaging metadata, release workflow, changelog, health endpoint, or immutable dependency lock. A green source-tree test suite cannot prove that a user can install and run the product reproducibly.
- All runtime dependencies are lower-bound ranges. A future resolver can install an incompatible combination even if the current environment passes; a release needs a tested lock or constraints artifact and a clean-environment install proof.
- The process-global in-memory task runner and repository-local output root define a single-process, single-machine authority model. Public/multi-user release would require persistent jobs, identity/authorization, per-tenant storage, quotas, and concurrency semantics rather than superficial middleware.
- Release diagnostics must inspect only configuration contracts and presence, never print ignored local configuration files. The audit command that displayed machine-local values is itself evidence that the release checklist needs a secret-safe inspection rule.
- The frontend fetches Google Fonts and Phosphor icon CSS from public CDNs. A local v1.0 therefore is not currently self-contained and can render differently or lose icons offline; release design must either vendor these assets with their licenses or explicitly require network availability. The recommended design vendors them.
- Credential loading is fragmented across `config.py`, `secrets.txt`, direct environment variables, and agent-specific fallback paths. A release configuration layer must become the single contract and preserve compatibility only through an explicit migration path.
- `TaskRunner.shutdown()` exists but Starlette does not register an application lifespan hook, so a formal release cannot yet prove orderly task-executor shutdown. Startup reconciliation is lazy per project state read rather than an explicit boot audit.
- The bootstrap script is a development helper restricted to Apple Silicon and installs mutable development requirements. It is not a cross-platform end-user installer or release verifier.
- The repository README declares an MIT license, but no tracked `LICENSE` file exists. `CHANGELOG.md`, `SECURITY.md`, packaging metadata, dependency lock/constraints, and container/build manifests are also absent; these are release-artifact gaps, not cosmetic documentation gaps.
