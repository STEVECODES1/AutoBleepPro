import type { CaptionMotionPreset, CaptionPacing, CaptionTemplate } from '../../captions/types.js';
import type { TrackId } from '../../editor/types.js';
import type { BinScriptsPlatformPresetId } from './presets.js';
import type { BinScriptsAssetRequest } from './resolvers.js';

export interface FrameRange {
  startFrame: number;
  endFrame: number;
}

export interface ResolvedCueAsset {
  assetId: string;
  /** Human-readable label used for the created timeline item. */
  name: string;
}

export interface TranscriptCutCue {
  id: string;
  wordIndices: number[];
}

export interface BleepCue<Asset = ResolvedCueAsset> extends FrameRange {
  id: string;
  asset: Asset;
  track: TrackId;
}

export interface ReactionCue<Asset = ResolvedCueAsset> extends FrameRange {
  id: string;
  asset: Asset;
  track: TrackId;
  transform?: { scale?: number; x?: number; y?: number; opacity?: number };
}

export interface SoundEffectCue<Asset = ResolvedCueAsset> {
  id: string;
  atFrame: number;
  durationInFrames?: number;
  asset: Asset;
  track: TrackId;
  volume?: number;
}

export interface ZoomCue extends FrameRange {
  id: string;
  magnification: number;
  focalPointX?: number;
  focalPointY?: number;
}

export interface CaptionCue {
  track?: TrackId;
  template?: CaptionTemplate;
  pacing?: CaptionPacing;
  motionPreset?: CaptionMotionPreset;
}

/** Provider-neutral plan. External systems resolve media before compilation. */
export interface BinScriptsEditPlanV1<Asset = ResolvedCueAsset> {
  version: 1;
  sourceItemId: string;
  targetPreset?: BinScriptsPlatformPresetId;
  cuts?: TranscriptCutCue[];
  bleeps?: BleepCue<Asset>[];
  reactions?: ReactionCue<Asset>[];
  captions?: CaptionCue;
  zooms?: ZoomCue[];
  soundEffects?: SoundEffectCue<Asset>[];
}

/** Pre-compilation plan whose media references still need host resolution. */
export type BinScriptsDraftEditPlanV1 = BinScriptsEditPlanV1<BinScriptsAssetRequest>;

export interface BinScriptsCompileWarning {
  cueId: string;
  code: 'duplicate-word-index' | 'cue-clamped';
  message: string;
}
