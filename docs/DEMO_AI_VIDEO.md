# AI 短视频 Demo 脚本（约 3 分钟）

## 前置

1. `docs-agent-server` 已启动；本机或服务器有 `ffmpeg`（或已 `npm i` Remotion）
2. App 登录后进入 **业务 Tab → Home（AI 短视频）**
3. **问答 Tab** 仍可跑制度问答（对照组）

## 口播

> 我们在动态分包宿主上，把首页做成移动端 AI 短视频创作入口：一句话进分镜，服务端 Remotion/模板渲染成片。文档问答 Tab 完全保留，创作助手复用同一套 SSE、取消和评测习惯，但走独立 `/v1/video` 路由。

## 操作清单

| 时间 | 操作 | 看点 |
|------|------|------|
| 0:00 | 打开问答 Tab，问一条制度 | 原链路不变 |
| 0:40 | 切业务 Home，点示例句「开始生成」 | 流式 scene_delta |
| 1:10 | 点「停止」再重新生成 | 可取消 |
| 1:40 | 等待 ready，系统播放器打开 MP4 | CDN `/cdn/video/` |
| 2:10 | Remix 改一镜 → 新版本 | version+1 |
| 2:40 | 创作助手调 plan_storyboard | 工具轨迹 |

## 失败兜底话术

- 若 Remotion 未装：说明 auto 回退 FFmpeg 字幕条，架构仍是服务端渲染  
- 若弱网：进度 SSE 断线后轮询 `GET /jobs/{id}`  
