import type { AtomicAction } from '../../editor/reducerActions.js';
import type { MediaAsset, TimelineItem, TimelineState } from '../../editor/types.js';
import type {
  BinScriptsCompileWarning,
  BinScriptsEditPlanV1,
  FrameRange,
  ResolvedCueAsset,
} from './types.js';
import { binScriptsPlatformPreset, type BinScriptsExportPreset } from './presets.js';

export interface BinScriptsCompileResult {
  actions: AtomicAction[];
  warnings: BinScriptsCompileWarning[];
  exportPreset?: BinScriptsExportPreset;
}

export class BinScriptsCompileError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'BinScriptsCompileError';
  }
}

const finiteInt = (value: number, label: string): number => {
  if (!Number.isFinite(value)) throw new BinScriptsCompileError(`${label} must be finite`);
  return Math.round(value);
};

const cueRange = (
  range: FrameRange,
  cueId: string,
  timelineStart: number,
  timelineEnd: number,
  warnings: BinScriptsCompileWarning[],
) => {
  const rawStart = finiteInt(range.startFrame, `${cueId}.startFrame`);
  const rawEnd = finiteInt(range.endFrame, `${cueId}.endFrame`);
  if (rawEnd <= rawStart) throw new BinScriptsCompileError(`${cueId}.endFrame must be after startFrame`);
  const startFrame = Math.max(timelineStart, Math.min(timelineEnd - 1, rawStart));
  const endFrame = Math.max(startFrame + 1, Math.min(timelineEnd, rawEnd));
  if (startFrame !== rawStart || endFrame !== rawEnd) warnings.push({
    cueId,
    code: 'cue-clamped',
    message: `Cue ${cueId} was clamped to the source clip bounds.`,
  });
  return { startFrame, endFrame };
};

const resolveAsset = (
  timeline: TimelineState,
  ref: ResolvedCueAsset,
  allowedKinds: MediaAsset['kind'][],
): MediaAsset => {
  const asset = timeline.assets?.find((candidate) => candidate.id === ref.assetId);
  if (!asset) throw new BinScriptsCompileError(`Resolved asset ${ref.assetId} is not in the project media pool`);
  if (!allowedKinds.includes(asset.kind)) {
    throw new BinScriptsCompileError(`Asset ${ref.assetId} has incompatible kind ${asset.kind}`);
  }
  return asset;
};

const mediaItem = (
  cueId: string,
  track: string,
  durationInFrames: number,
  asset: MediaAsset,
  name: string,
): Omit<TimelineItem, 'startFrame'> => ({
  id: `binscripts:${cueId}`,
  track,
  durationInFrames,
  name,
  kind: asset.kind as TimelineItem['kind'],
  src: asset.src,
  sourceAssetId: asset.id,
  sourceRevision: asset.sourceRevision,
  sourceContentHash: asset.sourceContentHash,
  srcInFrame: 0,
});

/** Compile one reviewable, undo-groupable plan into existing editor reducer actions. */
export function compileBinScriptsEditPlan(
  plan: BinScriptsEditPlanV1,
  timeline: TimelineState,
): BinScriptsCompileResult {
  if (plan.version !== 1) throw new BinScriptsCompileError('Unsupported BinScripts edit-plan version');
  const source = timeline.items.find((item) => item.id === plan.sourceItemId);
  if (!source) throw new BinScriptsCompileError(`Source item ${plan.sourceItemId} was not found`);
  if (source.kind !== 'audio' && source.kind !== 'video') {
    throw new BinScriptsCompileError('BinScripts source item must be audio or video');
  }

  const actions: AtomicAction[] = [];
  const warnings: BinScriptsCompileWarning[] = [];
  const timelineEnd = source.startFrame + source.durationInFrames;
  const exportPreset = plan.targetPreset ? binScriptsPlatformPreset(plan.targetPreset) : undefined;
  if (exportPreset) actions.push({ type: 'setCanvas', width: exportPreset.canvas.width, height: exportPreset.canvas.height, fit: 'cover' });

  const cueIds = [
    ...(plan.cuts ?? []), ...(plan.bleeps ?? []), ...(plan.reactions ?? []),
    ...(plan.zooms ?? []), ...(plan.soundEffects ?? []),
  ].map((cue) => cue.id);
  if (new Set(cueIds).size !== cueIds.length) throw new BinScriptsCompileError('Cue ids must be unique across the edit plan');

  const cutIndices = new Set<number>();
  for (const cue of [...(plan.cuts ?? [])].sort((a, b) => a.id.localeCompare(b.id))) {
    for (const rawIndex of cue.wordIndices) {
      const index = finiteInt(rawIndex, `${cue.id}.wordIndices`);
      if (!source.transcript || index < 0 || index >= source.transcript.length) {
        throw new BinScriptsCompileError(`${cue.id} references missing transcript word ${index}`);
      }
      if (cutIndices.has(index)) warnings.push({
        cueId: cue.id,
        code: 'duplicate-word-index',
        message: `Transcript word ${index} was already selected by another cut cue.`,
      });
      cutIndices.add(index);
    }
  }
  if (cutIndices.size) actions.push({ type: 'deleteWords', id: source.id, idxs: [...cutIndices].sort((a, b) => a - b) });

  for (const cue of [...(plan.bleeps ?? [])].sort((a, b) => a.startFrame - b.startFrame || a.id.localeCompare(b.id))) {
    const range = cueRange(cue, cue.id, source.startFrame, timelineEnd, warnings);
    const asset = resolveAsset(timeline, cue.asset, ['audio']);
    const localStart = range.startFrame - source.startFrame;
    const localEnd = range.endFrame - source.startFrame;
    const baseVolume = source.volume ?? 1;
    actions.push(
      { type: 'setKeyframe', id: source.id, prop: 'volume', frame: Math.max(0, localStart - 1), value: baseVolume },
      { type: 'setKeyframe', id: source.id, prop: 'volume', frame: localStart, value: 0 },
      { type: 'setKeyframe', id: source.id, prop: 'volume', frame: Math.max(localStart, localEnd - 1), value: 0 },
      { type: 'setKeyframe', id: source.id, prop: 'volume', frame: localEnd, value: baseVolume },
      { type: 'add', startFrame: range.startFrame, item: mediaItem(cue.id, cue.track, range.endFrame - range.startFrame, asset, cue.asset.name) },
    );
  }

  for (const cue of [...(plan.reactions ?? [])].sort((a, b) => a.startFrame - b.startFrame || a.id.localeCompare(b.id))) {
    const range = cueRange(cue, cue.id, source.startFrame, timelineEnd, warnings);
    const asset = resolveAsset(timeline, cue.asset, ['video', 'image', 'gif']);
    actions.push({
      type: 'add',
      startFrame: range.startFrame,
      item: { ...mediaItem(cue.id, cue.track, range.endFrame - range.startFrame, asset, cue.asset.name), transform: cue.transform },
    });
  }

  if (plan.captions) actions.push({
    type: 'setCaptions',
    track: plan.captions.track,
    captions: {
      enabled: true,
      sourceItemId: source.id,
      template: plan.captions.template ?? 'bold-outline',
      pacing: plan.captions.pacing ?? 'phrase',
      motionPreset: plan.captions.motionPreset ?? 'word-pop',
    },
  });

  for (const cue of [...(plan.zooms ?? [])].sort((a, b) => a.startFrame - b.startFrame || a.id.localeCompare(b.id))) {
    const range = cueRange(cue, cue.id, source.startFrame, timelineEnd, warnings);
    if (!Number.isFinite(cue.magnification) || cue.magnification < 1 || cue.magnification > 16) {
      throw new BinScriptsCompileError(`${cue.id}.magnification must be between 1 and 16`);
    }
    const localStart = range.startFrame - source.startFrame;
    const localEnd = range.endFrame - source.startFrame;
    const middle = Math.round((localStart + localEnd) / 2);
    const focalPointX = cue.focalPointX ?? 0.5;
    const focalPointY = cue.focalPointY ?? 0.5;
    if (![focalPointX, focalPointY].every((value) => Number.isFinite(value) && value >= 0 && value <= 1)) {
      throw new BinScriptsCompileError(`${cue.id} focal points must be between 0 and 1`);
    }
    actions.push(
      { type: 'reframeKeyframe', id: source.id, frame: localStart, focalPointX, focalPointY, magnification: 1 },
      { type: 'reframeKeyframe', id: source.id, frame: middle, focalPointX, focalPointY, magnification: cue.magnification },
      { type: 'reframeKeyframe', id: source.id, frame: localEnd, focalPointX, focalPointY, magnification: 1 },
    );
  }

  for (const cue of [...(plan.soundEffects ?? [])].sort((a, b) => a.atFrame - b.atFrame || a.id.localeCompare(b.id))) {
    const asset = resolveAsset(timeline, cue.asset, ['audio']);
    const rawStart = finiteInt(cue.atFrame, `${cue.id}.atFrame`);
    const startFrame = Math.max(source.startFrame, Math.min(timelineEnd - 1, rawStart));
    if (startFrame !== rawStart) warnings.push({
      cueId: cue.id,
      code: 'cue-clamped',
      message: `Cue ${cue.id} was clamped to the source clip bounds.`,
    });
    const durationInFrames = Math.max(1, finiteInt(cue.durationInFrames ?? asset.durationInFrames, `${cue.id}.durationInFrames`));
    actions.push({
      type: 'add',
      startFrame,
      item: { ...mediaItem(cue.id, cue.track, durationInFrames, asset, cue.asset.name), volume: cue.volume ?? 1 },
    });
  }

  return { actions, warnings, exportPreset };
}
