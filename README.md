# docs-agent-server

文档问答 **Agent + RAG** 后端（FastAPI）。与 RN 业务分包、动态壳职责分离：

| 仓库 | 职责 |
|------|------|
| `docs-agent-server`（本仓） | Agent 编排、检索、模型调用、SSE |
| `rn-biz-0.86` → `src/docs-agent` | 聊天 / 知识库 / 引用 UI |
| `rn-dynamic-0.86` | 原生宿主与分包加载 |

**当前进度：二期 P2-D5～D6（解析队列 + 重启恢复）完成**；P2-D1～D4 已落地。

## 这个项目解决什么问题

把「上传制度/产品文档 → 多轮提问 → 流式回答带工具轨迹与可点击引用」跑成一条可演示、可讲清原理的链路，而不是套一层聊天 UI。

技术关键词：`RAG` · `Function Calling` · `SSE` · `FAISS` · `Embedding` · `FastAPI` · `通义千问` · `React Native 分包`

## 文档导航

| 文档 | 内容 |
|------|------|
| [docs/PHASE2_SCHEDULE.md](./docs/PHASE2_SCHEDULE.md) | **二期 5～7 天排期**（改写 / 多 KB / 队列+鉴权） |
| [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md) | 分层、状态机、一次请求时序、记忆 vs 检索 |
| [docs/DIAGRAMS.md](./docs/DIAGRAMS.md) | Mermaid 架构图（可导出 PNG） |
| [docs/PROTOCOL.md](./docs/PROTOCOL.md) | HTTP / SSE / cancel 契约 |
| [docs/KNOWN_ISSUES.md](./docs/KNOWN_ISSUES.md) | 已知限制与和产品的差距 |
| [docs/DEMO_SCRIPT.md](./docs/DEMO_SCRIPT.md) | 30 秒 / 3 分钟演示口播与操作清单 |
| [docs/DEMO_VIDEO.md](./docs/DEMO_VIDEO.md) | 短视频镜头表与剪辑要点 |
| [docs/RESUME.md](./docs/RESUME.md) | 简历项目描述初稿（中/英） |
| [docs/INTERVIEW_QA.md](./docs/INTERVIEW_QA.md) | 模拟面试自问自答 15 卡 |
| [eval/FAILURE_CASES.md](./eval/FAILURE_CASES.md) | 评测失败归因 |

## 快速开始

**首次环境（只需一次）：**

```bash
cd docs-agent-server
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# 编辑 .env，填入 LLM_API_KEY（通义千问 DashScope）
# 以及 API_TOKEN（客户端 Bearer；示例为 dev-local-token）
```

**日常启动（推荐 IDE 一点即跑，不必敲命令）：**

1. 用 Cursor / VS Code 打开本仓库，选择解释器为 `.venv`
2. 打开根目录 [`run.py`](./run.py)，点右上角 ▶ **Run Python File**  
   或：侧边栏「运行和调试」→ 选 **Docs Agent Server** → F5
3. 浏览器访问 `http://127.0.0.1:8000/health` 应返回 `{"status":"ok"}`
4. 业务接口须带 `Authorization: Bearer <API_TOKEN>`（与 `.env` 一致）

启动后会在 `data/docs_agent.db` 建五表，并确保默认知识库 `kb_default`。

### 配置说明（`.env`）

| 变量 | 默认 | 说明 |
|------|------|------|
| `LLM_BASE_URL` | DashScope compatible | OpenAI 兼容基址 |
| `LLM_API_KEY` | （必填） | 勿提交仓库 |
| `API_TOKEN` | 空则关闭鉴权 | 非空时业务 API 须 `Authorization: Bearer`；`/health` 放行 |
| `LLM_MODEL` | `qwen-plus` | 对话模型（需支持 tools） |
| `EMBEDDING_MODEL` | `text-embedding-v3` | 全库须同一 embedding；更换需重建索引 |
| `DATABASE_URL` | `sqlite:///./data/docs_agent.db` | SQLAlchemy |
| `UPLOAD_DIR` / `EXTRACTED_DIR` / `FAISS_DIR` | `./data/...` | 本地数据目录（gitignore） |
| `MAX_UPLOAD_MB` | `20` | 上传上限 |
| `CHUNK_SIZE` / `CHUNK_OVERLAP` | `500` / `80` | 切分 |
| `EMBEDDING_BATCH_SIZE` | `10` | 通义单批上限 |

完整字段见 `app/core/config.py`。

## 主路径验收（5 分钟）

```bash
# 1. 探活
curl -s http://127.0.0.1:8000/health

# 2. 上传（示例用评测语料）
curl -s -X POST "http://127.0.0.1:8000/v1/knowledge-bases/kb_default/documents" \
  -F "file=@./eval/corpus/acme_handbook.md"
# 记下返回的 id，轮询直到 status=ready：
# curl -s http://127.0.0.1:8000/v1/documents/<DOC_ID>

# 3. 建会话
curl -s -X POST http://127.0.0.1:8000/v1/sessions \
  -H 'Content-Type: application/json' \
  -d '{"title":"demo"}'
# 记下 session id

# 4. SSE 问答
curl -s -N -X POST "http://127.0.0.1:8000/v1/chat/stream" \
  -H 'Content-Type: application/json' \
  -H 'Accept: text/event-stream' \
  -d '{"sessionId":"<SES_ID>","message":"试用期是多久？"}'
```

期望事件顺序：`message.accepted` → `tool.*`（多为 `search_docs`）→ `message.delta` → `message.completed`（含 `citations` / `toolTrace` / `usage`）。

非流式对比：`POST /v1/chat`。裸模型探活：`POST /debug/chat`。

## 目录结构

```text
app/
  api/         # HTTP：documents / sessions / chat / chunks / health / debug
  agent/       # Tool loop、Prompt、历史窗口、SSE、citations
  rag/         # 提取、切分、Embedding、FAISS、解析流水线
  core/        # 配置、LLM、错误码、request 日志
  db/          # SQLAlchemy 五表
  schemas/     # 响应模型
docs/          # 架构 / 协议 / 已知问题
eval/          # 评测语料、题库、失败 Case
scripts/       # run_eval.py 等
data/          # 本地 DB / 上传 / FAISS（不入库）
```

## 评测（D16）

```bash
.venv/bin/python scripts/run_eval.py
.venv/bin/python scripts/run_eval.py --limit 5   # 调试
```

固定《ACME 员工手册》+ 20 题；报告写入 `eval/results/`（gitignore）。粗判为启发式，失败请人工记入 `eval/FAILURE_CASES.md`。

## 可观测性（D15）与停止生成（D19）

| 能力 | 说明 |
|------|------|
| `X-Request-Id` | 每个 HTTP 请求注入并回写；日志带 `durationMs` |
| Agent `requestId` | SSE envelope / completed / 消息落库 |
| `usage` | latencyMs、searchCalls、searchQueries、rewriteUsed、toolCallCount… |
| 错误码 | `detail: { code, message, retryable }` |
| 停止生成 | `POST /v1/chat/cancel` + 端上 Abort；消息可落 `cancelled` |
| 改写二次检索（P2-D1） | 首次 `search_docs` 空命中时服务端改写 query 再搜；工具卡可见 `rewritten` |
| 多知识库（P2-D2/D3） | KB CRUD；prefs 持久化当前库；Settings；会话按库过滤 |

## RN 联调提示

- 业务代码在 `rn-biz-0.86/src/docs-agent`；API Base：`http://{host}:8000`
- 真机请把 Host 设为电脑局域网 IP（分包 `config.ts` / 调试页）
- 宿主需能转发 Activity Result，否则系统选文件无回调
- 点「停止」会先调 cancel 再断开 SSE

