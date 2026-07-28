# AI 短视频创作（与文档问答并存）

## 目标

在 **不改动** 制度问答主路径（`/v1/chat`、`docs-agent` Tab）的前提下，用 `home` 分包提供「一句话 → 分镜流式 → Remotion/FFmpeg 渲染 → 预览分享」。

## 架构

```text
RN home 分包  --SSE-->  /v1/video/plan/stream | /creative/stream
              --HTTP--> /v1/video/jobs
docs-agent-server
  app/video/          分镜 Schema / 规划 / 队列 / 渲染
  video-renderer/     Remotion 模板（可选）
  data/video_out/     MP4 + 封面/分镜 JPG → 挂载 /cdn/video/（免鉴权）
  TTS：DashScope CosyVoice（失败自动静音）
  Nginx：/cdn/video/ 反代到 uvicorn，避免双目录 404
docs-agent 分包       问答不变；可提示用户切到业务 Home 创作
```

## 状态机

`pending → scripting → rendering → ready | failed | cancelled`

与文档解析队列相同：SQLite 持久化 + 进程内串行 worker + 启动恢复。

## 渲染策略

`VIDEO_RENDERER=auto`（默认）：

1. 尝试 `npx remotion render`（`video-renderer/`）
2. 回退 FFmpeg 色块 + drawtext 字幕条
3. 均失败则任务 `failed`（需安装 ffmpeg 或 Remotion）

## 关键 API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/v1/video/templates` | 模板列表 |
| POST | `/v1/video/plan/stream` | 流式分镜 |
| POST | `/v1/video/creative/stream` | 创作 Agent（plan_storyboard / refine_scene） |
| POST | `/v1/video/jobs` | 创建并可选入队渲染 |
| GET | `/v1/video/jobs/{id}/events` | 进度 SSE（可轮询降级） |
| POST | `/v1/video/jobs/{id}/remix` | 局部改镜新版本 |
| POST | `/v1/video/cancel` | 取消任务/流 |

## 评测

```bash
python scripts/eval_video_storyboard.py
```

固定 10 条 prompt → Schema 校验。

## 面试口述要点

1. Remotion 在 **服务端**，RN 只做生成态 UI 与预览分享  
2. 问答是 RAG；创作是 **有界 Agent + 结构化 Storyboard**  
3. 取消链路复用 cancel registry，并延伸到渲染队列  
4. 与 `/v1/chat` **路由隔离**，避免制度问答串戏  
