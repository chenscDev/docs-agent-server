# BGM 内置短音频

由 `scripts/gen_bgm_wav.py`（或同目录生成逻辑）产出的轻量氛围轨，供成片混音。

| 文件 | 曲目 id | 用途 |
|------|---------|------|
| soft-pink.wav | soft-pink | 口播柔和铺底 |
| bright-pulse.wav | bright-pulse | 快闪轻快 |
| warm-pad.wav | warm-pad | 品牌暖垫 |

缺文件时渲染会回退到 lavfi 合成。可替换为任意循环友好的 wav/mp3（同文件名即可）。
