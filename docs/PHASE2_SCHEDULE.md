# 二期排期（约 5～7 个工作日）

一期（D1～D20）已锁定：**上传 → ready → Agent SSE → 引用 → 评测 → 文档叙事**。  
二期只做三块，按「效果 → 业务形态 → 工程可信」排序。

| 优先级 | 主题 | 产出 |
|--------|------|------|
| P0 | 查询改写二次检索 | 空/弱召回时改写再搜，usage 可观测 |
| P0 | 多知识库产品化 | KB CRUD + 会话绑 KB + 端上切换 |
| P1 | 解析队列 + 简单鉴权 | 重启不丢任务；Token 鉴权 |

日均按 **6～8h**；每天留 **1h** 复盘（能讲清为什么，而不是只合并代码）。

---

## P2-D1｜查询改写二次检索（今天）

**目标：** 首轮 `search_docs` 无命中时，强制/引导改写 query 再搜（仍 ≤2 次）；SSE / usage 能看出「改写过」。

**做：**
- 空命中时加强 tool `hint` + Agent 侧系统催促改写
- `usage` 增加 `searchQueries`、`rewriteUsed`
- README / 协议补一句验收

**验收：**
1. 用「拐弯抹角」问法问手册里有、但字面不重合的问题，第二次 search 的 query 与第一次不同  
2. `message.completed.usage.rewriteUsed === true`（或 `searchQueries.length === 2`）  
3. 仍能拒答真正不存在的内容（如产假）

**当天能讲：** 为什么不能无限重搜；改写与「上轮 hits 不带入历史」的区别。

---

## P2-D2～D3｜多知识库产品化

**目标：** 不再只有隐藏的 `kb_default`，可建多个 KB，会话/上传落到指定库。

**做：**
- API：`POST/GET/PATCH/DELETE /v1/knowledge-bases`（名称、文档数）  
- 上传 / 会话已有 `knowledgeBaseId` 处产品化默认与校验  
- RN：知识库列表切换、新建、当前 KB 指示  
- 删除 KB：清文档 + FAISS 目录（谨慎二次确认）

**验收：** 两个 KB 各传不同文档；会话 A/B 问答互不串库。

**当天能讲：** 按 KB 隔离索引 vs 多租户 ACL 的差别。

---

## P2-D4｜鉴权（薄一层）

**目标：** 无 Token 不能调业务 API；Key 仍只在服务端。

**做：**
- `.env`：`API_TOKEN=...`  
- 中间件：校验 `Authorization: Bearer ...`（`/health` 可放行）  
- RN：`config` / 请求头带 Token（本地调试写死或调试页配置，勿提交真 Token）

**验收：** 无头 401；有头主路径仍通。

---

## P2-D5～D6｜解析队列

**目标：** 进程重启不丢「解析中」任务（至少可恢复 pending）。

**做：**
- 引入轻量队列（推荐 **RQ + Redis**，或先用「启动时扫 pending/parsing 续跑」）  
- `run_parse_job` 从 BackgroundTasks 改为入队  
- 文档状态仍走原状态机；失败可 reparse

**验收：** 上传后杀 uvicorn 再启，文档能继续到 ready（或明确 failed 可点重试）。

**当天能讲：** 为什么 BackgroundTasks 不够；至少一次投递语义。

---

## P2-D7｜联调、评测回归、叙事（缓冲）

- 跑 `scripts/run_eval.py`，失败记 `FAILURE_CASES`  
- 更新 `ARCHITECTURE` / `RESUME` 二期 bullet  
- 演示脚本加一句：「空检索会改写再搜」  
- 可选：Settings 页只读展示当前模型名（不做热切换也行）

---

## 明确仍不做（避免二期膨胀）

- OCR / docx / 表格专问  
- 自研 Rerank 大模型（可后置「可选 cross-encoder」）  
- 多租户权限中台、微调 LoRA、MCP 全家桶  
- 掐断厂商 LLM TCP  

---

## 进度标记

| 天 | 状态 |
|----|------|
| P2-D1 改写二次检索 | **已完成** |
| P2-D2～D3 多知识库 | **已完成**（CRUD + 切换 + prefs 持久化 + Settings） |
| P2-D4 鉴权 | 待开始 |
| P2-D5～D6 队列 | 待开始 |
| P2-D7 回归叙事 | 待开始 |
