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
  #stage{position:relative;width:100%;height:100%;display:flex;align-items:center;justify-content:center;background:#0B1220}
  #card{width:min(100%,420px);aspect-ratio:9/16;max-height:100%;position:relative;overflow:hidden;border-radius:12px;background:#0F172A}
  #content{position:absolute;inset:0;transition:opacity .12s linear}
  #topBand,#bottomBand{position:absolute;left:0;right:0;background:#A78BFA;display:none}
  #topBand{top:0;height:20%}
  #bottomBand{bottom:0;height:16px}
  #badge{position:absolute;top:16px;left:16px;color:#fff;font-size:16px;font-weight:700;display:none}
  #brandDot{position:absolute;left:50%;top:18%;width:36px;height:36px;margin-left:-18px;border-radius:18px;background:#34D399;display:none}
  #frameBox{position:absolute;left:10%;right:10%;top:28%;bottom:28%;border:5px solid #34D399;border-radius:22px;display:none}
  #bar{position:absolute;left:0;right:0;bottom:0;height:28%;background:rgba(0,0,0,.88);border-left:10px solid #38BDF8;padding:18px 20px;display:none}
  #logoBadge{position:absolute;width:18%;aspect-ratio:1;object-fit:contain;display:none;z-index:4}
  #headline{color:#fff;font-size:26px;font-weight:700;text-align:center;line-height:1.25;max-width:100%}
  #body{margin-top:12px;color:rgba(255,255,255,.85);font-size:14px;text-align:center;line-height:1.45;max-width:100%}
  #hud{position:absolute;left:10px;right:10px;bottom:10px;display:flex;justify-content:space-between;align-items:center;color:rgba(255,255,255,.55);font-size:11px;pointer-events:none;z-index:5}
  #empty{color:rgba(255,255,255,.5);font-size:14px;text-align:center;padding:24px;position:absolute;inset:0;display:flex;align-items:center;justify-content:center}
</style>
</head>
<body>
<div id="stage">
  <div id="card">
    <div id="content">
      <div id="topBand"></div>
      <div id="bottomBand"></div>
      <div id="badge"></div>
      <div id="brandDot"></div>
      <div id="frameBox"></div>
      <div id="bar">
        <div id="headline"></div>
        <div id="body"></div>
      </div>
      <img id="logoBadge" alt="logo"/>
      <div id="empty">等待分镜数据…</div>
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

  function hideAllChrome() {
    ['topBand','bottomBand','badge','brandDot','frameBox','bar','empty','logoBadge'].forEach(function (id) {
      document.getElementById(id).style.display = 'none';
    });
  }

  function placeLogo() {
    var logo = document.getElementById('logoBadge');
    var src = (state.props.logoUrl || '').trim();
    if (!src) return;
    var pos = state.props.logoPosition || 'top-right';
    logo.src = src;
    logo.style.display = 'block';
    logo.style.top = 'auto';
    logo.style.bottom = 'auto';
    logo.style.left = 'auto';
    logo.style.right = 'auto';
    var m = '14px';
    if (pos === 'top-left') { logo.style.top = m; logo.style.left = m; }
    else if (pos === 'bottom-left') { logo.style.bottom = m; logo.style.left = m; }
    else if (pos === 'bottom-right') { logo.style.bottom = m; logo.style.right = m; }
    else { logo.style.top = m; logo.style.right = m; }
  }

  function render() {
    var hit = sceneAtFrame(state.frame);
    var headline = document.getElementById('headline');
    var body = document.getElementById('body');
    var content = document.getElementById('content');
    var card = document.getElementById('card');
    var bar = document.getElementById('bar');
    hideAllChrome();
    if (!hit) {
      document.getElementById('empty').style.display = 'flex';
      document.getElementById('sceneLabel').textContent = '无分镜';
      document.getElementById('frameLabel').textContent = state.frame + 'f';
      return;
    }
    var sc = hit.scene;
    var tid = (state.props.templateId || 'talking-captions');
    var accent = sc.accentColor || '#38BDF8';
    card.style.background = sc.bgColor || '#0F172A';
    headline.textContent = sc.headline || '';
    body.textContent = sc.body || '';
    body.style.display = sc.body ? 'block' : 'none';

    if (tid === 'kinetic-text') {
      var top = document.getElementById('topBand');
      var bottom = document.getElementById('bottomBand');
      var badge = document.getElementById('badge');
      top.style.display = 'block';
      top.style.background = accent;
      bottom.style.display = 'block';
      bottom.style.background = accent;
      badge.style.display = 'block';
      badge.textContent = String(Number(sc.index) + 1).padStart(2, '0');
      bar.style.display = 'flex';
      bar.style.flexDirection = 'column';
      bar.style.alignItems = 'center';
      bar.style.justifyContent = 'center';
      bar.style.left = '8%';
      bar.style.right = '8%';
      bar.style.top = '32%';
      bar.style.bottom = '28%';
      bar.style.height = 'auto';
      bar.style.background = 'rgba(0,0,0,0.55)';
      bar.style.borderLeft = '0';
      bar.style.borderRadius = '12px';
      bar.style.transform = 'none';
      headline.style.fontSize = '28px';
      headline.style.textAlign = 'center';
      headline.style.letterSpacing = '1px';
      body.style.textAlign = 'center';
    } else if (tid === 'brand-intro') {
      var dot = document.getElementById('brandDot');
      var frame = document.getElementById('frameBox');
      dot.style.display = 'block';
      dot.style.background = accent;
      frame.style.display = 'block';
      frame.style.borderColor = accent;
      bar.style.display = 'flex';
      bar.style.flexDirection = 'column';
      bar.style.alignItems = 'center';
      bar.style.justifyContent = 'center';
      bar.style.left = '12%';
      bar.style.right = '12%';
      bar.style.top = '34%';
      bar.style.bottom = '34%';
      bar.style.height = 'auto';
      bar.style.background = 'transparent';
      bar.style.borderLeft = '0';
      bar.style.transform = 'none';
      headline.style.fontSize = Number(sc.index) === 0 ? '26px' : '24px';
      headline.style.textAlign = 'center';
      headline.style.letterSpacing = '1.2px';
      body.style.textAlign = 'center';
    } else {
      var capPos = state.props.captionPosition || 'bottom';
      bar.style.display = 'block';
      bar.style.left = '0';
      bar.style.right = '0';
      bar.style.height = '36%';
      bar.style.top = 'auto';
      bar.style.bottom = 'auto';
      if (capPos === 'top') {
        bar.style.top = '0';
      } else if (capPos === 'center') {
        bar.style.top = '34%';
      } else {
        bar.style.bottom = '0';
      }
      bar.style.background = 'rgba(0,0,0,0.88)';
      bar.style.borderLeft = '10px solid ' + accent;
      bar.style.borderRadius = '0';
      bar.style.overflow = 'hidden';
      headline.style.fontSize = '20px';
      headline.style.lineHeight = '1.35';
      headline.style.wordBreak = 'break-word';
      headline.style.textAlign = 'left';
      headline.style.letterSpacing = '0.5px';
      body.style.fontSize = '14px';
      body.style.lineHeight = '1.4';
      body.style.wordBreak = 'break-word';
      body.style.textAlign = 'left';
    }

    placeLogo();

    var fadeFrames = Math.max(1, Math.round(fps() * (
      tid === 'brand-intro' ? (Number(sc.index) === 0 ? 0.55 : 0.4) :
      tid === 'kinetic-text' ? 0.12 : 0.28
    )));
    var local = hit.local;
    var opacity = Math.min(1, local / fadeFrames);
    var transform = 'none';
    if (tid === 'kinetic-text') {
      var popT = Math.min(1, local / Math.max(1, Math.round(fps() * 0.18)));
      var scale = 0.78 + 0.22 * popT;
      var rise = 36 * (1 - popT);
      transform = 'translateY(' + rise + 'px) scale(' + scale + ')';
    } else if (tid === 'brand-intro') {
      var isFirst = Number(sc.index) === 0;
      var isLast = Number(sc.index) === (scenes().length - 1);
      var enterT = Math.min(1, local / fadeFrames);
      var scaleB = (isFirst ? 0.72 : 0.92) + (1 - (isFirst ? 0.72 : 0.92)) * enterT;
      if (isLast) {
        var sceneDur = Math.max(1, Math.round((Number(sc.durationSec) || 3) * fps()));
        var exitStart = Math.floor(sceneDur * 0.62);
        if (local >= exitStart) {
          var exitT = Math.min(1, (local - exitStart) / Math.max(1, sceneDur - exitStart));
          opacity = opacity * (1 - 0.85 * exitT);
          scaleB = scaleB * (1 - 0.1 * exitT);
        }
      }
      transform = 'scale(' + scaleB + ')';
    } else {
      // talking-captions：字幕条滑入感（用 translateY 近似）
      var slideT = Math.min(1, local / fadeFrames);
      var slide = (1 - slideT) * 28;
      var capMotion = state.props.captionPosition || 'bottom';
      if (capMotion === 'top') slide = -slide;
      else if (capMotion === 'center') slide = slide * 0.4;
      bar.style.transform = 'translateY(' + slide + 'px)';
    }
    content.style.opacity = String(opacity);
    content.style.transform = transform;
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
