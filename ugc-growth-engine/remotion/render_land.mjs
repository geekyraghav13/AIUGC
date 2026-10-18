import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {bundle} from '@remotion/bundler';
import {renderMedia, renderStill, selectComposition} from '@remotion/renderer';
const here = path.dirname(fileURLToPath(import.meta.url));
const props = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const out = process.argv[3];
const serveUrl = await bundle({entryPoint: path.join(here, 'src/index.ts'), publicDir: path.join(here, 'public')});
const composition = await selectComposition({serveUrl, id: 'Landscape', inputProps: props});
if (process.argv[4] === 'still') {
  await renderStill({composition, serveUrl, inputProps: props, output: out, frame: Number(process.argv[5] ?? 30), imageFormat: 'jpeg'});
} else {
  await renderMedia({composition, serveUrl, inputProps: props, codec: 'h264', outputLocation: out, crf: 20, audioBitrate: '192k', concurrency: 4, pixelFormat: 'yuv420p',
    onProgress: ({progress}) => process.stdout.write(`progress ${(progress * 100).toFixed(0)}\n`)});
}
console.log('done');
