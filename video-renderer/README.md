# Remotion 模板（本机 CLI + 可选 Lambda 渲染跳板）

服务端 `VIDEO_RENDERER=auto` 默认顺序：

1. **本机** `npx remotion render`（低并发 / 限制 Node 堆，稳住 2G 演示机）
2. **（可选）Remotion Lambda**：只替换「渲这一跳」，Job / SSE / TTS / RN 不变
3. **FFmpeg** 字幕条兜底；Remix 局部重渲仍优先 FFmpeg clip 复用

## 本机

```bash
cd video-renderer
npm install
npx remotion compositions   # TalkingCaptions / KineticText / BrandIntro
npx remotion render TalkingCaptions ../data/video_out/demo.mp4 --props=./demo.props.json
```

由 `app/video/renderer.py` 调用；关键环境变量见仓库根 `.env.example`。

## Lambda（可选）

1. 按 [Remotion Lambda 文档](https://www.remotion.dev/docs/lambda) 部署 function + site，得到：
   - `REMOTION_LAMBDA_REGION`
   - `REMOTION_LAMBDA_FUNCTION_NAME`
   - `REMOTION_LAMBDA_SERVE_URL`
2. 配置 AWS 密钥（`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` 或 Remotion 前缀变量）
3. `.env`：

```bash
REMOTION_LAMBDA_ENABLED=true
# 演示机内存紧：优先云端渲染
REMOTION_PREFER_LAMBDA=true
# 或强制：VIDEO_RENDERER=lambda
```

4. 依赖：`npm i`（含 `@remotion/lambda`）后，Python 会调：

```bash
node scripts/render-on-lambda.mjs --composition TalkingCaptions --props x.json --out out.mp4
```

未配齐 Lambda 时自动跳过，不影响本机 Remotion / FFmpeg。
