// Usage: node render.mjs <job.json>
// job.json: {props, publicDir, out, thumb, concurrency, crf}
// Bundles the composition with the job's assets as the public dir, renders the MP4 and a still thumbnail.
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {bundle} from '@remotion/bundler';
import {renderMedia, renderStill, selectComposition} from '@remotion/renderer';

const here = path.dirname(fileURLToPath(import.meta.url));
const job = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const props = JSON.parse(fs.readFileSync(job.props, 'utf8'));

const serveUrl = await bundle({entryPoint: path.join(here, 'src/index.ts'), publicDir: job.publicDir});
const composition = await selectComposition({serveUrl, id: 'Ugc', inputProps: props});
await renderMedia({
  composition, serveUrl, inputProps: props, codec: 'h264', outputLocation: job.out,
  crf: job.crf ?? 23, audioBitrate: '128k', concurrency: job.concurrency ?? 2, pixelFormat: 'yuv420p',
  onProgress: ({progress}) => process.stdout.write(`progress ${(progress * 100).toFixed(0)}\n`),
});
if (props.cover) {
  const coverProps = {...props.cover};
  const cover = await selectComposition({serveUrl, id: 'Cover', inputProps: coverProps});
  await renderStill({composition: cover, serveUrl, inputProps: coverProps, output: job.thumb, imageFormat: 'jpeg', jpegQuality: 90});
} else {
  await renderStill({composition, serveUrl, inputProps: props, output: job.thumb, frame: props.hookFrame, imageFormat: 'jpeg', jpegQuality: 88});
}
console.log('done');
