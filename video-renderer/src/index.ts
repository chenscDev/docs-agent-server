import {Composition, registerRoot} from 'remotion';
import {BrandIntro, KineticText, TalkingCaptions, StoryboardProps} from './Root';

const defaultProps: StoryboardProps = {
  title: 'Demo',
  templateId: 'talking-captions',
  fps: 30,
  scenes: [
    {
      id: 'sc_0',
      index: 0,
      durationSec: 3,
      headline: '一句话生成短视频',
      body: 'AI × Remotion',
      bgColor: '#0F172A',
      accentColor: '#38BDF8',
    },
    {
      id: 'sc_1',
      index: 1,
      durationSec: 3,
      headline: '流式分镜 · 可取消',
      body: 'docs-agent-server',
      bgColor: '#0F172A',
      accentColor: '#38BDF8',
    },
  ],
};

const RemotionRoot: React.FC = () => {
  const totalSec = (defaultProps.scenes || []).reduce(
    (s, sc) => s + sc.durationSec,
    0,
  );
  const fps = 30;
  const durationInFrames = Math.max(1, Math.round(totalSec * fps));

  const calcSize = (aspect?: string) => {
    if (aspect === '16:9') return {width: 1280, height: 720};
    if (aspect === '1:1') return {width: 720, height: 720};
    return {width: 720, height: 1280};
  };

  return (
    <>
      <Composition
        id="TalkingCaptions"
        component={TalkingCaptions}
        durationInFrames={durationInFrames}
        fps={fps}
        width={720}
        height={1280}
        defaultProps={defaultProps}
        calculateMetadata={async ({props}) => {
          const p = props as StoryboardProps & {aspectRatio?: string};
          const scenes = p.scenes || [];
          const sec = scenes.reduce((a, b) => a + b.durationSec, 6);
          const size = calcSize(p.aspectRatio);
          return {
            durationInFrames: Math.max(1, Math.round(sec * fps)),
            ...size,
            props,
          };
        }}
      />
      <Composition
        id="KineticText"
        component={KineticText}
        durationInFrames={durationInFrames}
        fps={fps}
        width={720}
        height={1280}
        defaultProps={defaultProps}
        calculateMetadata={async ({props}) => {
          const p = props as StoryboardProps & {aspectRatio?: string};
          const scenes = p.scenes || [];
          const sec = scenes.reduce((a, b) => a + b.durationSec, 6);
          const size = calcSize(p.aspectRatio);
          return {
            durationInFrames: Math.max(1, Math.round(sec * fps)),
            ...size,
            props,
          };
        }}
      />
      <Composition
        id="BrandIntro"
        component={BrandIntro}
        durationInFrames={durationInFrames}
        fps={fps}
        width={720}
        height={1280}
        defaultProps={defaultProps}
        calculateMetadata={async ({props}) => {
          const p = props as StoryboardProps & {aspectRatio?: string};
          const scenes = p.scenes || [];
          const sec = scenes.reduce((a, b) => a + b.durationSec, 6);
          const size = calcSize(p.aspectRatio);
          return {
            durationInFrames: Math.max(1, Math.round(sec * fps)),
            ...size,
            props,
          };
        }}
      />
    </>
  );
};

registerRoot(RemotionRoot);
