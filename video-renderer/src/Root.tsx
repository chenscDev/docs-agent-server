import React from 'react';
import {
  AbsoluteFill,
  Img,
  OffthreadVideo,
  Sequence,
  interpolate,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';

export type Scene = {
  id: string;
  index: number;
  durationSec: number;
  headline: string;
  body?: string;
  bgColor?: string;
  accentColor?: string;
  imageUrl?: string;
  /** 短视频底图，优先于 imageUrl */
  videoUrl?: string;
  /** 从原视频第几秒开始播（连续分镜错开） */
  videoTrimStartSec?: number;
};

export type StoryboardProps = {
  title?: string;
  templateId?: string;
  scenes?: Scene[];
  fps?: number;
  logoUrl?: string;
  logoPosition?: 'top-right' | 'top-left' | 'bottom-right' | 'bottom-left';
  /** 口播字幕条位置 */
  captionPosition?: 'bottom' | 'top' | 'center';
};

type SceneCompProps = {
  scene: Scene;
  captionPosition?: StoryboardProps['captionPosition'];
  sceneCount: number;
  isFirst: boolean;
  isLast: boolean;
};

function SceneBackground({scene}: {scene: Scene}) {
  const bg = scene.bgColor || '#0F172A';
  const videoSrc = (scene.videoUrl || '').trim();
  const imageSrc = (scene.imageUrl || '').trim();
  const {fps} = useVideoConfig();
  if (videoSrc) {
    const startFrom = Math.max(
      0,
      Math.round((scene.videoTrimStartSec || 0) * fps),
    );
    return (
      <AbsoluteFill style={{backgroundColor: bg}}>
        <OffthreadVideo
          src={videoSrc}
          muted
          startFrom={startFrom}
          style={{width: '100%', height: '100%', objectFit: 'cover'}}
        />
        <AbsoluteFill style={{backgroundColor: 'rgba(0,0,0,0.28)'}} />
      </AbsoluteFill>
    );
  }
  if (imageSrc) {
    return (
      <AbsoluteFill style={{backgroundColor: bg}}>
        <Img
          src={imageSrc}
          style={{width: '100%', height: '100%', objectFit: 'cover'}}
        />
        <AbsoluteFill style={{backgroundColor: 'rgba(0,0,0,0.28)'}} />
      </AbsoluteFill>
    );
  }
  return <AbsoluteFill style={{backgroundColor: bg}} />;
}

function LogoBadge({
  logoUrl,
  logoPosition = 'top-right',
}: {
  logoUrl?: string;
  logoPosition?: StoryboardProps['logoPosition'];
}) {
  const src = (logoUrl || '').trim();
  if (!src) {
    return null;
  }
  const {width} = useVideoConfig();
  const size = Math.round(width * 0.18);
  const margin = 28;
  const style: React.CSSProperties = {
    position: 'absolute',
    width: size,
    height: size,
    objectFit: 'contain',
  };
  if (logoPosition === 'top-left') {
    style.top = margin;
    style.left = margin;
  } else if (logoPosition === 'bottom-left') {
    style.bottom = margin;
    style.left = margin;
  } else if (logoPosition === 'bottom-right') {
    style.bottom = margin;
    style.right = margin;
  } else {
    style.top = margin;
    style.right = margin;
  }
  return <Img src={src} style={style} />;
}

/**
 * 口播字幕条：字幕条自下/上滑入 + 左侧强调色，偏解说节奏
 */
const TalkingCaptionsScene: React.FC<SceneCompProps> = ({
  scene,
  captionPosition = 'bottom',
}) => {
  const frame = useCurrentFrame();
  const {fps, height} = useVideoConfig();
  const accent = scene.accentColor || '#38BDF8';
  const barH = Math.round(height * 0.36);
  const slideIn = Math.max(1, Math.round(fps * 0.28));
  const opacity = interpolate(frame, [0, slideIn], [0, 1], {
    extrapolateRight: 'clamp',
  });
  const slide =
    captionPosition === 'top'
      ? interpolate(frame, [0, slideIn], [-barH * 0.45, 0], {
          extrapolateRight: 'clamp',
        })
      : captionPosition === 'center'
        ? interpolate(frame, [0, slideIn], [24, 0], {
            extrapolateRight: 'clamp',
          })
        : interpolate(frame, [0, slideIn], [barH * 0.45, 0], {
            extrapolateRight: 'clamp',
          });
  const bodyDelay = Math.round(fps * 0.12);
  const bodyOpacity = interpolate(
    frame,
    [bodyDelay, bodyDelay + slideIn],
    [0, 1],
    {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'},
  );
  const barPos: React.CSSProperties =
    captionPosition === 'top'
      ? {top: 0, transform: `translateY(${slide}px)`}
      : captionPosition === 'center'
        ? {
            top: Math.round(height * 0.34),
            transform: `translateY(${slide}px)`,
          }
        : {bottom: 0, transform: `translateY(${slide}px)`};
  return (
    <AbsoluteFill>
      <SceneBackground scene={scene} />
      <div
        style={{
          position: 'absolute',
          left: 0,
          right: 0,
          height: barH,
          ...barPos,
          backgroundColor: 'rgba(0,0,0,0.88)',
          borderLeft: `14px solid ${accent}`,
          padding: '24px 32px',
          opacity,
          boxSizing: 'border-box',
          display: 'flex',
          flexDirection: 'column',
          justifyContent: 'center',
          overflow: 'hidden',
        }}
      >
        <div
          style={{
            color: '#fff',
            fontSize: 34,
            fontWeight: 700,
            lineHeight: 1.35,
            letterSpacing: 0.5,
            wordBreak: 'break-word',
            overflowWrap: 'anywhere',
            display: '-webkit-box',
            WebkitLineClamp: 2,
            WebkitBoxOrient: 'vertical',
            overflow: 'hidden',
          }}
        >
          {scene.headline}
        </div>
        {scene.body ? (
          <div
            style={{
              marginTop: 10,
              color: 'rgba(255,255,255,0.88)',
              fontSize: 22,
              lineHeight: 1.4,
              opacity: bodyOpacity,
              wordBreak: 'break-word',
              overflowWrap: 'anywhere',
              display: '-webkit-box',
              WebkitLineClamp: 3,
              WebkitBoxOrient: 'vertical',
              overflow: 'hidden',
            }}
          >
            {scene.body}
          </div>
        ) : null}
      </div>
    </AbsoluteFill>
  );
};

/**
 * 图文快闪：色带 + 镜号滑入 + 标题弹入上移，偏卖点节奏
 */
const KineticTextScene: React.FC<SceneCompProps> = ({scene}) => {
  const frame = useCurrentFrame();
  const {fps, height} = useVideoConfig();
  const accent = scene.accentColor || '#A78BFA';
  const popDur = Math.max(1, Math.round(fps * 0.18));
  const pop = interpolate(frame, [0, popDur], [0.78, 1], {
    extrapolateRight: 'clamp',
  });
  const rise = interpolate(frame, [0, popDur], [36, 0], {
    extrapolateRight: 'clamp',
  });
  const opacity = interpolate(frame, [0, Math.max(1, fps * 0.1)], [0, 1], {
    extrapolateRight: 'clamp',
  });
  const badgeX = interpolate(frame, [0, popDur], [-40, 0], {
    extrapolateRight: 'clamp',
  });
  const bodyDelay = Math.round(fps * 0.14);
  const bodyOpacity = interpolate(
    frame,
    [bodyDelay, bodyDelay + popDur],
    [0, 1],
    {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'},
  );
  const bodyY = interpolate(frame, [bodyDelay, bodyDelay + popDur], [18, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  return (
    <AbsoluteFill style={{justifyContent: 'center', alignItems: 'center'}}>
      <SceneBackground scene={scene} />
      <div
        style={{
          position: 'absolute',
          top: 0,
          left: 0,
          right: 0,
          height: Math.round(height * 0.2),
          backgroundColor: accent,
          opacity,
        }}
      />
      <div
        style={{
          position: 'absolute',
          bottom: 0,
          left: 0,
          right: 0,
          height: 16,
          backgroundColor: accent,
          opacity,
        }}
      />
      <div
        style={{
          position: 'absolute',
          top: 28,
          left: 28,
          color: '#fff',
          fontSize: 28,
          fontWeight: 800,
          opacity,
          transform: `translateX(${badgeX}px)`,
          textShadow: '0 2px 8px rgba(0,0,0,0.35)',
        }}
      >
        {String(scene.index + 1).padStart(2, '0')}
      </div>
      <div
        style={{
          opacity,
          transform: `translateY(${rise}px) scale(${pop})`,
          color: '#fff',
          fontSize: 56,
          fontWeight: 800,
          textAlign: 'center',
          maxWidth: 620,
          padding: '16px 20px',
          backgroundColor: 'rgba(0,0,0,0.55)',
          borderRadius: 12,
          lineHeight: 1.2,
          letterSpacing: 1,
        }}
      >
        {scene.headline}
      </div>
      {scene.body ? (
        <div
          style={{
            marginTop: 24,
            opacity: bodyOpacity,
            transform: `translateY(${bodyY}px)`,
            color: 'rgba(255,255,255,0.92)',
            fontSize: 24,
            textAlign: 'center',
            maxWidth: 560,
            padding: '8px 14px',
            backgroundColor: 'rgba(0,0,0,0.4)',
            borderRadius: 8,
          }}
        >
          {scene.body}
        </div>
      ) : null}
    </AbsoluteFill>
  );
};

/**
 * 品牌片头：首镜放大入场 / 末镜收束淡出，中间镜稳定框显
 */
const BrandIntroScene: React.FC<SceneCompProps> = ({
  scene,
  isFirst,
  isLast,
}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const accent = scene.accentColor || '#34D399';
  const sceneFrames = Math.max(1, Math.round(scene.durationSec * fps));
  const enter = Math.max(1, Math.round(fps * (isFirst ? 0.55 : 0.4)));
  const opacity = interpolate(frame, [0, enter], [0, 1], {
    extrapolateRight: 'clamp',
  });
  const scaleFrom = isFirst ? 0.72 : 0.92;
  const scale = interpolate(frame, [0, enter], [scaleFrom, 1], {
    extrapolateRight: 'clamp',
  });
  const dotScale = interpolate(frame, [0, enter], [0.2, 1], {
    extrapolateRight: 'clamp',
  });
  // 末镜后段收束淡出（相对本镜时长）
  let exitOpacity = 1;
  let exitScale = 1;
  if (isLast) {
    const exitStart = Math.max(enter, Math.floor(sceneFrames * 0.62));
    exitOpacity = interpolate(
      frame,
      [exitStart, Math.max(exitStart + 1, sceneFrames - 1)],
      [1, 0.15],
      {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'},
    );
    exitScale = interpolate(
      frame,
      [exitStart, Math.max(exitStart + 1, sceneFrames - 1)],
      [1, 0.9],
      {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'},
    );
  }
  return (
    <AbsoluteFill style={{justifyContent: 'center', alignItems: 'center'}}>
      <SceneBackground scene={scene} />
      <div
        style={{
          opacity: opacity * exitOpacity,
          transform: `scale(${scale * exitScale})`,
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          width: '100%',
        }}
      >
        <div
          style={{
            width: isFirst ? 72 : 56,
            height: isFirst ? 72 : 56,
            borderRadius: 999,
            backgroundColor: accent,
            marginBottom: 28,
            transform: `scale(${dotScale})`,
            boxShadow: `0 0 0 8px ${accent}33`,
          }}
        />
        <div
          style={{
            width: '80%',
            maxWidth: 640,
            border: `6px solid ${accent}`,
            borderRadius: 28,
            padding: isFirst ? '56px 40px' : '48px 36px',
            boxSizing: 'border-box',
            alignItems: 'center',
            backgroundColor: 'rgba(0,0,0,0.22)',
          }}
        >
          <div
            style={{
              color: '#fff',
              fontSize: isFirst ? 48 : 42,
              fontWeight: 700,
              textAlign: 'center',
              lineHeight: 1.25,
              letterSpacing: 1.2,
            }}
          >
            {scene.headline}
          </div>
          {scene.body ? (
            <div
              style={{
                marginTop: 20,
                color: 'rgba(255,255,255,0.85)',
                fontSize: 24,
                textAlign: 'center',
              }}
            >
              {scene.body}
            </div>
          ) : null}
        </div>
      </div>
    </AbsoluteFill>
  );
};

function SequenceScenes({
  scenes,
  SceneComp,
  logoUrl,
  logoPosition,
  captionPosition,
}: {
  scenes: Scene[];
  SceneComp: React.FC<SceneCompProps>;
  logoUrl?: string;
  logoPosition?: StoryboardProps['logoPosition'];
  captionPosition?: StoryboardProps['captionPosition'];
}) {
  const {fps} = useVideoConfig();
  let from = 0;
  const count = scenes.length;
  return (
    <AbsoluteFill>
      {scenes.map((scene, i) => {
        const durationInFrames = Math.max(1, Math.round(scene.durationSec * fps));
        const start = from;
        from += durationInFrames;
        return (
          <Sequence key={scene.id} from={start} durationInFrames={durationInFrames}>
            <SceneComp
              scene={scene}
              captionPosition={captionPosition}
              sceneCount={count}
              isFirst={i === 0}
              isLast={i === count - 1}
            />
          </Sequence>
        );
      })}
      <LogoBadge logoUrl={logoUrl} logoPosition={logoPosition} />
    </AbsoluteFill>
  );
}

export const TalkingCaptions: React.FC<StoryboardProps> = ({
  scenes = [],
  logoUrl,
  logoPosition,
  captionPosition = 'bottom',
}) => (
  <SequenceScenes
    scenes={scenes}
    SceneComp={TalkingCaptionsScene}
    logoUrl={logoUrl}
    logoPosition={logoPosition}
    captionPosition={captionPosition}
  />
);

export const KineticText: React.FC<StoryboardProps> = ({
  scenes = [],
  logoUrl,
  logoPosition,
  captionPosition,
}) => (
  <SequenceScenes
    scenes={scenes}
    SceneComp={KineticTextScene}
    logoUrl={logoUrl}
    logoPosition={logoPosition}
    captionPosition={captionPosition}
  />
);

export const BrandIntro: React.FC<StoryboardProps> = ({
  scenes = [],
  logoUrl,
  logoPosition,
  captionPosition,
}) => (
  <SequenceScenes
    scenes={scenes}
    SceneComp={BrandIntroScene}
    logoUrl={logoUrl}
    logoPosition={logoPosition}
    captionPosition={captionPosition}
  />
);
