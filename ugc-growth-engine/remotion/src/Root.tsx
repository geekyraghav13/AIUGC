import React from 'react';
import {Composition} from 'remotion';
import {UgcVideo} from './UgcVideo';
import {Cover} from './Cover';
import {Landscape} from './Landscape';
import type {CoverProps} from './types';
import type {Look, Props} from './types';

const look: Look = {y: 1290, band: 'lower', primary: 'FFFFFF', outline: '000000', highlight: 'FFD400', plate: null};
const defaults: Props = {
  fps: 30, width: 1080, height: 1920, totalFrames: 90, segments: [], voice: [], music: null, captionLines: [],
  design: {family: 'Poppins', scriptFamily: 'Poppins', langFont: 'Noto Sans', weight: 800, anim: 'pop', transition: 'punch', caps: false, size: 90,
           outlinePx: 6},
  hook: {text: '', seconds: 2, look}, endCard: {startS: 0, appName: '', cta: '', icon: null, bg: ''},
  aiLabel: 'AI-generated', fonts: [], hookFrame: 15, voiceVolume: 1,
};

const coverDefaults: CoverProps = {bg: '', hook: '', appName: '', icon: null, accent: 'FFD400', design: defaults.design,
                                   fonts: [], aiLabel: 'AI-generated'};

export const Root: React.FC = () => (
  <>
  <Composition
    id="Ugc"
    component={UgcVideo}
    width={1080}
    height={1920}
    fps={30}
    durationInFrames={90}
    defaultProps={defaults}
    calculateMetadata={({props}) => ({
      durationInFrames: Math.max(1, props.totalFrames),
      fps: props.fps,
      width: props.width,
      height: props.height,
    })}
  />
  <Composition id="Landscape" component={Landscape} width={1920} height={1080} fps={30} durationInFrames={900}
               defaultProps={{scenes: [], totalFrames: 900, fps: 30, width: 1920, height: 1080, appName: '', icon: '', points: [], cta: '', note: '', music: ''} as any}
               calculateMetadata={({props}: any) => ({durationInFrames: Math.max(1, props.totalFrames)})} />
  <Composition id="Cover" component={Cover} width={1080} height={1920} fps={30} durationInFrames={1}
               defaultProps={coverDefaults} />
  </>
);
