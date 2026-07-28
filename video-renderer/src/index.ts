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
          const scenes = (props as StoryboardProps).scenes || [];
          const sec = scenes.reduce((a, b) => a + b.durationSec, 6);
          return {
            durationInFrames: Math.max(1, Math.round(sec * fps)),
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
      />
      <Composition
        id="BrandIntro"
        component={BrandIntro}
        durationInFrames={durationInFrames}
        fps={fps}
        width={720}
        height={1280}
        defaultProps={defaultProps}
      />
    </>
  );
};

registerRoot(RemotionRoot);
