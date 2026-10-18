// Shape of the props JSON written by engine/render.py. Keep in sync with build_props() there.
export type Segment =
  | {kind: 'video'; src: string; from: number; frames: number; startS: number; clipS: number; audible?: boolean}
  | {kind: 'graphic'; from: number; frames: number; index: number; graphic: GraphicSpec}
  | {kind: 'image'; src: string; from: number; frames: number; direction: number}
  | {kind: 'screenshot'; src: string; from: number; frames: number}
  | {kind: 'gradient'; src: string; from: number; frames: number; direction: number};

// Motion graphic drawn in the video's colours and fonts (engine: director `motion_graphic`, or a text card).
export type GraphicSpec = {type: 'steps' | 'number' | 'compare' | 'card' | string; items: string[]};

export type Headline = {from: number; frames: number; text: string; y: number};

// Where and in which colours text sits, decided per scene from the footage (engine/textfit.py).
export type Look = {
  y: number; // centre of the text block, px
  band: string;
  primary: string; // hex, no #
  outline: string;
  highlight: string;
  plate: string | null; // css colour of a translucent plate behind the text, if the area is busy
};

export type CaptionWord = {text: string; start: number; end: number};
export type CaptionLine = {start: number; end: number; look: Look; words: CaptionWord[]};

export type Design = {
  family: string; // Latin display font (English/Hinglish words, digits)
  scriptFamily: string; // font for the language's own script (Devanagari, Tamil, ...)
  langFont: string; // Noto font for the video's language (Indic glyphs)
  weight: number;
  anim: 'pop' | 'slide' | 'bounce' | 'pill' | 'fade';
  transition: 'punch' | 'fade';
  caps: boolean;
  size: number; // base caption size, px (already scaled for the font)
  outlinePx: number;
  captionLayout?: 'karaoke' | 'word' | 'boxed' | 'lowerbar';
  headlineStyle?: 'sticker' | 'banner' | 'outline' | 'underline' | 'split';
  hookStyle?: 'card' | 'underline' | 'stacked';
};

export type FontFile = {family: string; file: string; weight: string};

export type Props = {
  fps: number;
  width: number;
  height: number;
  totalFrames: number;
  segments: Segment[];
  headlines?: Headline[];
  voice: {src: string; from: number}[];
  music: {src: string; volume: number} | null;
  captionLines: CaptionLine[];
  design: Design;
  hook: {text: string; seconds: number; look: Look};
  endCard: {startS: number; appName: string; cta: string; icon: string | null; bg: string};
  aiLabel: string;
  fonts: FontFile[];
  hookFrame: number;
  cover?: CoverProps;
  voiceVolume: number; // >1 boosts (there is no loudnorm pass); music volume is set on `music`
};

// Thumbnail / cover image (rendered as a still by render.mjs).
export type CoverProps = {
  bg: string; // staged image, already cropped to 9:16
  hook: string;
  appName: string;
  icon: string | null;
  accent: string; // hex, no #
  design: Design;
  fonts: FontFile[];
  aiLabel: string;
};
