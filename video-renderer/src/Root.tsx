import React from 'react';
import {
  AbsoluteFill,
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
};

export type StoryboardProps = {
  title?: string;
  templateId?: string;
  scenes?: Scene[];
  fps?: number;
};

/** 口播字幕条：底部字幕区 + 左侧强调色 */
const TalkingCaptionsScene: React.FC<{scene: Scene}> = ({scene}) => {
  const frame = useCurrentFrame();
  const {fps, height} = useVideoConfig();
  const opacity = Math.min(1, frame / (fps * 0.25));
  const barH = Math.round(height * 0.28);
  const accent = scene.accentColor || '#38BDF8';
  return (
    <AbsoluteFill style={{backgroundColor: scene.bgColor || '#0F172A'}}>
      <div
        style={{
          position: 'absolute',
          left: 0,
          right: 0,
          bottom: 0,
          height: barH,
          backgroundColor: 'rgba(0,0,0,0.88)',
          borderLeft: `14px solid ${accent}`,
          padding: '28px 36px',
          opacity,
          boxSizing: 'border-box',
        }}
      >
        <div
          style={{
            color: '#fff',
            fontSize: 42,
            fontWeight: 700,
            lineHeight: 1.25,
          }}
        >
          {scene.headline}
        </div>
        {scene.body ? (
          <div
            style={{
              marginTop: 12,
              color: 'rgba(255,255,255,0.88)',
              fontSize: 24,
            }}
          >
            {scene.body}
          </div>
        ) : null}
      </div>
    </AbsoluteFill>
  );
};

/** 图文快闪：顶部色带 + 大号标题弹出 */
const KineticTextScene: React.FC<{scene: Scene}> = ({scene}) => {
  const frame = useCurrentFrame();
  const {fps, height} = useVideoConfig();
  const accent = scene.accentColor || '#A78BFA';
  const pop = interpolate(frame, [0, Math.max(1, fps * 0.15)], [0.86, 1], {
    extrapolateRight: 'clamp',
  });
  const opacity = Math.min(1, frame / (fps * 0.12));
  return (
    <AbsoluteFill
      style={{
        backgroundColor: scene.bgColor || '#1E1B4B',
        justifyContent: 'center',
        alignItems: 'center',
      }}
    >
      <div
        style={{
          position: 'absolute',
          top: 0,
          left: 0,
          right: 0,
          height: Math.round(height * 0.2),
          backgroundColor: accent,
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
        }}
      />
      <div
        style={{
          position: 'absolute',
          top: 28,
          left: 28,
          color: '#fff',
          fontSize: 28,
          fontWeight: 700,
          opacity,
        }}
      >
        {scene.index + 1}
      </div>
      <div
        style={{
          opacity,
          transform: `scale(${pop})`,
          color: '#fff',
          fontSize: 56,
          fontWeight: 800,
          textAlign: 'center',
          maxWidth: 620,
          padding: '16px 20px',
          backgroundColor: 'rgba(0,0,0,0.55)',
          borderRadius: 12,
          lineHeight: 1.2,
        }}
      >
        {scene.headline}
      </div>
      {scene.body ? (
        <div
          style={{
            marginTop: 24,
            opacity,
            color: 'rgba(255,255,255,0.9)',
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

/** 品牌片头：居中描边框 + 顶部色点 */
const BrandIntroScene: React.FC<{scene: Scene}> = ({scene}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const accent = scene.accentColor || '#34D399';
  const opacity = interpolate(frame, [0, Math.max(1, fps * 0.5)], [0, 1], {
    extrapolateRight: 'clamp',
  });
  return (
    <AbsoluteFill
      style={{
        backgroundColor: scene.bgColor || '#022C22',
        justifyContent: 'center',
        alignItems: 'center',
      }}
    >
      <div
        style={{
          width: 56,
          height: 56,
          borderRadius: 28,
          backgroundColor: accent,
          marginBottom: 28,
          opacity,
        }}
      />
      <div
        style={{
          opacity,
          width: '80%',
          maxWidth: 640,
          border: `6px solid ${accent}`,
          borderRadius: 28,
          padding: '48px 36px',
          boxSizing: 'border-box',
          alignItems: 'center',
        }}
      >
        <div
          style={{
            color: '#fff',
            fontSize: 42,
            fontWeight: 700,
            textAlign: 'center',
            lineHeight: 1.25,
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
    </AbsoluteFill>
  );
};

function SequenceScenes({
  scenes,
  SceneComp,
}: {
  scenes: Scene[];
  SceneComp: React.FC<{scene: Scene}>;
}) {
  const {fps} = useVideoConfig();
  let from = 0;
  return (
    <AbsoluteFill>
      {scenes.map((scene) => {
        const durationInFrames = Math.max(1, Math.round(scene.durationSec * fps));
        const start = from;
        from += durationInFrames;
        return (
          <Sequence key={scene.id} from={start} durationInFrames={durationInFrames}>
            <SceneComp scene={scene} />
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
}

export const TalkingCaptions: React.FC<StoryboardProps> = ({scenes = []}) => (
  <SequenceScenes scenes={scenes} SceneComp={TalkingCaptionsScene} />
);

export const KineticText: React.FC<StoryboardProps> = ({scenes = []}) => (
  <SequenceScenes scenes={scenes} SceneComp={KineticTextScene} />
);

export const BrandIntro: React.FC<StoryboardProps> = ({scenes = []}) => (
  <SequenceScenes scenes={scenes} SceneComp={BrandIntroScene} />
);
