# AI 短视频 Remotion 模板

服务端默认 `VIDEO_RENDERER=auto`：优先本目录 Remotion，失败回退 FFmpeg 字幕条。

```bash
cd video-renderer
npm install
npx remotion compositions   # 应看到 TalkingCaptions / KineticText / BrandIntro
```

由 `app/video/renderer.py` 调用：

```bash
npx remotion render TalkingCaptions <out.mp4> --props=<storyboard.json>
```
