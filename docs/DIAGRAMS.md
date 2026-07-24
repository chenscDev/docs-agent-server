# 架构图（Diagrams）

可用支持 Mermaid 的编辑器预览（VS Code / GitHub / Notion）。导出 PNG：在预览里右键复制图，或用 [mermaid.live](https://mermaid.live) 导出。

## 1. 系统上下文

```mermaid
flowchart LR
  User[用户]
  RN[RN 分包 docs-agent]
  API[docs-agent-server]
  Qwen[通义千问 Chat+Embed]
  SQL[(SQLite)]
  Faiss[(FAISS)]

  User --> RN
  RN -->|HTTP / SSE| API
  API --> Qwen
  API --> SQL
  API --> Faiss
```

## 2. 文档解析状态机

```mermaid
stateDiagram-v2
  [*] --> pending: 上传 / reparse
  pending --> parsing: parse_queue 入队
  parsing --> indexing: 切分完成
  indexing --> ready: FAISS 重建成功
  parsing --> failed: 提取失败
  indexing --> failed: 索引失败
  failed --> pending: reparse
  ready --> pending: reparse
  parsing --> pending: 进程重启 recover
  indexing --> pending: 进程重启 recover
```

## 3. 一次 SSE 问答

```mermaid
sequenceDiagram
  participant RN as RN Client
  participant API as FastAPI
  participant Agent as Agent Loop
  participant LLM as Qwen
  participant RAG as FAISS+SQLite

  RN->>API: POST /v1/chat/stream
  API->>Agent: iter_agent_sse
  Agent-->>RN: message.accepted
  loop Tool rounds
    Agent->>LLM: chat_with_tools
    LLM-->>Agent: tool_calls?
    alt search_docs
      Agent-->>RN: tool.started
      Agent->>RAG: search
      Agent-->>RN: tool.completed
    end
  end
  Agent->>LLM: final content / stream
  Agent-->>RN: message.delta*
  Agent-->>RN: message.completed
  Note over RN: 用户点停止
  RN->>API: POST /v1/chat/cancel
  RN->>API: XHR abort
```

## 4. 数据分工（单一事实来源）

```mermaid
flowchart TB
  Doc[原始文件 uploads]
  Doc --> Extract[提取文本]
  Extract --> Chunks[chunks 表 · 正文]
  Chunks --> Emb[Embedding]
  Emb --> Vec[FAISS 向量 + id_map]
  Vec -->|hit chunkId| Chunks
  Chunks -->|GET /chunks| Citation[Citation 页全文]
```

## 5. 记忆 vs 检索

```mermaid
flowchart LR
  Hist[历史窗口 ≤8 条 / ~3k 字]
  Search[本轮 search_docs hits]
  Hist --> Prompt[拼进 messages]
  Search --> Prompt
  Prompt --> LLM[模型]
  Note1[上轮 hits 不自动带入下一轮]
```

导出建议文件名：`docs-agent-arch-context.png`、`docs-agent-sse-seq.png`（放作品集或面试投屏）。
