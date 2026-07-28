import React from 'react';
import {AbsoluteFill, Sequence, useCurrentFrame, useVideoConfig} from 'remotion';

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

const SceneBlock: React.FC<{scene: Scene}> = ({scene}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const opacity = Math.min(1, frame / (fps * 0.3));
  return (
    <AbsoluteFill
      style={{
        backgroundColor: scene.bgColor || '#0F172A',
        justifyContent: 'center',
        alignItems: 'center',
        padding: 48,
      }}
    >
      <div
        style={{
          opacity,
          color: '#fff',
          fontSize: 52,
          fontWeight: 700,
          textAlign: 'center',
          lineHeight: 1.25,
          borderLeft: `6px solid ${scene.accentColor || '#38BDF8'}`,
          paddingLeft: 24,
        }}
      >
        {scene.headline}
      </div>
      {scene.body ? (
        <div
          style={{
            marginTop: 28,
            opacity,
            color: 'rgba(255,255,255,0.82)',
            fontSize: 28,
            textAlign: 'center',
            maxWidth: 560,
          }}
        >
          {scene.body}
        </div>
      ) : null}
    </AbsoluteFill>
  );
};

export const TalkingCaptions: React.FC<StoryboardProps> = ({scenes = []}) => {
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
            <SceneBlock scene={scene} />
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};

export const KineticText = TalkingCaptions;
export const BrandIntro = TalkingCaptions;
