# 模拟面试自问自答（D20）

用法：关掉代码，对着下面卡片口述；卡壳处回看 `ARCHITECTURE.md` / 源码再讲一遍。  
每题目标：**先 30 秒结论，再按需展开 1～2 分钟**。

---

## 卡片 01 · 项目一句话

**问：** 用一句话介绍这个项目。  
**答：** 私有文档问答 Agent：RN 薄客户端 + FastAPI 上的 RAG 与手写 Tool Loop，SSE 流式下发工具轨迹和答案，引用可点回 chunk 原文。

---

## 卡片 02 · 为什么不是套壳 Chat

**问：** 和直接调千问聊天有什么区别？  
**答：**  
1）文档要过状态机，未 ready 不能搜；  
2）模型通过 Function Calling 调 `search_docs`，不是把全文塞 Prompt；  
3）自定 SSE 拆开 tool / delta / completed；  
4）citations 与 DB 主键对齐，点开二次拉全文；  
5）有 requestId、usage、20 题评测。

---

## 卡片 03 · RAG 链路

**问：** 文档从上传到可问答经过什么？  
**答：** 落盘 → `pending` → 提取文本 → 切分写 `chunks` → Embedding → 按 KB 重建 FAISS → `ready`。失败写 `errorCode`。删除/reparse 会清 chunk 并重建索引，避免旧向量。

---

## 卡片 04 · 为什么正文不放向量库

**问：** FAISS 里存什么？为什么？  
**答：** 只存向量和 chunkId 映射。正文唯一来源在 SQLite，避免两处文本不一致；展示、引用、Citation 都回表查。

---

## 卡片 05 · Agent Loop

**问：** Tool Calling 怎么跑的？  
**答：** 手写循环：带 tools 调模型 → 若有 `tool_calls` 则执行并把结果以 role=tool 回灌 → 最多 6 轮；`search_docs` 全会话最多 2 次防刷。无 tool 后出最终回答（伪流式或真 stream）。

---

## 卡片 06 · SSE 事件

**问：** 端上怎么知道「在检索」还是「在出字」？  
**答：** `message.accepted` → `tool.started/completed` → `message.delta` → `message.completed`；出错有 `error`。Envelope 带 `requestId/seq`。

---

## 卡片 07 · 幻觉怎么控

**问：** 如何减少胡说八道？  
**答：** Prompt 限制只依据工具结果；无命中则拒答；citations 只映射本轮 hits 的合法 `[n]`；端上点引用必须能打开真实 chunk，删文档后 `CHUNK_GONE`。

---

## 卡片 08 · 记忆 vs 检索

**问：** 多轮对话会不会把上轮检索全文一直带着？  
**答：** 不会。历史只保留最近约 8 条 / 3000 字的 role+content；每轮检索是新的 tool 结果。这是成本与串味控制。

---

## 卡片 09 · 引用二次查询

**问：** Citation「二次查询」是模型又搜一遍吗？  
**答：** 不是。SSE 只带短 snippet；用户点击后 RN 调 `GET /v1/chunks/{id}` 拉全文，是 UI 按需加载。

---

## 卡片 10 · 停止生成

**问：** 停止按钮怎么做的？  
**答：** 端上先 `POST /v1/chat/cancel` 再 Abort 断开 SSE。服务端 set requestId 取消标记，并 close 该轮绑定的 LLM httpx 连接以打断阻塞中的厂商调用；随后在 tool/delta 间隙抛 `GenerationCancelled`，落库 `cancelled` 与已推送部分文本。多进程/多机下 cancel 仍是进程内的，生产可换共享总线。

---

## 卡片 11 · 可观测性

**问：** 线上一条问答怎么排障？  
**答：** 气泡上的 `req_…` 对齐日志与 messages 表；看 toolTrace、usage（latency、searchCalls）；HTTP 另有 `X-Request-Id`。

---

## 卡片 12 · 评测

**问：** 怎么证明效果？  
**答：** 固定 ACME 手册语料 + 20 题（事实/改写/拒答）；脚本跑 `/v1/chat` 做关键词粗判；失败记入 FAILURE_CASES 归因（切分/评分误杀/Prompt）。粗判不能代替人工。

---

## 卡片 13 · 和生产差距

**问：** 和公司真实系统差在哪？  
**答：** 单机 FAISS+SQLite、进程内解析队列（非 Redis 分布式）、薄 Bearer 鉴权、无多租户 ACL。演进：RQ/Redis、按租户索引、网关鉴权、rerank、增量删向量。

---

## 卡片 14 · Key 与安全

**问：** Key 放哪？  
**答：** 只在服务端 `.env`；RN 只打自家 API。当前无用户鉴权，演示勿公网裸奔。

---

## 卡片 15 · 分包架构

**问：** 为什么 RN 用动态分包？  
**答：** 与现有宿主一致：`docs-agent` 是业务包，壳负责加载；Agent 大脑不塞进包体积，也符合 ToB「端薄中心化」分工。

---

## 白板题（可选，限时 5 分钟）

1. 画「一次提问」时序（含 tool 与 delta）。  
2. 画文档状态机。  
3. 说明删文档后为什么要重建 FAISS。

对照图见 [DIAGRAMS.md](./DIAGRAMS.md)。

---

## 自测记录（自己填）

| 日期 | 不看稿过关数 /15 | 最卡的 3 张 | 明日补 |
|------|------------------|------------|--------|
|      |                  |            |        |
