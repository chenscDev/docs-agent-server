# 简历项目描述初稿（Resume）

按投递习惯改公司名/时间即可。**不要写「调用了千问做了个聊天」**；突出决策与结果。

---

## 中文版（推荐，约 6～8 行）

**文档问答 Agent（个人项目）**｜`2026.xx – 2026.xx`  
*React Native（动态分包） · FastAPI · 通义千问 · FAISS · SQLite · SSE*

- 设计并实现面向私有文档的问答 Agent：解析 / 切分 / Embedding / 向量检索全链路，文档状态机（pending→ready/failed）保证未就绪内容不可检索；解析任务经进程内队列投递，进程重启可 recover 未完成文档。  
- 服务端手写 Function Calling 循环（`search_docs` / `list_documents`），空检索可改写 query 二次检索；经 SSE 区分工具事件与回答增量；客户端 RN 分包渲染工具轨迹、流式气泡与可点击引用。  
- 多知识库产品化（CRUD + 会话绑库）与薄层 Bearer 鉴权；SQLite 存 chunk 正文与会话，FAISS 按 KB 隔离；citations 与 chunk 主键对齐。  
- 统一错误码与 requestId/usage 落库便于排障；固定 20 题评测集回归（P2-D7：**20/20** 粗判通过）。

**可选加一句量化（有则写）：** 评测集 20 题粗判通过率 100%（`eval/results/eval_20260724_110754`）；主路径真机演示稳定（上传→问答→引用）。

---

## 中文版（压缩到 4 条，版面紧时用）

**文档问答 Agent**｜RN 分包 + FastAPI + 千问 + FAISS  

- RAG + 手写 Tool Loop，空检索改写再搜；SSE 下发 tool / delta / completed。  
- 多 KB + Bearer 鉴权；正文与向量分离；引用 chunkId 对齐。  
- 文档状态机 + 解析队列 recover；requestId/usage 可观测；20 题评测 20/20。

---

## English（optional，海外岗 / 双语简历）

**Document Q&A Agent (Personal)**｜`2026.xx – 2026.xx`  
*React Native · FastAPI · Qwen · FAISS · SQLite · SSE*

- Built an end-to-end private-doc Agent: parse → chunk → embed → FAISS retrieval with a document status machine and an in-process parse queue that recovers unfinished jobs after restart.  
- Implemented a server-side tool-calling loop (`search_docs`, `list_documents`) with optional query rewrite on empty retrieval, streaming tool traces vs. answer deltas over SSE to a thin RN client.  
- Productized multi knowledge bases and thin Bearer API auth; kept chunk text in SQLite and vectors in FAISS (per KB); aligned citations to chunk IDs.  
- Added structured error codes, requestId/usage logging, and a 20-question eval set (**20/20** pass on P2 regression).

---

## 技能栏可勾选的关键词

`RAG` `Agent` `Function Calling` / `Tool Calling` `SSE` `FAISS` `Embedding` `FastAPI` `SQLAlchemy` `SQLite` `React Native` `通义千问` / `Qwen`

---

## 面试时主动对齐的「公司问题」（口头，不必全写进简历）

| 对方关心 | 你项目里的对应 |
|----------|----------------|
| 幻觉 | 强制检索、拒答、citations 来自 hits |
| 上下文贵 | 历史 ≤8 条 / ~3k 字；hit 截断 |
| 流式体验 | SSE 先 tool 再 delta；可停止（端上 Abort） |
| 文档更新 | reparse + FAISS 重建；解析队列重启可续跑 |
| 排障 | requestId + toolTrace + usage（含 rewriteUsed） |
| 和生产差距 | 单机 FAISS、进程内队列非分布式、无多租户——并说演进 |

---

## 自检（投递前）

- [ ] 没有出现 API Key、内网地址、客户真实文档名  
- [ ] 「个人项目」时间与仓库现状大致一致  
- [ ] 每条 bullet 能指到代码或文档（`docs/ARCHITECTURE.md` / 评测）  
- [ ] 演示视频或可访问说明已准备（见 `DEMO_VIDEO.md`）
