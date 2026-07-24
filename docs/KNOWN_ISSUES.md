# 已知问题与限制（Known Issues）

作品集阶段如实列出边界，避免演示翻车，也方便面试谈「和生产差在哪」。

## 产品 / 体验

| 问题 | 影响 | 现状 / 规避 |
|------|------|-------------|
| 无服务端 cancel 接口 | 停止生成靠端上断开 SSE | **已补** `POST /v1/chat/cancel`；阻塞中的单次 LLM 调用仍需返回后才停 |
| 粘贴上传与文件上传并存 | 演示时选一条主路径即可 | 文件上传为主；粘贴作兜底 |
| 历史抽屉与左滑删除手势曾冲突 | 松手误关抽屉 | 已改为仅蒙层 / ✕ 关闭 |
| 扫描版 / 纯图片 PDF | `PARSE_EMPTY` | **P3-D5 待做** OCR；当前需可提取文本的 PDF / docx |
| 加密 PDF / 超 100 页 | 解析失败 | 明确 `errorCode`，提示用户处理文件 |

## RAG / Agent

| 问题 | 影响 | 说明 |
|------|------|------|
| 切分边界 | 超长段落仍可能切断 | **P3-D3**：标题→段落→窗口；chunk 可带 `heading` |
| 检索排序 | 难 query 召回不稳 | **P3-D1**：FAISS Top-N + 云 Rerank（失败本地启发式）；`usage.rerankUsed` |
| `search_docs` 最多 2 次 | 复杂多跳受限 | 防成本失控；**P2-D1** 空命中会服务端改写再搜 1 次 |
| 非流式与流式路径不完全同一套编排 | 评测默认走 `/v1/chat` | 演示主路径仍用 SSE Agent；均已接 Rerank |
| 引用 fallback | 模型忘写 `[n]` 时曾挂前 3 条 | **P3-D2 默认关闭**；`CITATION_FALLBACK_TOP3=true` 可开 |

## 工程

| 问题 | 影响 | 说明 |
|------|------|------|
| 解析曾用 BackgroundTasks | 进程重启丢任务 | **P2-D5～D6 已改**：DB `pending` + 进程内串行队列 + 启动 recover |
| 进程内队列非分布式 | 多进程/多机不能共享 | 演示单机足够；生产可换 RQ/Redis |
| 单机 FAISS + 全量 rebuild | 文档多时删改变慢 | 按 KB 隔离；未做增量删向量优化到极致 |
| SQLite | 并发写有限 | 单用户演示足够 |
| 薄鉴权 | 局域网可直打 API | **P2-D4**：`API_TOKEN` Bearer；LLM Key 仅服务端 |
| Embedding 批次 ≤10 | DashScope 限制 | `EMBEDDING_BATCH_SIZE=10` |

## 客户端联调

| 问题 | 影响 | 规避 |
|------|------|------|
| 真机 API Host | 连不上后端 | `config.ts`：Metro host / `DEV_API_HOST` / 调试页改 IP |
| Android 模拟器 | `127.0.0.1` 指模拟器自身 | 用 `10.0.2.2` 或局域网 IP |
| 选文件无回调 | 上传卡住 | 宿主需转发 `onActivityResult`（已在动态壳修过） |

## 评测

- 自动粗判是关键词启发式，**可能误杀 / 漏杀**；以 `eval/FAILURE_CASES.md` 人工归因为准。  
- 语料为虚构《ACME 手册》，与用户真实上传文档无关；跑评测会再上传一份语料到 `kb_default`。

## 明确不做（当前 Phase）

- 多租户 / ACL、模型热切换 API、点赞点踩、WebSocket 解析进度  
- 自研重排模型、微调 LoRA、OCR  
- 掐断进行中的厂商 LLM TCP（仅协作式 cancel）
