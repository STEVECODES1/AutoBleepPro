import assert from 'node:assert/strict';
import type { TimelineState } from '../../editor/types';
import { compileBinScriptsEditPlan } from './compiler';
import { BINSCRIPTS_PLATFORM_PRESETS } from './presets';
import { resolveBinScriptsEditPlan } from './resolve-plan';
import { createAutoBleepProResolver, projectAssetResolver } from './resolvers';
import type { BinScriptsDraftEditPlanV1, BinScriptsEditPlanV1 } from './types';

const state: TimelineState = {
  fps: 30,
  width: 1920,
  height: 1080,
  selectedId: null,
  assets: [
    { id: 'bleep', name: 'Bleep', kind: 'audio', src: '/media/uploads/bleep.wav', durationInFrames: 15 },
    { id: 'reaction', name: 'Reaction', kind: 'image', src: '/media/uploads/reaction.png', durationInFrames: 300 },
    { id: 'sfx', name: 'Pop', kind: 'audio', src: '/media/uploads/pop.wav', durationInFrames: 12 },
  ],
  items: [{
    id: 'source', track: 'V1', startFrame: 100, durationInFrames: 300, name: 'Monkey App source', kind: 'video',
    src: '/media/uploads/source.mp4', volume: 0.8,
    transcript: [
      { text: 'keep', start: 0, end: 100 },
      { text: 'cut', start: 100, end: 200 },
      { text: 'also', start: 200, end: 300 },
    ],
  }],
};

const plan: BinScriptsEditPlanV1 = {
  version: 1,
  sourceItemId: 'source',
  targetPreset: 'youtube-shorts',
  cuts: [{ id: 'cut-1', wordIndices: [2, 1] }],
  bleeps: [{ id: 'bleep-1', startFrame: 50, endFrame: 130, track: 'A2', asset: { assetId: 'bleep', name: 'Bleep' } }],
  reactions: [{ id: 'reaction-1', startFrame: 200, endFrame: 240, track: 'V2', asset: { assetId: 'reaction', name: 'Reaction' } }],
  captions: {},
  zooms: [{ id: 'zoom-1', startFrame: 220, endFrame: 280, magnification: 1.4, focalPointX: 0.78, focalPointY: 0.35 }],
  soundEffects: [{ id: 'sfx-1', atFrame: 999, track: 'A3', asset: { assetId: 'sfx', name: 'Pop' } }],
};

const first = compileBinScriptsEditPlan(plan, state);
const second = compileBinScriptsEditPlan(plan, state);
assert.deepEqual(first, second, 'compiler output is deterministic');
assert.deepEqual(first.actions[0], { type: 'setCanvas', width: 1080, height: 1920, fit: 'cover' });
assert.deepEqual(first.actions[1], { type: 'deleteWords', id: 'source', idxs: [1, 2] });
assert.ok(first.actions.some((action) => action.type === 'setCaptions'), 'caption cue uses the existing caption action');
assert.equal(first.actions.filter((action) => action.type === 'reframeKeyframe').length, 3, 'zoom cue uses sparse reframe actions');
assert.equal(first.warnings.filter((warning) => warning.code === 'cue-clamped').length, 2, 'out-of-source bleep and SFX are reported');
assert.equal(first.exportPreset?.id, 'youtube-shorts');

assert.throws(() => compileBinScriptsEditPlan({ ...plan, reactions: [{ ...plan.reactions![0]!, id: 'zoom-1' }] }, state),
  /Cue ids must be unique/, 'timeline item ids cannot collide across cue categories');

const draft: BinScriptsDraftEditPlanV1 = {
  version: 1,
  sourceItemId: 'source',
  bleeps: [{
    id: 'profanity-1', startFrame: 120, endFrame: 130, track: 'A2',
    asset: { kind: 'auto-bleep-pro', name: 'AutoBleepPro clean bleep', preset: 'default' },
  }],
  soundEffects: [{
    id: 'sfx-project', atFrame: 140, track: 'A3',
    asset: { kind: 'project-asset', assetId: 'sfx', name: 'Pop' },
  }],
};
const resolved = await resolveBinScriptsEditPlan(draft, [
  projectAssetResolver,
  createAutoBleepProResolver({ resolveImportedAsset: async () => ({ assetId: 'bleep', name: 'Imported AutoBleepPro bleep' }) }),
]);
assert.equal(resolved.bleeps![0]!.asset.assetId, 'bleep');
assert.equal(resolved.soundEffects![0]!.asset.assetId, 'sfx');

assert.deepEqual(
  Object.fromEntries(Object.entries(BINSCRIPTS_PLATFORM_PRESETS).map(([id, preset]) => [id, `${preset.canvas.width}x${preset.canvas.height}`])),
  { 'youtube-long': '1920x1080', 'youtube-shorts': '1080x1920', 'rumble-regular': '1920x1080', 'rumble-shorts': '1080x1920' },
);
assert.equal(BINSCRIPTS_PLATFORM_PRESETS['rumble-shorts'].classification.maximumDurationSeconds, 180);

console.log('binscripts.verify: all assertions passed');
