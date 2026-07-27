# 协议与 API（Protocol）

客户端约定：JSON 字段 **驼峰**（`sessionId`）；错误体统一。

## 0. 鉴权（P2-D4 / P3-D13）

| 项 | 约定 |
|----|------|
| 有效 Token | `API_TOKEN` ∪ `API_TOKENS`（逗号/分号/空白分隔） |
| 作废 | `API_TOKENS_REVOKED`；或 `data/api_tokens_revoked.local`（每行一个，**改文件热生效**） |
| 请求头 | `Authorization: Bearer <任一有效 token>` |
| 放行 | 仅 `GET /health`（探活） |
| 未配置任何有效 Token | 不强制鉴权（便于首次起服） |
| 未带 / 错误 / 已作废 | `401`，`detail.code` = `AUTH_REQUIRED` / `AUTH_INVALID` |

LLM Key（`LLM_API_KEY`）只留在服务端，不下发到端上。RN 在 `config` / Settings 配置客户端 Token，勿提交真实生产 Token。

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
| `AUTH_REQUIRED` / `AUTH_INVALID` | 缺少或错误的 Bearer Token |
| `UNSUPPORTED_TYPE` / `TOO_LARGE` | 上传校验 |
| `PDF_ENCRYPTED` / `PDF_TOO_MANY_PAGES` / `PARSE_EMPTY` | 解析边角 |
| `OCR_TOO_MANY_PAGES` / `OCR_FAILED` | 扫描 PDF OCR 超页或云识别失败（可 reparse） |
| `PARSE_TIMEOUT` | 解析进度 SSE 等待超时（端上应回退轮询） |
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
| DELETE | `/v1/documents/{docId}` | 删除并增量更新索引（失败则全量 rebuild） |
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

解析调度（P2-D5～D6）：上传 / reparse 后 `status=pending` 落库并 `enqueue_parse`；进程内串行执行。启动时扫描 `pending|parsing|indexing` 重置续跑（至少一次）。失败可 `POST .../reparse`。

**P3-D5 OCR：** PDF 文本层有效字符 `< OCR_MIN_CHARS` 时，若 `OCR_ENABLED`，逐页渲染并调用 `OCR_MODEL`（默认 `qwen-vl-ocr-latest`）；`stageMessage` 形如「OCR 识别中 3/12…」。超 `OCR_MAX_PAGES` → `OCR_TOO_MANY_PAGES`。

**P3-D6 解析进度 SSE：** `GET /v1/documents/{docId}/events`（`text/event-stream`）。事件：`document.snapshot` → `document.progress*` → `document.completed`（或 `error`）。payload 与 `DocumentOut` 同形（驼峰）。服务端内部仍短间隔读库；端上优先订阅，断线/错误回退 `GET /documents/{id}` 轮询。

端上建议：上传后优先订阅 events；无法建立流时每 1～2s 轮询，直到 `ready` / `failed`。

## 3. 会话

| Method | Path | 说明 |
|--------|------|------|
| POST | `/v1/sessions` | `{ knowledgeBaseId?, title? }` |
| GET | `/v1/sessions` | `?limit=` / `?knowledgeBaseId=` |
| DELETE | `/v1/sessions/{sessionId}` | 级联删消息与反馈 |
| GET | `/v1/sessions/{sessionId}/messages` | 历史（可含 `feedback`） |

消息项可含：`citations`, `toolTrace`, `usage`, `requestId`, `feedback`。

### 3.1 回答反馈（P3-D8）

| Method | Path | 说明 |
|--------|------|------|
| POST | `/v1/messages/{messageId}/feedback` | `{ rating: "up"\|"down", comment? }` 点赞/点踩（可改评） |
| DELETE | `/v1/messages/{messageId}/feedback` | 清除反馈 |
| GET | `/v1/feedbacks?rating=down` | 反馈列表（人工审） |
| POST | `/v1/feedbacks/export-down` | 未导出点踩写入 `eval/feedback_candidates.jsonl`，并追加 `FAILURE_CASES` 候选段 |

仅助手消息可反馈。

## 4. 问答

### 4.1 非流式

`POST /v1/chat`

与 `POST /v1/chat/stream` 共用同一 Agent 编排（`iter_agent_sse`，`stream_text=false` 一次性聚合成答）。字段与 SSE `message.completed` 对齐，便于评测脚本走非流式。

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
    "followupRewriteUsed": false,
    "rewriteReasons": [],
    "rerankUsed": true,
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

`usage` 改写可区分（P2 / P3-D7）：

| 字段 | 含义 |
|------|------|
| `rewriteUsed` | 空命中后二次改写再搜（`empty_recall`） |
| `followupRewriteUsed` | 多轮指代改写（`followup`） |
| `rewriteReasons` | 如 `["followup"]` / `["empty_recall"]` / 二者皆有 |
| `searchQueries` | 实际检索句列表 |

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
`ok=false` 表示未找到进行中任务（可能已结束）。  
服务端：set 取消标记 + **close 当前 LLM 的 httpx 连接**（P3-D15），并在 tool / delta 间隙检查；落库 `status=cancelled` 与已推送部分文本。

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
