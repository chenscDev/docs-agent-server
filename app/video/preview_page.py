"""Remotion 风格分镜预览页：供 RN WebView 嵌入，走 postMessage 协议。"""

from __future__ import annotations


def build_preview_html() -> str:
    """返回自包含预览 HTML（与 TalkingCaptions 视觉一致，协议对齐 @remotion/player）。"""
    return """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no"/>
<title>分镜预览</title>
<style>
  *{box-sizing:border-box}
  html,body{margin:0;height:100%;background:#0B1220;overflow:hidden;font-family:-apple-system,BlinkMacSystemFont,sans-serif}
  #stage{position:relative;width:100%;height:100%;display:flex;align-items:center;justify-content:center;background:#0F172A}
  #card{width:min(100%,420px);aspect-ratio:9/16;max-height:100%;position:relative;overflow:hidden;border-radius:12px;background:#0F172A}
  #content{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:36px 28px;transition:opacity .12s linear}
  #headline{color:#fff;font-size:28px;font-weight:700;text-align:center;line-height:1.25;border-left:5px solid #38BDF8;padding-left:16px;max-width:100%}
  #body{margin-top:18px;color:rgba(255,255,255,.82);font-size:15px;text-align:center;line-height:1.45;max-width:100%}
  #hud{position:absolute;left:10px;right:10px;bottom:10px;display:flex;justify-content:space-between;align-items:center;color:rgba(255,255,255,.55);font-size:11px;pointer-events:none}
  #empty{color:rgba(255,255,255,.5);font-size:14px;text-align:center;padding:24px}
</style>
</head>
<body>
<div id="stage">
  <div id="card">
    <div id="content">
      <div id="empty">等待分镜数据…</div>
      <div id="headline" style="display:none"></div>
      <div id="body" style="display:none"></div>
    </div>
    <div id="hud"><span id="sceneLabel">—</span><span id="frameLabel">0f</span></div>
  </div>
</div>
<script>
(function () {
  var state = {
    props: { title: '', templateId: 'talking-captions', fps: 30, scenes: [] },
    frame: 0,
    playing: false,
    raf: 0,
    lastTs: 0
  };

  function postToRn(msg) {
    try {
      if (window.ReactNativeWebView && window.ReactNativeWebView.postMessage) {
        window.ReactNativeWebView.postMessage(JSON.stringify(msg));
      }
    } catch (e) {}
  }

  function fps() {
    var f = Number(state.props.fps);
    return f > 0 ? f : 30;
  }

  function scenes() {
    return Array.isArray(state.props.scenes) ? state.props.scenes : [];
  }

  function totalFrames() {
    var f = fps();
    var t = 0;
    scenes().forEach(function (sc) {
      t += Math.max(1, Math.round(Number(sc.durationSec || 3) * f));
    });
    return Math.max(1, t);
  }

  function sceneAtFrame(frame) {
    var f = fps();
    var cursor = 0;
    var list = scenes();
    for (var i = 0; i < list.length; i++) {
      var dur = Math.max(1, Math.round(Number(list[i].durationSec || 3) * f));
      if (frame < cursor + dur) {
        return { scene: list[i], local: frame - cursor, index: i, start: cursor, dur: dur };
      }
      cursor += dur;
    }
    if (list.length) {
      var last = list[list.length - 1];
      var lastDur = Math.max(1, Math.round(Number(last.durationSec || 3) * f));
      return { scene: last, local: lastDur - 1, index: list.length - 1, start: cursor - lastDur, dur: lastDur };
    }
    return null;
  }

  function render() {
    var hit = sceneAtFrame(state.frame);
    var empty = document.getElementById('empty');
    var headline = document.getElementById('headline');
    var body = document.getElementById('body');
    var content = document.getElementById('content');
    var card = document.getElementById('card');
    if (!hit) {
      empty.style.display = 'block';
      headline.style.display = 'none';
      body.style.display = 'none';
      document.getElementById('sceneLabel').textContent = '无分镜';
      document.getElementById('frameLabel').textContent = state.frame + 'f';
      return;
    }
    empty.style.display = 'none';
    headline.style.display = 'block';
    var sc = hit.scene;
    card.style.background = sc.bgColor || '#0F172A';
    headline.style.borderLeftColor = sc.accentColor || '#38BDF8';
    headline.textContent = sc.headline || '';
    if (sc.body) {
      body.style.display = 'block';
      body.textContent = sc.body;
    } else {
      body.style.display = 'none';
      body.textContent = '';
    }
    var fadeFrames = Math.max(1, Math.round(fps() * 0.3));
    var opacity = Math.min(1, hit.local / fadeFrames);
    content.style.opacity = String(opacity);
    document.getElementById('sceneLabel').textContent =
      '#' + (Number(sc.index) + 1) + ' · ' + (state.props.title || '预览');
    document.getElementById('frameLabel').textContent =
      state.frame + ' / ' + totalFrames() + 'f';
  }

  function seekTo(frame) {
    var max = totalFrames() - 1;
    state.frame = Math.max(0, Math.min(max, Math.round(Number(frame) || 0)));
    render();
    postToRn({ type: 'preview/frame', frame: state.frame });
  }

  function applyUpdate(msg) {
    if (msg.props && typeof msg.props === 'object') {
      state.props = Object.assign({}, state.props, msg.props);
      if (!Array.isArray(state.props.scenes)) state.props.scenes = [];
    }
    if (typeof msg.frame === 'number') {
      seekTo(msg.frame);
    } else {
      render();
    }
  }

  function handle(raw) {
    var msg = raw;
    if (typeof raw === 'string') {
      try { msg = JSON.parse(raw); } catch (e) { return; }
    }
    if (!msg || typeof msg !== 'object') return;
    if (msg.type === 'preview/update') {
      applyUpdate(msg);
      return;
    }
    if (msg.type === 'preview/seek') {
      seekTo(msg.frame);
      return;
    }
    if (msg.type === 'preview/play') {
      state.playing = true;
      state.lastTs = 0;
      if (!state.raf) tick();
      return;
    }
    if (msg.type === 'preview/pause') {
      state.playing = false;
      return;
    }
  }

  function tick(ts) {
    state.raf = requestAnimationFrame(tick);
    if (!state.playing) return;
    if (!state.lastTs) { state.lastTs = ts; return; }
    var dt = (ts - state.lastTs) / 1000;
    state.lastTs = ts;
    var next = state.frame + dt * fps();
    if (next >= totalFrames()) {
      state.frame = totalFrames() - 1;
      state.playing = false;
      render();
      postToRn({ type: 'preview/frame', frame: state.frame });
      return;
    }
    state.frame = next;
    render();
  }

  window.__RN_PREVIEW__ = { handle: handle, seekTo: seekTo };
  window.addEventListener('message', function (ev) {
    handle(ev.data);
  });
  document.addEventListener('message', function (ev) {
    handle(ev.data);
  });

  render();
  postToRn({ type: 'preview/ready' });
})();
</script>
</body>
</html>
"""
