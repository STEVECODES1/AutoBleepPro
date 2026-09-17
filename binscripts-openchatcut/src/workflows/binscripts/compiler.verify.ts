import assert from 'node:assert/strict';
import { reduce } from '../../editor/reducerTimeline.js';
import type { TimelineState } from '../../editor/types.js';
import { BinScriptsCompileError, compileBinScriptsEditPlan } from './compiler.js';
import type { BinScriptsEditPlanV1 } from './types.js';

const timeline: TimelineState = {
  fps: 30,
  width: 1920,
  height: 1080,
  selectedId: null,
  items: [{
    id: 'gameplay',
    track: 'V1',
    startFrame: 10,
    durationInFrames: 150,
    name: 'Monkey session',
    kind: 'video',
    src: '/media/gameplay.mp4',
    volume: 0.8,
    transcript: [
      { text: 'this', start: 0, end: 1000 },
      { text: 'um', start: 1000, end: 2000 },
      { text: 'works', start: 2000, end: 5000 },
    ],
  }],
  assets: [
    { id: 'bleep', name: 'AutoBleepPro output', kind: 'audio', src: '/media/bleep.wav', durationInFrames: 30 },
    { id: 'reaction', name: 'Reaction', kind: 'image', src: '/media/reaction.png', durationInFrames: 90 },
    { id: 'whoosh', name: 'Whoosh', kind: 'audio', src: '/media/whoosh.wav', durationInFrames: 18 },
  ],
};

const plan: BinScriptsEditPlanV1 = {
  version: 1,
  sourceItemId: 'gameplay',
  cuts: [{ id: 'remove-filler', wordIndices: [1] }],
  bleeps: [{ id: 'bleep-1', startFrame: 30, endFrame: 38, track: 'A2', asset: { assetId: 'bleep', name: 'Bleep' } }],
  reactions: [{ id: 'reaction-1', startFrame: 45, endFrame: 60, track: 'V2', asset: { assetId: 'reaction', name: 'Reaction' }, transform: { scale: 0.35, x: 25, y: 20 } }],
  captions: { track: 'C1' },
  zooms: [{ id: 'zoom-1', startFrame: 70, endFrame: 90, magnification: 1.4 }],
  soundEffects: [{ id: 'whoosh-1', atFrame: 100, track: 'A1', asset: { assetId: 'whoosh', name: 'Whoosh' }, volume: 0.7 }],
};

const compiled = compileBinScriptsEditPlan(plan, timeline);
assert.equal(compiled.warnings.length, 0);
assert.deepEqual(compiled.actions[0], { type: 'deleteWords', id: 'gameplay', idxs: [1] });
assert.equal(compiled.actions.filter((action) => action.type === 'setKeyframe').length, 4);
assert.equal(compiled.actions.filter((action) => action.type === 'reframeKeyframe').length, 3);
assert.equal(compiled.actions.filter((action) => action.type === 'add').length, 3);
assert.equal(compiled.actions.filter((action) => action.type === 'setCaptions').length, 1);

const applied = compiled.actions.reduce((state, action) => reduce(state, action), timeline);
const source = applied.items.find((item) => item.id === 'gameplay')!;
assert.deepEqual(source.deletedWordIdx, [1]);
assert.deepEqual(source.keyframes?.volume?.map(({ frame, value }) => [frame, value]), [
  [19, 0.8], [20, 0], [27, 0], [28, 0.8],
]);
assert.deepEqual(source.zoom?.reframeCurve?.keyframes.map(({ frame, magnification }) => [frame, magnification]), [
  [60, 1], [70, 1.4], [80, 1],
]);
assert.equal(applied.items.find((item) => item.id === 'binscripts:reaction-1')?.sourceAssetId, 'reaction');
assert.equal(applied.items.find((item) => item.id === 'binscripts:whoosh-1')?.volume, 0.7);
assert.equal(applied.tracks?.C1?.captions?.sourceItemId, 'gameplay');
assert.equal(applied.tracks?.C1?.captions?.template, 'bold-outline');

const deterministic = compileBinScriptsEditPlan({
  ...plan,
  soundEffects: [...plan.soundEffects!].reverse(),
  cuts: [{ id: 'z', wordIndices: [1] }, { id: 'a', wordIndices: [0] }],
}, timeline);
assert.deepEqual(deterministic.actions[0], { type: 'deleteWords', id: 'gameplay', idxs: [0, 1] });

assert.throws(
  () => compileBinScriptsEditPlan({ ...plan, reactions: [{ ...plan.reactions![0]!, asset: { assetId: 'missing', name: 'Missing' } }] }, timeline),
  BinScriptsCompileError,
);

console.log('BinScripts edit-plan compiler: OK');
