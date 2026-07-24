# 协议与 API（Protocol）

客户端约定：JSON 字段 **驼峰**（`sessionId`）；错误体统一。

## 1. 错误结构

HTTP 4xx/5xx 的 `detail`（以及部分包装）为：

```json
{
  "code": "NO_READY_DOC",
  "message": "请至少等待一份文档显示为可问答（ready）后再提问",
  "retryable": false,
  "requestId": "http_xxx"
}
```

| code 示例 | 含义 |
|-----------|------|
| `UNSUPPORTED_TYPE` / `TOO_LARGE` | 上传校验 |
| `PDF_ENCRYPTED` / `PDF_TOO_MANY_PAGES` / `PARSE_EMPTY` | 解析边角 |
| `NO_READY_DOC` | 无就绪文档仍提问 |
| `SESSION_NOT_FOUND` / `DOC_NOT_FOUND` / `CHUNK_GONE` | 资源不存在 |
| `LLM_ERROR` / `AGENT_FAILED` | 模型或编排失败（可 `retryable`） |
| `VALIDATION_ERROR` | 请求体校验失败 |

响应头：`X-Request-Id`（HTTP 层）；业务问答另有 Agent `requestId`（`req_…`）。

## 2. 文档与知识库

| Method | Path | 说明 |
|--------|------|------|
| GET | `/v1/knowledge-bases` | 知识库列表（含 documentCount / readyCount） |
| POST | `/v1/knowledge-bases` | 新建 `{ name }` |
| GET | `/v1/knowledge-bases/{kbId}` | 详情 |
| PATCH | `/v1/knowledge-bases/{kbId}` | 重命名 `{ name }` |
| DELETE | `/v1/knowledge-bases/{kbId}` | 删库（级联文档/会话/FAISS；**禁止删 kb_default**） |
| POST | `/v1/knowledge-bases/{kbId}/documents` | multipart 上传文件 |
| POST | `/v1/knowledge-bases/{kbId}/documents/text` | 粘贴文本 `{ title, content }` |
| GET | `/v1/knowledge-bases/{kbId}/documents` | 文档列表 |
| GET | `/v1/documents/{docId}` | 详情 / 状态轮询 |
| GET | `/v1/documents/{docId}/chunks` | 分块预览 |
| DELETE | `/v1/documents/{docId}` | 删除并重建索引 |
| POST | `/v1/documents/{docId}/reparse` | 重新解析 |
| GET | `/v1/chunks/{chunkId}` | Citation 全文 |

会话创建时传 `knowledgeBaseId` 绑定知识库；RN 端「当前知识库」决定上传与新建会话落库。

| Method | Path | 说明 |
|--------|------|------|
| GET | `/v1/meta` | 公开元信息（模型名 / chunk 参数，无 Key） |
| GET | `/v1/prefs` | 客户端偏好（如 `currentKnowledgeBaseId`） |
| PUT | `/v1/prefs` | 更新偏好 |

`GET /v1/sessions?knowledgeBaseId=` 可按库过滤会话列表。

**Document 关键字段：**  
`id`, `knowledgeBaseId`, `title`, `mimeType`, `byteSize`, `status`, `progress`, `stageMessage`, `chunkCount`, `errorCode`, `errorMessage`, `createdAt`, `updatedAt`

**status：** `pending` | `parsing` | `indexing` | `ready` | `failed`

端上建议：上传后每 1～2s 轮询 `GET /v1/documents/{id}`，直到 `ready` / `failed`。

## 3. 会话

| Method | Path | 说明 |
|--------|------|------|
| POST | `/v1/sessions` | `{ knowledgeBaseId?, title? }` |
| GET | `/v1/sessions` | `?limit=` |
| DELETE | `/v1/sessions/{sessionId}` | 级联删消息 |
| GET | `/v1/sessions/{sessionId}/messages` | 历史 |

消息项可含：`citations`, `toolTrace`, `usage`, `requestId`。

## 4. 问答

### 4.1 非流式

`POST /v1/chat`

```json
{ "sessionId": "ses_…", "message": "…", "clientMessageId": "可选" }
```

响应：

```json
{
  "requestId": "req_…",
  "userMessageId": "msg_…",
  "assistantMessageId": "msg_…",
  "answer": "……三个月。[1]",
  "citations": [
    {
      "index": 1,
      "documentId": "doc_…",
      "documentTitle": "…",
      "chunkId": "chk_…",
      "snippet": "…",
      "score": 0.12
    }
  ],
  "toolTrace": [],
  "usage": {
    "latencyMs": 1234,
    "searchCalls": 1,
    "searchQueries": ["试用期是多久"],
    "rewriteUsed": false,
    "toolCallCount": 1,
    "citationCount": 1,
    "completionChars": 80
  }
}
```

### 4.2 流式 SSE（主路径）

`POST /v1/chat/stream`  
`Accept: text/event-stream`

每条事件：

```text
event: message.delta
data: {"v":1,"requestId":"req_…","sessionId":"ses_…","seq":3,"ts":"…","type":"message.delta","payload":{"text":"试用"}}
```

**Envelope 字段：** `v`, `requestId`, `sessionId`, `seq`, `ts`, `type`, `payload`

| type | payload 要点 |
|------|----------------|
| `message.accepted` | `userMessageId`, `clientMessageId` |
| `tool.started` | `toolCallId`, `name`, `args` |
| `tool.completed` | `toolCallId`, `name`, `ok`, `durationMs`, `summary` |
| `message.delta` | `text`（增量拼接） |
| `error` | `code`, `message`, `retryable` |
| `message.completed` | `status`, `answer`, `citations`, `toolTrace`, `usage`, … |

端上状态机建议：

1. `accepted` → 插入用户气泡 + 占位助手气泡  
2. `tool.*` → 更新工具卡  
3. `delta` → 追加文本  
4. `completed` / `error` → 定稿或标失败  
5. 停止：`POST /v1/chat/cancel`（`requestId` 或 `sessionId`）+ 断开 SSE；completed 可能为 `status: "cancelled"`

### 4.3 停止生成

`POST /v1/chat/cancel`

```json
{ "requestId": "req_…", "sessionId": "ses_…" }
```

二者至少其一。响应：`{ ok, requestId, message }`。  
`ok=false` 表示未找到进行中任务（可能已结束）。服务端在 tool / delta 间隙生效；单次阻塞的 LLM HTTP 需返回后才会停下。

## 5. 调试接口

| Method | Path | 用途 |
|--------|------|------|
| GET | `/health` | 探活 |
| POST | `/debug/chat` | 裸模型连通性（不走 RAG） |
| POST | `/debug/search` | 直接打 FAISS（不走 Agent） |

## 6. 引用与二次查询

- SSE / 落库的 `citations` **只带短 snippet**，不带全文。  
- 用户点击 `[n]`：`GET /v1/chunks/{chunkId}` 拉全文 → Citation 页。  
- 这是 **UI 按需加载**，不是 Agent 自动二次追问。
