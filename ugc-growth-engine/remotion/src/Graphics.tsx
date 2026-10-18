import React from 'react';
import {AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import {fitText} from '@remotion/layout-utils';
import type {Design, GraphicSpec} from './types';

type P = {spec: GraphicSpec; design: Design; accent: string; stack: string};

const hex = (c: string) => `#${c.replace('#', '')}`;

// Dark background tinted with the video's accent, with a slow drifting glow — so graphics feel part of the video.
const Backdrop: React.FC<{accent: string}> = ({accent}) => {
  const f = useCurrentFrame();
  const x = 30 + 20 * Math.sin(f / 40);
  return (
    <AbsoluteFill style={{background: `radial-gradient(circle at ${x}% 30%, ${hex(accent)}55 0%, #0d0f14 58%)`}} />
  );
};

const fit = (text: string, width: number, stack: string, weight: number, max: number, min: number) =>
  Math.round(Math.max(min, Math.min(max, fitText({text, withinWidth: width, fontFamily: stack, fontWeight: weight}).fontSize)));

export const Graphic: React.FC<P> = ({spec, design, accent, stack}) => {
  const f = useCurrentFrame();
  const {fps} = useVideoConfig();
  const items = (spec.items || []).filter(Boolean).slice(0, 4);
  const base: React.CSSProperties = {fontFamily: stack, fontWeight: design.weight, color: '#fff'};
  const inAt = (i: number) => spring({frame: f - 6 - i * 9, fps, config: {damping: 14, stiffness: 160}});

  if (spec.type === 'number' && items.length) {
    const s = inAt(0);
    return (
      <AbsoluteFill>
        <Backdrop accent={accent} />
        <AbsoluteFill style={{alignItems: 'center', justifyContent: 'center', padding: '120px 90px 460px', textAlign: 'center'}}>
          <div style={{...base, fontSize: fit(items[0], 860, stack, design.weight, 300, 120), color: hex(accent),
                       transform: `scale(${0.6 + 0.4 * s})`, opacity: s}}>{items[0]}</div>
          {items[1] && <div style={{...base, fontSize: fit(items[1], 860, stack, design.weight, 84, 44), marginTop: 30,
                                    opacity: inAt(1)}}>{items[1]}</div>}
        </AbsoluteFill>
      </AbsoluteFill>
    );
  }
  if (spec.type === 'compare' && items.length >= 2) {
    const rows = [{t: items[0], mark: '✕', c: '#ff5a5f'}, {t: items[1], mark: '✓', c: hex(accent)}];
    return (
      <AbsoluteFill>
        <Backdrop accent={accent} />
        <AbsoluteFill style={{justifyContent: 'center', padding: '120px 80px 460px', gap: 60}}>
          {rows.map((r, i) => {
            const s = inAt(i * 2);
            return (
              <div key={i} style={{display: 'flex', alignItems: 'center', gap: 36, opacity: s,
                                   transform: `translateX(${(1 - s) * (i ? 120 : -120)}px)`}}>
                <div style={{width: 130, height: 130, borderRadius: 30, background: r.c, color: '#111', fontSize: 88,
                             display: 'flex', alignItems: 'center', justifyContent: 'center', fontWeight: 900,
                             flexShrink: 0}}>{r.mark}</div>
                <div style={{...base, fontSize: fit(r.t, 1100, stack, design.weight, 92, 62), lineHeight: 1.15,
                             opacity: i ? 1 : 0.8, textDecoration: i ? 'none' : 'line-through',
                             textDecorationColor: '#ff5a5f'}}>{r.t}</div>
              </div>
            );
          })}
        </AbsoluteFill>
      </AbsoluteFill>
    );
  }
  if (spec.type === 'steps' && items.length > 1) {
    return (
      <AbsoluteFill>
        <Backdrop accent={accent} />
        <AbsoluteFill style={{justifyContent: 'center', padding: '120px 90px 460px', gap: 44}}>
          {items.map((t, i) => {
            const s = inAt(i);
            return (
              <div key={i} style={{display: 'flex', alignItems: 'center', gap: 34, opacity: s,
                                   transform: `translateY(${(1 - s) * 60}px)`}}>
                <div style={{...base, width: 110, height: 110, borderRadius: 60, background: hex(accent), color: '#111',
                             fontSize: 64, display: 'flex', alignItems: 'center', justifyContent: 'center',
                             flexShrink: 0}}>{i + 1}</div>
                <div style={{...base, fontSize: fit(t, 1000, stack, design.weight, 84, 60), lineHeight: 1.15}}>{t}</div>
              </div>
            );
          })}
        </AbsoluteFill>
      </AbsoluteFill>
    );
  }
  // card: one strong line
  const text = items[0] || '';
  const s = inAt(0);
  const drift = interpolate(f, [0, 90], [1, 1.05], {extrapolateRight: 'clamp'});
  return (
    <AbsoluteFill>
      <Backdrop accent={accent} />
      <AbsoluteFill style={{alignItems: 'center', justifyContent: 'center', padding: '120px 90px 460px', textAlign: 'center'}}>
        <div style={{...base, fontSize: fit(text, 860, stack, design.weight, 150, 64), lineHeight: 1.12, opacity: s,
                     transform: `scale(${drift})`}}>
          <span style={{boxShadow: `inset 0 -0.34em 0 ${hex(accent)}aa`}}>{text}</span>
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
