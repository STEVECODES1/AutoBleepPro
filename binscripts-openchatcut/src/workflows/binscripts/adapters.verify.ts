import assert from 'node:assert/strict';
import { buildAutoBleepArgs, parseAutoBleepCliOutput } from './autobleep-cli.js';
import { extractFalMediaUrl, generateFalMedia } from './fal-client.js';
import { BinScriptsAssetResolutionError } from './resolvers.js';

// AutoBleepPro CLI stdout parsing (progress → stderr, paths → stdout).
assert.deepEqual(
  parseAutoBleepCliOutput('/tmp/out/stream_CLEAN.mp4\n/tmp/out/stream_CLEAN.srt\n/tmp/out/stream_CLEAN.txt\n'),
  {
    outputVideo: '/tmp/out/stream_CLEAN.mp4',
    srt: '/tmp/out/stream_CLEAN.srt',
    txt: '/tmp/out/stream_CLEAN.txt',
  },
  'parses video + SRT + TXT output paths from stdout',
);
assert.deepEqual(parseAutoBleepCliOutput('loading model…\n/out/a_CLEAN.srt\n'), {
  outputVideo: undefined,
  srt: '/out/a_CLEAN.srt',
  txt: undefined,
}, 'ignores non-path lines and missing artifacts');

assert.deepEqual(
  buildAutoBleepArgs('in.mp4', { outputDir: 'out', srt: true, txt: true, method: 'beep', sensitivity: 40, customWords: ['brand1'], model: 'base', trimSilence: true }),
  ['in.mp4', '-o', 'out', '--method', 'beep', '--srt', '--txt', '--sensitivity', '40', '--custom-words', 'brand1', '--model', 'base', '--trim-silence'],
  'builds the documented CLI argument order',
);
assert.deepEqual(buildAutoBleepArgs('in.mp4', { outputDir: 'out' }), ['in.mp4', '-o', 'out'], 'minimal args');

// fal.ai response URL extraction across media shapes.
assert.equal(extractFalMediaUrl({ images: [{ url: 'https://fal/x.jpg' }] }, 'image'), 'https://fal/x.jpg');
assert.equal(extractFalMediaUrl({ audio: { url: 'https://fal/x.mp3' } }, 'audio'), 'https://fal/x.mp3');
assert.equal(extractFalMediaUrl({ video: { url: 'https://fal/x.mp4' } }, 'video'), 'https://fal/x.mp4');
assert.equal(extractFalMediaUrl({ url: 'https://fal/z.jpg' }, 'image'), 'https://fal/z.jpg');
assert.equal(extractFalMediaUrl({ images: [] }, 'image'), null, 'empty images returns null');
assert.equal(extractFalMediaUrl(null, 'image'), null, 'null payload returns null');

// fal client uses the injectable fetch and correct auth header.
const seen: { url: string; headers: Record<string, string> }[] = [];
const fakeFetch = (async (url: string, init: RequestInit) => {
  seen.push({ url: String(url), headers: (init.headers ?? {}) as Record<string, string> });
  return new Response(JSON.stringify({ images: [{ url: 'https://fal/gen.jpg' }] }), { status: 200 });
}) as typeof fetch;
const generated = await generateFalMedia({
  apiKey: 'k:s',
  model: 'fal-ai/flux/schnell',
  prompt: 'test',
  mediaKind: 'image',
  requestName: 'thumb',
  baseUrl: 'https://fal.run',
  fetchImpl: fakeFetch,
});
assert.equal(generated.url, 'https://fal/gen.jpg');
assert.equal(seen[0]!.url, 'https://fal.run/fal-ai/flux/schnell');
assert.equal(seen[0]!.headers['Authorization'], 'Key k:s');

// Non-OK responses surface as a typed resolution error.
const failingFetch = (async () => new Response('{"detail":"User is locked. Reason: TOP_UP."}', { status: 402 })) as typeof fetch;
await assert.rejects(
  () => generateFalMedia({ apiKey: 'k:s', model: 'm', prompt: 'p', mediaKind: 'image', requestName: 'x', fetchImpl: failingFetch }),
  BinScriptsAssetResolutionError,
);

console.log('binscripts adapters: OK');
