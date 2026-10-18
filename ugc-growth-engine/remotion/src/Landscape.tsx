import React, {useEffect, useState} from 'react';
import {AbsoluteFill, Audio, Img, OffthreadVideo, Sequence, delayRender, continueRender, interpolate, spring,
        staticFile, useCurrentFrame, useVideoConfig} from 'remotion';

// One-off 16:9 explainer-ad composition (1920x1080). Scenes: stock clip or real app screenshot beside a headline, then an end card.
export type Word = {t: string; hl?: boolean; strike?: boolean};
export type LScene = {kind: 'video' | 'shot' | 'end'; src?: string; start?: number; frames: number; words: Word[]; shot?: string;
                      voice: string; voiceAt?: number; extra?: {src: string; at: number; volume: number}[]};
export type LProps = {scenes: LScene[]; totalFrames: number; fps: number; width: number; height: number; appName: string; icon: string;
                      points: string[]; cta: string; note: string; music: string};

const PURPLE = '#5B2BB5';
const FONT = 'NotoDeva, Poppins, sans-serif';

const useFonts = () => {
  const [h] = useState(() => delayRender('fonts'));
  useEffect(() => {
    Promise.all([
      new FontFace('NotoDeva', `url(${staticFile('upisb/NotoSansDevanagari-Bold.ttf')})`, {weight: '700'}).load(),
      new FontFace('Poppins', `url(${staticFile('upisb/Poppins-ExtraBold.ttf')})`, {weight: '800'}).load(),
    ]).then((fs) => { fs.forEach((f) => document.fonts.add(f)); continueRender(h); }).catch(() => continueRender(h));
  }, [h]);
};

const Headline: React.FC<{words: Word[]; frame: number; fps: number; size?: number; maxWidth?: number}> = ({words, frame, fps, size = 104, maxWidth = 1000}) => {
  const p = spring({frame, fps, config: {damping: 14, stiffness: 140}});
  return (
    <div style={{transform: `translateY(${(1 - p) * 40}px) scale(${0.94 + 0.06 * p})`, opacity: p, maxWidth,
                 background: 'rgba(10,8,25,0.72)', borderRadius: 28, padding: '26px 40px', backdropFilter: 'blur(6px)',
                 boxShadow: '0 12px 40px rgba(0,0,0,0.45)'}}>
      <div style={{fontFamily: FONT, fontWeight: 700, fontSize: size, lineHeight: 1.28, color: '#fff'}}>
        {words.map((w, i) => (
          <span key={i} style={{color: w.hl ? '#FFD400' : '#fff', textDecoration: w.strike ? 'line-through' : 'none',
                                textDecorationColor: '#FF3B3B', textDecorationThickness: 9, marginRight: 22, display: 'inline-block'}}>
            {w.t}
          </span>
        ))}
      </div>
    </div>
  );
};

const PhoneFrame: React.FC<{src: string; frame: number; fps: number}> = ({src, frame, fps}) => {
  const p = spring({frame: frame - 6, fps, config: {damping: 15, stiffness: 110}});
  const H = 940;
  return (
    <div style={{position: 'absolute', right: 150, top: (1080 - H) / 2, height: H, transform: `translateX(${(1 - p) * 220}px) rotate(${(1 - p) * 4}deg)`, opacity: p,
                 borderRadius: 54, border: '12px solid #111', boxShadow: '0 30px 80px rgba(0,0,0,0.6)', overflow: 'hidden', background: '#000'}}>
      <Img src={staticFile(src)} style={{height: '100%', display: 'block'}} />
    </div>
  );
};

const SceneView: React.FC<{s: LScene; p: LProps}> = ({s, p}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const zoom = 1 + 0.06 * (frame / s.frames);
  const flash = interpolate(frame, [0, 5], [0.55, 0], {extrapolateRight: 'clamp'});
  if (s.kind === 'end') {
    const q = spring({frame, fps, config: {damping: 14}});
    return (
      <AbsoluteFill style={{background: `radial-gradient(circle at 25% 30%, #7A45E0 0%, ${PURPLE} 45%, #24104F 100%)`, fontFamily: FONT}}>
        <div style={{position: 'absolute', left: 130, top: 150, display: 'flex', alignItems: 'center', gap: 40, transform: `scale(${0.85 + 0.15 * q})`, opacity: q}}>
          <Img src={staticFile(p.icon)} style={{width: 220, height: 220, borderRadius: 48, boxShadow: '0 12px 40px rgba(0,0,0,0.4)'}} />
          <div style={{fontFamily: 'Poppins', fontWeight: 800, fontSize: 118, color: '#fff'}}>{p.appName}</div>
        </div>
        <div style={{position: 'absolute', left: 130, top: 460, display: 'flex', flexDirection: 'column', gap: 26}}>
          {p.points.map((t, i) => {
            const a = spring({frame: frame - 10 - i * 8, fps, config: {damping: 14}});
            return (<div key={i} style={{opacity: a, transform: `translateX(${(1 - a) * -60}px)`, fontSize: 64, color: '#fff', fontWeight: 700}}>
              <span style={{color: '#FFD400', marginRight: 24}}>✔</span>{t}</div>);
          })}
        </div>
        <div style={{position: 'absolute', left: 130, bottom: 150, background: '#FFD400', color: '#1a0f3a', fontSize: 66, fontWeight: 700, padding: '22px 56px', borderRadius: 999,
                     transform: `scale(${1 + 0.03 * Math.sin(frame / 5)})`}}>{p.cta}</div>
        <div style={{position: 'absolute', left: 130, bottom: 70, fontSize: 34, color: 'rgba(255,255,255,0.75)'}}>{p.note}</div>
        <PhoneFrame src="upisb/shot1.png" frame={frame} fps={fps} />
      </AbsoluteFill>
    );
  }
  return (
    <AbsoluteFill style={{background: '#111'}}>
      <AbsoluteFill style={{transform: `scale(${zoom})`, filter: s.kind === 'shot' ? 'blur(14px) brightness(0.55)' : 'none'}}>
        <OffthreadVideo src={staticFile(s.src!)} startFrom={Math.round((s.start ?? 0) * p.fps)} muted style={{width: '100%', height: '100%', objectFit: 'cover'}} />
      </AbsoluteFill>
      <AbsoluteFill style={{background: 'linear-gradient(90deg, rgba(0,0,0,0.55) 0%, rgba(0,0,0,0.05) 60%)'}} />
      <div style={{position: 'absolute', left: 90, bottom: s.kind === 'shot' ? 'auto' : 110, top: s.kind === 'shot' ? 340 : 'auto'}}>
        <Headline words={s.words} frame={frame} fps={fps} maxWidth={s.kind === 'shot' ? 900 : 1300} />
      </div>
      {s.kind === 'shot' && <PhoneFrame src={s.shot!} frame={frame} fps={fps} />}
      <AbsoluteFill style={{background: '#fff', opacity: flash}} />
    </AbsoluteFill>
  );
};

export const Landscape: React.FC<LProps> = (p) => {
  useFonts();
  const frame = useCurrentFrame();
  let at = 0;
  return (
    <AbsoluteFill style={{background: '#000'}}>
      {p.scenes.map((s, i) => {
        const from = at; at += s.frames;
        return (
          <Sequence key={i} from={from} durationInFrames={s.frames}>
            <SceneView s={s} p={p} />
            <Sequence from={s.voiceAt ?? 4}><Audio src={staticFile(s.voice)} volume={1} /></Sequence>
            {(s.extra ?? []).map((e, j) => (<Sequence key={j} from={e.at}><Audio src={staticFile(e.src)} volume={e.volume} /></Sequence>))}
          </Sequence>
        );
      })}
      <Audio src={staticFile(p.music)} loop volume={interpolate(frame, [0, 20, p.totalFrames - 45, p.totalFrames], [0, 0.11, 0.11, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'})} />
    </AbsoluteFill>
  );
};
