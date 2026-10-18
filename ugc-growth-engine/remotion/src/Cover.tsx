import React from 'react';
import {AbsoluteFill, Img, staticFile} from 'remotion';
import {fitText} from '@remotion/layout-utils';
import {hex, readableOn, stack, useFonts} from './UgcVideo';
import type {CoverProps} from './types';

// Split the hook into short stacked lines (2–3 words) so it reads at a glance in a feed.
const toLines = (text: string): string[] => {
  const words = text.trim().split(/\s+/);
  const per = words.length <= 4 ? 2 : 3;
  const lines: string[] = [];
  for (let i = 0; i < words.length; i += per) lines.push(words.slice(i, i + per).join(' '));
  return lines.slice(0, 4);
};

export const Cover: React.FC<CoverProps> = ({bg, hook, appName, icon, accent, design, fonts, aiLabel}) => {
  const ready = useFonts(fonts);
  if (!ready) return null;
  const label = design.caps ? hook.toUpperCase() : hook;
  const lines = toLines(label);
  const font = stack(design);
  const size = Math.round(Math.max(64, Math.min(180, Math.min(
    ...lines.map((l) => fitText({text: l, withinWidth: 730, fontFamily: font, fontWeight: design.weight}).fontSize)))));
  const last = lines.length - 1;
  return (
    <AbsoluteFill style={{background: '#000'}}>
      <Img src={staticFile(bg)} style={{width: '100%', height: '100%', objectFit: 'cover'}} />
      <AbsoluteFill style={{background:
        'linear-gradient(180deg, rgba(0,0,0,.55) 0%, rgba(0,0,0,0) 28%, rgba(0,0,0,0) 40%, rgba(0,0,0,.78) 100%)'}} />
      <div style={{position: 'absolute', left: 70, right: 70, bottom: 380, display: 'flex', flexDirection: 'column',
                   alignItems: 'flex-start', gap: 10}}>
        {lines.map((l, i) => {
          const hot = i === last;
          return (
            <span key={i} style={{
              fontFamily: font, fontWeight: design.weight, fontSize: size, lineHeight: 1.12, padding: '4px 26px',
              borderRadius: 18, color: hot ? hex(readableOn(accent)) : '#fff', background: hot ? hex(accent) : 'transparent',
              WebkitTextStroke: hot ? undefined : '10px #000', paintOrder: 'stroke fill',
              textShadow: hot ? undefined : '0 6px 24px rgba(0,0,0,.6)',
              transform: `rotate(${hot ? -1.5 : 0}deg)`,
            }}>{l}</span>
          );
        })}
      </div>
      <div style={{position: 'absolute', left: 70, bottom: 190, display: 'flex', alignItems: 'center', gap: 24}}>
        {icon && <Img src={staticFile(icon)} style={{width: 110, height: 110, borderRadius: 26}} />}
        <span style={{fontFamily: `'Noto Sans'`, fontWeight: 700, fontSize: 54, color: '#fff',
                      textShadow: '0 4px 16px rgba(0,0,0,.7)'}}>{appName}</span>
      </div>
      {aiLabel ? (
      <div style={{position: 'absolute', top: 60, right: 40, fontFamily: `'Noto Sans'`, fontWeight: 700, fontSize: 34,
                   color: 'rgba(255,255,255,.85)', background: 'rgba(0,0,0,.55)', padding: '6px 16px', borderRadius: 6}}>
        {aiLabel}
      </div>
      ) : null}
    </AbsoluteFill>
  );
};
