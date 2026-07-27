# 面经速查 · 蚂蚁 AI 应用开发（二面）

用法：关掉代码，按表从左到右口述——**先说项目事实，再说通用方案，最后诚实边界**。  
配套：`ARCHITECTURE.md` / `INTERVIEW_QA.md` / `eval/`。

> 来源：蚂蚁 AI 应用开发夏令营二面回忆（约 65min+，无手撕）+ 同场常见 Agent/RAG 深化题。

---

## 三栏怎么用

| 栏 | 含义 | 面试口径 |
|----|------|----------|
| **项目事实** | docs-agent 里真实做过的 | 优先说，可追问到代码/日志 |
| **通用方案** | 业界常见做法（30～60 秒） | 证明你见过生产形态 |
| **边界 / 未做** | 明确没做 + 为何 + 演进 | 比硬蹭加分 |

---

## 1. 开场与实习

| 题 | 项目事实 | 通用方案 | 边界 / 未做 |
|----|----------|----------|-------------|
| 自我介绍 | 文档问答 Agent：解析状态机 + FAISS RAG + 手写 FC 循环；RN SSE 看工具与引用；固定语料 25 题回归 | 场景→链路→差异点（改写/引用对齐/可观测） | 钩子留「评测归因」「cancel 掐 HTTP」 |
| 拷打实习 | 强调可迁移能力：可靠性、评测、边界，不虚构蚂蚁技术栈 | STAR + 与本项目对照一句 | 勿把个人项目说成实习产物 |

---

## 2. 第一个项目向 · 解析 / 评测 / 检索质量

| 题 | 项目事实 | 通用方案 | 边界 / 未做 |
|----|----------|----------|-------------|
| MinerU 跨页表如何保证语义完整？ | PyMuPDF + 文本层不足走 OCR；表在 docx 侧抽成文本块 | 跨页表检测合并；表标题锚点；父 chunk 挂整表摘要 | **未用 MinerU**；跨页表会断——主动承认 |
| 讲一下 Ragas 评测？ | **自研**关键词/`expectMust`/`refuse` 粗判 + `FAILURE_CASES` 人工归因；点踩导出候选 | Ragas：Faithfulness、Answer Relevance、Context Precision/Recall 等 | 未接 Ragas SDK；可说「指标思想对齐，实现更轻」 |
| Answer Relevance 低，怎么判断是检索差还是模型差？ | 看 `toolTrace`/hits 是否含金句；citations 是否对上；FAILURE 分「召回/切分/Prompt/评分误杀」 | AR 低 + Context 差 → 检索；Context 好但答偏 → 生成/Prompt | 无自动 AR 分数；用案例拆解代替 |
| Noise：GraphRAG 召回很多，生成阶段怎么过滤？ | Rerank 截断；Prompt 只依据工具结果；引用收紧（无 `[n]` 不挂假 citation） | Self-Reflection / CoT：「逐句核对是否有证据」；第二轮审核 | 无 GraphRAG；Reflection 未产品化，可口述演进 |
| 了解 GraphRAG 吗？ | 当前向量检索 + 结构切分 | 抽实体关系，多跳用图遍历再生成 | 制度手册优先向量；关系网场景再上图 |
| Agentic RAG：怎么判断检索够不够？何时停搜直接答？ | 有可引用命中则生成；空→改写再搜；`search_docs` 全会话≤2；仍空拒答 | 分数阈值 / LLM-as-judge「证据是否足够」 | 无独立 sufficiency 模型 |
| HyDE：何时好用、何时噪声？ | 有真实 query 改写（空召回、指代） | 短事实/语义鸿沟好用；精确条款/拒答题易偏 | 未上 HyDE |
| Late Chunking 与先切再 embed 区别？ | 先切分再 Embedding | Late：长文先编码再切，跨块上下文更好、算力更贵 | 未实现 |

---

## 3. 第二个项目向 · LangGraph / 记忆 / 状态

| 题 | 项目事实 | 通用方案 | 边界 / 未做 |
|----|----------|----------|-------------|
| LangGraph 里 State 怎么定义与传递？ | **无 LangGraph**：messages 列表 + tool 回灌；会话在 SQLite | TypedDict/Pydantic state；节点读写字段；checkpoint | 对照讲：我们 state≈messages+usage+hits |
| 任务节点很多，State 膨胀 OOM 怎么办？ | 历史窗口截断；工具结果截断；不默认带上轮全文 hits | 只存引用 id；外置 artifact；checkpoint 裁剪 | 无超长多节点图 |
| 长期记忆如何从多轮蒸馏成用户画像进向量库？ | **短期窗口**；每轮新检索；无用户画像向量库 | 定期摘要→结构化 profile→向量/KV；读写分离 | 制度问答刻意不做强画像（减串味/隐私） |
| Agent 长程任务中间一堆结果，怎么结构化存防污染？ | tool 结果进 messages 但截断；citations 只留 id+snippet | 工作区文件 / DB 存中间态，上下文只放指针与摘要 | 无独立 workspace 文件系统 |
| 动态规划：中间结果不符预期怎么改计划？ | 空检索触发改写再搜；轮次/search 硬顶后拒答或结束 | Re-plan 节点；失败写入 state 再分支 | 无通用 planner |
| 推理时 Scaling 在 Agent 怎么体现？ | 有限改写/多轮 tool；有硬顶防爆成本 | 更多采样、自洽、搜索宽度 | 不做无限 Scaling |

---

## 4. 文档切分与噪声（蚂蚁明确问过）

| 题 | 项目事实 | 通用方案 | 边界 / 未做 |
|----|----------|----------|-------------|
| overlap 的作用？ | 窗口切分保留边界连续；结构优先 | 防句子从中切断；过大引入噪声与重复 | 无自适应 overlap |
| chunk size vs 上下文完整性怎么权衡？ | 标题→段落→再窗口；`heading` 进 metadata | 评测驱动：条款完整性 vs 检索噪声 | 未自动优化 chunk |

---

## 5. Agent / 多 Agent / 安全 / 协议（蚂蚁周边高频）

| 题 | 项目事实 | 通用方案 | 边界 / 未做 |
|----|----------|----------|-------------|
| 多 Agent 辩论如何提升质量？辩偏了怎么拉回？ | 单 Agent | 正反方+裁判；用原问题与证据约束；超时收敛 | 未做；手册问答收益低 |
| 历史成功/失败经验库存什么？怎么复用？ | `FAILURE_CASES` + 点踩→`feedback_candidates.jsonl` | 存 query、证据、归因、正确答法；相似题检索 few-shot | 未自动 few-shot 注入 Prompt |
| Skills 和普通 Tool 区别？按需加载？ | 固定 TOOL_DEFINITIONS | Skill=说明+工具包+流程，按意图加载减上下文 | 无 Skill 机制 |
| Skill vs MCP？ | 内置 FC | Skill=产品能力单元；MCP=工具互通协议 | 未接 MCP |
| Constrained Decoding vs Prompt 约束 JSON？ | 用官方 tools 协议 | CD 更稳但依赖运行时；Prompt 便宜但不稳 | 未上 CD 库 |
| 提示注入：「忽略之前指令」怎么处理？ | System/User 分离；只信工具证据答文档题 | 忽略越权指令；工具白名单；输出过滤 | 无专用检测器 |
| HITL：支付等高敏感操作？ | 无支付工具 | 计划→人工批准→执行；审计日志 | 未做；可类比「删库/reparse 需确认」演进 |
| 中心化编排 vs 点对点 Multi-Agent？ | 单环路编排 | 中心化易治理/审计；点对点灵活但难控 | 无多 Agent 网络 |
| Self-Reflection 如何抓逻辑错误？ | 评测与人工归因；引用合法性校验 | 第二模型审「是否每句有据」 | 未在线 Reflection |
| 上下文缓存？ | — | 缓存长 system/工具定义，降 TTFT/成本 | 未专门接厂商 cache（可说了解） |

---

## 6. 概念与开放题

| 题 | 项目事实 | 通用方案 | 边界 / 未做 |
|----|----------|----------|-------------|
| ReAct 原理？为何利于复杂任务？ | 我们是 FC 循环：推理→tool→观察→再推理 | 显式交错 Reasoning 与 Acting，可纠错 | 非教科书 ReAct 文本格式，思想一致 |
| 工具参数幻觉/语法错误怎么自动修？ | 解析 args；异常变 tool 错误信息回灌或 API error | Schema 校验、重试、修复 LLM、拒绝执行 | 无专用 repair agent |
| 如何设计长期记忆？ | 会话消息表 + 短窗口 | 情景/语义/程序记忆分层 | 无跨会话用户画像 |
| 自然语言任务如何变成可靠执行路径？ | 有界工具 + 次数上限 + 状态机解析 | 计划校验、沙箱、HITL | 开放域任务未做 |
| Vibe Coding 理解与经验？ | 项目用 AI 辅助加速，但靠评测/错误码/SSE 契约兜底 | 快速原型 + 工程护栏 | 强调「能演示可回归」 |
| OpenClaw 等本地文件/代码权限？ | RN 不跑本地 Agent 沙箱；文件上传到服务端 | 权限最小化、路径白名单、审计 | 未用该框架则直说不熟，类比权限模型 |
| 反问（建议） | — | ① 组内文档/金融场景更偏 Workflow 还是 Agent？② Ragas/业务 Case 怎么配？③ 实习能否参与评测与护栏？ | — |

---

## 7. 蚂蚁向 · 60 分钟口述优先级

1. **必深讲：** 切分策略与 overlap、检索够用即停、空检索改写、引用与 Faithfulness 思想、自研评测 vs Ragas 对照、SSE 中间态。  
2. **必会嘴炮：** MinerU 跨页表（承认未用 + 方案）、State 膨胀（用你的截断策略类比）、长期记忆为何故意不做强画像。  
3. **慎说：** 不要宣称用了 LangGraph / GraphRAG / Ragas，除非你真接上；用「对齐指标思想」更稳。

---

## 8. 与项目能力对照（自检）

| 已落地可吹 | 选做可抬一口价 | 面试前不做 |
|------------|----------------|------------|
| 结构切分、Rerank、改写/指代、拒答、评测+FAILURE、反馈闭环、SSE、cancel、增量索引 | 混合检索、兄弟块扩召回、轻量答案自检、RQ/Redis | GraphRAG、LangGraph 重写、用户画像向量记忆、Multi-Agent 辩论 |

---

## 9. 阿里云 vs 蚂蚁：侧重点差异（备考）

| 维度 | 阿里云面经更爱问 | 蚂蚁面经更爱问 |
|------|------------------|----------------|
| 解析 | 跨元素、数字敏感、冲突 Prompt、Rerank 延迟 | MinerU 跨页表、Ragas、AR 拆因 |
| 编排 | Workflow vs Agent、SSE、JSON/工具、MCP/A2A | LangGraph State、OOM、长期记忆 |
| 增强检索 | 垂域偏移、混合检索 | GraphRAG、HyDE、Late Chunking、Agentic 停搜 |
| 安全产品 | 共情边界、注入、MCP 大 JSON | HITL、Self-Reflection、经验库 |

两份速查可交叉看：**同一项目事实，换「通用方案」话术即可两边用。**
