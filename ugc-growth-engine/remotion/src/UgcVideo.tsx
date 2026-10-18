import React, {useEffect, useState} from 'react';
import {
  AbsoluteFill, Audio, Img, Loop, OffthreadVideo, Sequence, cancelRender, continueRender, delayRender, interpolate,
  spring, staticFile, useCurrentFrame, useVideoConfig,
} from 'remotion';
import {fitText, measureText} from '@remotion/layout-utils';
import type {CaptionLine, Design, Headline, Look, Props, Segment} from './types';
import {Graphic} from './Graphics';

const LATIN = 'Noto Sans';
export const hex = (c: string) => `#${c.replace('#', '')}`;

// ---------------------------------------------------------------- fonts

// Returns true once every font is loaded. Text is measured with fitText, which caches by font stack, so nothing
// that measures text may render before the fonts are ready (otherwise fallback-font widths get cached).
export const useFonts = (fonts: Props['fonts']): boolean => {
  const [handle] = useState(() => delayRender('fonts'));
  const [ready, setReady] = useState(false);
  useEffect(() => {
    Promise.all(
      fonts.map(async (f) => {
        const face = new FontFace(f.family, `url(${staticFile(f.file)})`, {weight: f.weight});
        await face.load();
        document.fonts.add(face);
      }),
    )
      .then(() => { setReady(true); continueRender(handle); })
      .catch((e) => cancelRender(e));
  }, [fonts, handle]);
  return ready;
};

export const stack = (d: Design) =>
  `'${d.family}', '${d.scriptFamily}', '${d.langFont}', '${LATIN}', sans-serif`;

const luminance = (h: string) => {
  const c = [0, 2, 4].map((i) => parseInt(h.replace('#', '').slice(i, i + 2), 16) / 255)
    .map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
};
export const readableOn = (bg: string) => (luminance(bg) > 0.4 ? '111111' : 'FFFFFF');

// ---------------------------------------------------------------- scenes

const Cover: React.CSSProperties = {width: '100%', height: '100%', objectFit: 'cover'};

// Every cut lands with a small push-in (punch) or a gentle settle (fade) so the edit feels alive.
const CutIn: React.FC<{kind: Design['transition']; children: React.ReactNode}> = ({kind, children}) => {
  const f = useCurrentFrame();
  const {fps} = useVideoConfig();
  const s = spring({frame: f, fps, config: {damping: 16, stiffness: 180}, durationInFrames: kind === 'punch' ? 10 : 18});
  const from = kind === 'punch' ? 1.09 : 1.03;
  return <AbsoluteFill style={{transform: `scale(${from + (1 - from) * s})`}}>{children}</AbsoluteFill>;
};

const Pan: React.FC<{src: string; frames: number; direction: number}> = ({src, frames, direction}) => {
  const f = useCurrentFrame();
  const t = frames > 1 ? f / (frames - 1) : 0;
  const tx = (direction % 2 === 0 ? t : 1 - t) * -8; // % of a 110%-size image
  const ty = direction % 3 === 0 ? t * -8 : -4;
  return (
    <AbsoluteFill style={{overflow: 'hidden'}}>
      <Img src={staticFile(src)} style={{...Cover, position: 'absolute', top: 0, left: 0, flexShrink: 0, width: '110%',
                                          height: '110%', transform: `translate(${tx}%, ${ty}%)`}} />
    </AbsoluteFill>
  );
};

const Screenshot: React.FC<{src: string; frames: number}> = ({src, frames}) => {
  const f = useCurrentFrame();
  const zoom = 1 + 0.07 * (f / Math.max(1, frames));
  return (
    <AbsoluteFill style={{background: '#000', overflow: 'hidden'}}>
      <Img src={staticFile(src)} style={{...Cover, filter: 'blur(28px) brightness(0.88)', transform: 'scale(1.15)'}} />
      <div style={{position: 'absolute', top: 150, left: 144, width: 792, height: 1408, display: 'flex',
                   alignItems: 'center', justifyContent: 'center'}}>
        <Img src={staticFile(src)} style={{maxWidth: '100%', maxHeight: '100%', transform: `scale(${zoom})`,
                                            borderRadius: 24, boxShadow: '0 12px 60px rgba(0,0,0,.55)'}} />
      </div>
    </AbsoluteFill>
  );
};

const SegmentView: React.FC<{seg: Segment; fps: number; transition: Design['transition']; design: Design;
  accent: string; volume: number}> = ({seg, fps, transition, design, accent, volume}) => {
  if (seg.kind === 'screenshot') return <Screenshot src={seg.src} frames={seg.frames} />;
  if (seg.kind === 'graphic') return <Graphic spec={seg.graphic} design={design} accent={accent} stack={stack(design)} />;
  let inner: React.ReactNode;
  if (seg.kind === 'video' && seg.audible) {
    // talking presenter: the clip carries its own lip-synced voice
    inner = <OffthreadVideo src={staticFile(seg.src)} volume={volume} style={Cover} />;
  } else if (seg.kind === 'video') {
    const clipFrames = Math.max(1, Math.floor(seg.clipS * fps));
    inner = seg.clipS * fps >= seg.startS * fps + seg.frames
      ? <OffthreadVideo src={staticFile(seg.src)} muted startFrom={Math.floor(seg.startS * fps)} style={Cover} />
      : <Loop durationInFrames={clipFrames}><OffthreadVideo src={staticFile(seg.src)} muted style={Cover} /></Loop>;
  } else {
    inner = <Pan src={seg.src} frames={seg.frames} direction={seg.direction} />;
  }
  return <CutIn kind={transition}>{inner}</CutIn>;
};

// ---------------------------------------------------------------- captions

const accentWord = (t: string) => /[0-9₹%]/.test(t) || (/^[A-Z]{2,}[.,!?]?$/.test(t));

const Captions: React.FC<{lines: CaptionLine[]; design: Design}> = ({lines, design}) => {
  const {fps} = useVideoConfig();
  const frame = useCurrentFrame();
  const t = frame / fps;
  const li = lines.findIndex((l) => t >= l.start && t < l.end);
  if (li < 0) return null;
  const line = lines[li];
  const look = line.look;
  const lf = frame - Math.round(line.start * fps);
  const idx = Math.max(0, line.words.findIndex((w, i) => t >= w.start && (i === line.words.length - 1 || t < line.words[i + 1].start)));
  const texts = line.words.map((w) => (design.caps ? w.text.toUpperCase() : w.text));
  const layout = design.captionLayout || 'karaoke';

  if (layout === 'word') {
    // one big word at a time
    const w = line.words[idx];
    const wf = frame - Math.round(w.start * fps);
    const s = spring({frame: wf, fps, config: {damping: 10, stiffness: 260}});
    const txt = texts[idx];
    const wsize = Math.round(Math.min(design.size * 1.6, fitText({text: txt, withinWidth: 860, fontFamily: stack(design),
                                                                  fontWeight: design.weight}).fontSize));
    return (
      <div style={{position: 'absolute', left: 60, right: 60, top: look.y, display: 'flex', justifyContent: 'center',
                   transform: `translateY(-50%) scale(${0.7 + 0.3 * s})`}}>
        <span style={{fontFamily: stack(design), fontWeight: design.weight, fontSize: wsize, lineHeight: 1.1,
                      color: hex(accentWord(w.text) || idx % 2 ? look.highlight : look.primary),
                      WebkitTextStroke: `${design.outlinePx * 2.4}px ${hex(look.outline)}`, paintOrder: 'stroke fill',
                      textShadow: '0 6px 18px rgba(0,0,0,.5)'}}>{txt}</span>
      </div>
    );
  }

  // Fit the line to the frame: start from the ideal size, then shrink until the real rendered width (word widths +
  // the gaps between words + outline + plate padding) fits inside the safe area.
  const full = texts.join(' ');
  const plated = Boolean(look.plate) || design.anim === 'pill';
  const strokeAt = (sz: number) => Math.max(2, design.outlinePx * (sz / design.size)) * (look.plate ? 0.6 : 1);
  const gapAt = (sz: number) => 0.3 * sz + strokeAt(sz) * 3;
  const widthAt = (sz: number) =>
    measureText({text: full, fontFamily: stack(design), fontWeight: design.weight, fontSize: sz}).width
    + gapAt(sz) * (texts.length - 1) + strokeAt(sz) * 4 + (plated ? 68 : 0);
  const SAFE = 960;
  let size = Math.round(Math.min(design.size * 1.25, fitText({text: full, withinWidth: SAFE - 120, fontFamily: stack(design),
                                                              fontWeight: design.weight}).fontSize));
  size = Math.max(size, Math.round(design.size * 0.7));
  while (size > design.size * 0.45 && widthAt(size) > SAFE) size -= 3;
  const stroke = strokeAt(size);

  const enter = spring({frame: lf, fps, config: {damping: design.anim === 'bounce' ? 8 : 13, stiffness: 190}});
  let transform = 'translateY(-50%)';
  let opacity = 1;
  if (design.anim === 'pop') transform += ` scale(${0.8 + 0.2 * enter}) translateY(${(1 - enter) * 24}px)`;
  if (design.anim === 'bounce') transform += ` scale(${1.15 - 0.15 * enter}) rotate(${(li % 2 ? 1 : -1) * 2.2 * enter}deg)`;
  if (design.anim === 'slide') { transform += ` translateY(${(1 - enter) * 70}px)`; opacity = enter; }
  if (design.anim === 'pill') transform += ` scale(${0.9 + 0.1 * enter})`;
  if (design.anim === 'fade') opacity = interpolate(lf, [0, 8], [0, 1], {extrapolateRight: 'clamp'});

  const pill = design.anim === 'pill';
  const boxed = layout === 'boxed';
  const bar = layout === 'lowerbar';
  const plate = bar ? 'rgba(0,0,0,.72)' : boxed ? (look.plate ?? 'rgba(0,0,0,.66)') : pill ? (look.plate ?? 'rgba(0,0,0,.72)') : look.plate;
  if (bar) {
    // full-width lower bar with an accent edge (news / talking-head style), always clear of the face
    return (
      <div style={{position: 'absolute', left: 0, right: 0, top: 1500, opacity, background: plate ?? undefined,
                   borderLeft: `14px solid ${hex(look.highlight)}`, padding: '26px 60px', textAlign: 'center'}}>
        <div style={{fontFamily: stack(design), fontWeight: design.weight, fontSize: Math.round(size * 0.85),
                     lineHeight: 1.2, color: '#fff'}}>
          {line.words.map((w, i) => (
            <span key={i} style={{color: i === idx || accentWord(w.text) ? hex(look.highlight) : '#fff',
                                   marginRight: i < line.words.length - 1 ? `${0.28 * size}px` : 0}}>{texts[i]}</span>
          ))}
        </div>
      </div>
    );
  }
  return (
    <div style={{position: 'absolute', left: 50, right: 50, top: look.y, display: 'flex', justifyContent: 'center',
                 transform, opacity}}>
      <div style={{
        fontFamily: stack(design), fontWeight: design.weight, fontSize: size, lineHeight: 1.2, textAlign: 'center',
        letterSpacing: design.anim === 'fade' ? 0.5 : 0, color: hex(look.primary),
        WebkitTextStroke: pill || boxed || design.anim === 'fade' ? undefined : `${stroke * 2}px ${hex(look.outline)}`,
        paintOrder: 'stroke fill', textShadow: pill ? undefined : '0 4px 14px rgba(0,0,0,.45)',
        background: plate ?? undefined, borderRadius: pill ? 60 : boxed ? 14 : 30,
        padding: plate ? (boxed ? '14px 30px' : '12px 34px') : undefined,
      }}>
        {line.words.map((w, i) => {
          const active = i === idx;
          const accent = accentWord(w.text);
          const wf = frame - Math.round(w.start * fps);
          const pop = active && (design.anim === 'pop' || design.anim === 'bounce')
            ? 1 + 0.07 * spring({frame: wf, fps, config: {damping: 9, stiffness: 240}}) : accent ? 1.06 : 1;
          const bg = pill && active ? hex(look.highlight) : undefined;
          const color = pill && active ? hex(readableOn(look.highlight))
            : active || accent ? hex(look.highlight) : hex(look.primary);
          return (
            <span key={i} style={{display: 'inline-block', transform: `scale(${pop})`, color, background: bg,
                                   borderRadius: 24, padding: pill ? '0 14px' : undefined, marginRight: i < line.words.length - 1 ? `${0.3 * size + stroke * 3}px` : 0}}>
              {texts[i]}
            </span>
          );
        })}
      </div>
    </div>
  );
};

// ---------------------------------------------------------------- hook sticker

const Hook: React.FC<{text: string; seconds: number; look: Look; design: Design}> = ({text, seconds, look, design}) => {
  const {fps} = useVideoConfig();
  const f = useCurrentFrame();
  const enter = spring({frame: f, fps, config: {damping: 11, stiffness: 170}});
  const out = interpolate(f, [seconds * fps - 6, seconds * fps], [1, 0], {extrapolateLeft: 'clamp'});
  const label = design.caps ? text.toUpperCase() : text;
  const size = Math.round(Math.max(design.size * 0.62, Math.min(design.size * 0.95,
    fitText({text: label, withinWidth: 820, fontFamily: stack(design), fontWeight: design.weight}).fontSize)));
  const bg = look.highlight;
  const style = design.hookStyle || 'card';
  if (style === 'underline') {
    return (
      <div style={{position: 'absolute', left: 70, right: 70, top: look.y, textAlign: 'center', opacity: out,
                   transform: `translateY(-50%) translateY(${(1 - enter) * 50}px)`}}>
        <span style={{fontFamily: stack(design), fontWeight: design.weight, fontSize: Math.round(size * 1.08), lineHeight: 1.25,
                      color: '#fff', WebkitTextStroke: `${design.outlinePx * 2}px #000`, paintOrder: 'stroke fill',
                      boxShadow: `inset 0 -${Math.round(size * 0.3 * enter)}px 0 ${hex(bg)}`}}>{label}</span>
      </div>
    );
  }
  if (style === 'stacked') {
    const words = label.split(/\s+/).filter(Boolean);
    return (
      <div style={{position: 'absolute', left: 70, right: 70, top: look.y, opacity: out, display: 'flex',
                   flexDirection: 'column', alignItems: 'flex-start', transform: 'translateY(-50%)'}}>
        {words.map((w, i) => {
          const s = spring({frame: f - i * 3, fps, config: {damping: 12, stiffness: 200}});
          return (
            <span key={i} style={{fontFamily: stack(design), fontWeight: design.weight, lineHeight: 1.05,
                                   fontSize: Math.round(size * (i % 2 ? 1.0 : 1.18)), color: i % 2 ? hex(bg) : '#fff',
                                   WebkitTextStroke: `${design.outlinePx * 2}px #000`, paintOrder: 'stroke fill',
                                   transform: `translateX(${(1 - s) * -300}px)`}}>{w}</span>
          );
        })}
      </div>
    );
  }
  return (
    <div style={{position: 'absolute', left: 60, right: 60, top: look.y, display: 'flex', justifyContent: 'center',
                 opacity: out, transform: `translateY(-50%) translateX(${(1 - enter) * -420}px) rotate(${-2.2 * enter}deg) scale(${0.9 + 0.1 * enter})`}}>
      <div style={{fontFamily: stack(design), fontWeight: design.weight, fontSize: size, lineHeight: 1.22, textAlign: 'center',
                   color: hex(readableOn(bg)), background: hex(bg), padding: '16px 40px', borderRadius: 26,
                   boxShadow: '0 10px 0 rgba(0,0,0,.35), 0 18px 40px rgba(0,0,0,.35)', maxWidth: 940}}>
        {label}
      </div>
    </div>
  );
};

// ---------------------------------------------------------------- per-scene headlines

const HeadlineView: React.FC<{h: Headline; design: Design; accent: string}> = ({h, design, accent}) => {
  const f = useCurrentFrame();
  const {fps} = useVideoConfig();
  const s = spring({frame: f, fps, config: {damping: 13, stiffness: 170}});
  const out = interpolate(f, [h.frames - 6, h.frames], [1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const text = design.caps ? h.text.toUpperCase() : h.text;
  const size = Math.round(Math.max(64, Math.min(design.size * 1.25,
    fitText({text, withinWidth: 820, fontFamily: stack(design), fontWeight: design.weight}).fontSize)));
  const st = design.headlineStyle || 'sticker';
  const font: React.CSSProperties = {fontFamily: stack(design), fontWeight: design.weight, fontSize: size, lineHeight: 1.15};
  const words = text.split(' ');
  let el: React.ReactNode;
  if (st === 'banner') {
    el = <div style={{...font, background: 'rgba(10,10,14,.82)', color: '#fff', padding: '18px 40px',
                      borderLeft: `16px solid ${hex(accent)}`, width: '100%', textAlign: 'left'}}>{text}</div>;
  } else if (st === 'outline') {
    el = <div style={{...font, fontSize: Math.round(size * 1.12), color: '#fff', textAlign: 'center',
                      WebkitTextStroke: `${design.outlinePx * 2.2}px ${hex(accent)}`, paintOrder: 'stroke fill'}}>{text}</div>;
  } else if (st === 'underline') {
    el = <div style={{...font, color: '#fff', textAlign: 'center', WebkitTextStroke: `${design.outlinePx * 1.8}px #000`,
                      paintOrder: 'stroke fill'}}>
      <span style={{boxShadow: `inset 0 -${Math.round(size * 0.32)}px 0 ${hex(accent)}cc`}}>{text}</span></div>;
  } else if (st === 'split') {
    el = <div style={{...font, textAlign: 'center', WebkitTextStroke: `${design.outlinePx * 2}px #000`, paintOrder: 'stroke fill'}}>
      <span style={{color: hex(accent)}}>{words[0]} </span><span style={{color: '#fff'}}>{words.slice(1).join(' ')}</span></div>;
  } else {
    el = <div style={{...font, background: hex(accent), color: hex(readableOn(accent)), padding: '14px 34px',
                      borderRadius: 22, transform: 'rotate(-2deg)', boxShadow: '0 10px 0 rgba(0,0,0,.3)'}}>{text}</div>;
  }
  return (
    <div style={{position: 'absolute', left: st === 'banner' ? 0 : 60, right: st === 'banner' ? 0 : 60, top: h.y,
                 display: 'flex', justifyContent: 'center', opacity: s * out,
                 transform: `translateY(-50%) translateY(${(1 - s) * 40}px)`}}>{el}</div>
  );
};

const EndCard: React.FC<Props['endCard'] & {design: Design}> = ({appName, cta, icon, bg, design}) => {
  const f = useCurrentFrame();
  const {fps} = useVideoConfig();
  const s = spring({frame: f, fps, config: {damping: 12, stiffness: 150}});
  const t = spring({frame: f - 6, fps, config: {damping: 14, stiffness: 150}});
  return (
    <AbsoluteFill>
      <Img src={staticFile(bg)} style={Cover} />
      {icon && <Img src={staticFile(icon)} style={{position: 'absolute', top: '30%', left: 380, width: 320, height: 320,
                                                  transform: `scale(${0.6 + 0.4 * s})`, opacity: s}} />}
      <div style={{position: 'absolute', top: '57%', left: 60, right: 60, textAlign: 'center', opacity: t,
                   transform: `translateY(${(1 - t) * 40}px)`}}>
        <div style={{fontFamily: stack(design), fontWeight: design.weight, fontSize: 96, color: '#fff',
                     WebkitTextStroke: '8px #000', paintOrder: 'stroke fill'}}>{appName}</div>
        <div style={{fontFamily: stack(design), fontWeight: design.weight, fontSize: 62, color: '#fff', marginTop: 40,
                     WebkitTextStroke: '6px #000', paintOrder: 'stroke fill'}}>{cta}</div>
      </div>
    </AbsoluteFill>
  );
};

const AiLabel: React.FC<{text: string}> = ({text}) => (
  <div style={{position: 'absolute', top: 60, right: 40, fontFamily: `'${LATIN}'`, fontWeight: 700, fontSize: 34,
               color: 'rgba(255,255,255,.82)', background: 'rgba(0,0,0,.55)', padding: '6px 16px', borderRadius: 6}}>
    {text}
  </div>
);

// ---------------------------------------------------------------- composition

export const UgcVideo: React.FC<Props> = (p) => {
  const fontsReady = useFonts(p.fonts);
  const {fps, durationInFrames} = useVideoConfig();
  const endFrom = Math.round(p.endCard.startS * fps);
  const fade = (f: number) =>
    interpolate(f, [0, fps, durationInFrames - 1.5 * fps, durationInFrames], [0, 1, 1, 0], {
      extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
    });
  if (!fontsReady) return null;
  return (
    <AbsoluteFill style={{background: '#000'}}>
      {p.segments.map((s, i) => (
        <Sequence key={i} from={s.from} durationInFrames={s.frames}>
          <SegmentView seg={s} fps={fps} transition={p.design.transition} design={p.design}
                       accent={p.hook.look.highlight} volume={p.voiceVolume} />
        </Sequence>
      ))}
      {(p.headlines || []).map((h, i) => (
        <Sequence key={`h${i}`} from={h.from} durationInFrames={h.frames}>
          <HeadlineView h={h} design={p.design} accent={p.hook.look.highlight} />
        </Sequence>
      ))}
      <Sequence from={endFrom}>
        <EndCard {...p.endCard} design={p.design} />
      </Sequence>
      <Captions lines={p.captionLines} design={p.design} />
      {p.hook.text && (
        <Sequence from={0} durationInFrames={Math.round(p.hook.seconds * fps)}>
          <Hook text={p.hook.text} seconds={p.hook.seconds} look={p.hook.look} design={p.design} />
        </Sequence>
      )}
      {p.aiLabel ? <AiLabel text={p.aiLabel} /> : null}
      {p.voice.map((v, i) => (
        <Sequence key={i} from={v.from}><Audio src={staticFile(v.src)} volume={p.voiceVolume} /></Sequence>
      ))}
      {p.music && <Audio src={staticFile(p.music.src)} loop volume={(f) => p.music!.volume * fade(f)} />}
    </AbsoluteFill>
  );
};
