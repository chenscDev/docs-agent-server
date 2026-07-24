# 架构说明（Architecture）

面向面试 / 作品集：讲清「一次提问怎么走完」以及「为什么这样切」。

## 1. 仓库职责

| 仓库 | 职责 |
|------|------|
| `docs-agent-server` | Agent 编排、RAG、模型 Key、SSE |
| `rn-biz-0.86` → `src/docs-agent` | 业务 RN 分包（UI / 上传 / 流式渲染） |
| `rn-dynamic-0.86` | 原生壳 / 动态分包宿主（调试挂载 Metro） |

原则：**RN 是薄客户端；大脑在服务端。** Key 永不下发到端上。

```text
┌─────────────┐   HTTP/SSE    ┌──────────────────┐   OpenAI兼容   ┌────────────┐
│ docs-agent  │ ────────────► │ docs-agent-server│ ─────────────► │ 通义千问   │
│ (RN 分包)   │ ◄──────────── │ FastAPI          │ ◄───────────── │ Chat+Embed │
└─────────────┘               │  SQLite │ FAISS  │                └────────────┘
                              └──────────────────┘
```

## 2. 后端分层

```text
app/
  api/       # 对外 HTTP（给人 / App）
  agent/     # 手写 Tool Loop + Prompt + 历史窗口 + SSE
  rag/       # 解析 / 切分 / Embedding / FAISS
  db/        # 五表：kb / documents / chunks / sessions / messages
  core/      # 配置、LLM 客户端、错误码、request 日志
```

| 层 | 存什么 | 不存什么 |
|----|--------|----------|
| SQLite | 文档元数据、chunk **正文**、会话消息、citations | 向量 |
| FAISS | 向量 + `id_map`（chunkId） | 正文（避免双源真相） |
| 上传目录 | 原始文件 | — |

**面试要点：** 向量库只负责相似度检索；展示原文、引用对齐一律回 SQLite 的 `chunks.id`。

## 3. 文档状态机

```text
upload/reparse
      │
      ▼
  pending ──► parsing ──► indexing ──► ready
                 │            │
                 └──── failed ◄┘
```

- 仅 `ready` 的文档参与检索；否则聊天返回 `NO_READY_DOC`。
- 删除 / reparse 会清 chunks，并 **重建该 KB 的 FAISS**，保证索引与元数据一致。
- 解析投递：`enqueue_parse`（进程内串行队列）；`pending` 已落库即「至少一次」；启动扫描 `pending|parsing|indexing` 续跑。

进度字段：`status` + `progress` + `stageMessage`（端上轮询用）。

## 4. 一次问答时序（SSE 主路径）

```text
用户发问
  │
  ▼
POST /v1/chat/stream
  │  message.accepted          # 用户消息已落库
  ▼
Agent Loop（最多 6 轮 tool）
  │  tool.started / completed  # search_docs / list_documents
  │  （search_docs 全会话最多 2 次；空命中可服务端改写补搜）
  ▼
流式生成
  │  message.delta             # 增量文本
  ▼
收尾
  │  message.completed         # answer + citations + toolTrace + usage
  │                            # usage 含 searchQueries / rewriteUsed
  ▼
RN 渲染气泡 / 工具卡 / 引用角标
  │
  用户点 [n] ──► GET /v1/chunks/{chunkId}   # Citation 二次查询（仅 UI）
```

**记忆 vs 检索（易混点）：**

| 概念 | 机制 | 是否每轮全量进模型 |
|------|------|-------------------|
| 会话历史 | 最近 ≤8 条且 ≤约 3000 字 | 否，截断窗口 |
| 本轮检索 hits | `search_docs` 结果 | 是（本轮 tool 结果） |
| 上轮 hits | **不**自动带入下一轮 | — |

## 5. Agent 与「裸 RAG」

| 路径 | 接口 | 行为 |
|------|------|------|
| 非流式 RAG | `POST /v1/chat` | 服务端固定先检索再生成（便于评测 / 对比） |
| 流式 Agent | `POST /v1/chat/stream` | 模型 Function Calling + 手写 loop |

工具（仅服务端内部，不对公网暴露）：

| Tool | 作用 |
|------|------|
| `search_docs` | 向量检索，返回 hits（含 chunkId） |
| `list_documents` | 列当前 KB 已 ready 文档 |

## 6. 幻觉控制（证据门禁）

1. System Prompt：只依据检索片段回答；无关则拒答。
2. 引用 `[n]` 必须对应本轮 hits 的 `index`；非法序号丢弃。
3. 对外 `citations[].chunkId` 必须能被 `GET /v1/chunks/{id}` 打开；文档删除后返回 `CHUNK_GONE`。

## 7. 可观测性

- HTTP：`X-Request-Id` + 访问日志 `durationMs`
- Agent：`requestId`（`req_…`）贯穿 SSE envelope 与 `messages` 表
- `usage`：latencyMs / searchCalls / toolCallCount / citationCount / completionChars
- 停止：`POST /v1/chat/cancel` 协作式取消；部分文本落库 `status=cancelled`

排障路径：端上气泡 `req=…` → 日志 / DB 同 `requestId` → 复盘 toolTrace。

更多图示见 [DIAGRAMS.md](./DIAGRAMS.md)。

## 8. 与生产的差距（诚实边界）

- 单机 FAISS + SQLite，无多租户 ACL；解析为进程内队列（非 Redis/RQ）
- Embedding / Chat 走云厂商 API，成本与限流依赖外部
- 解析队列至少一次投递：重启可续跑，但不保证恰好一次

这些不妨碍作品集叙事；面试时主动说出「下一步可换 RQ/Redis、按租户隔离索引」更加分。
