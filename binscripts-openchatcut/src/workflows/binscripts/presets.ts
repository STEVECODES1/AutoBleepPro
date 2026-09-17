import type { SafeAreaInsets } from '../../reframe/safe-focus.js';

export type BinScriptsPlatformPresetId =
  | 'youtube-long'
  | 'youtube-shorts'
  | 'rumble-regular'
  | 'rumble-shorts';

export interface BinScriptsExportPreset {
  id: BinScriptsPlatformPresetId;
  platform: 'youtube' | 'rumble';
  format: 'long-form' | 'shorts';
  canvas: { width: number; height: number; aspectRatio: '16:9' | '9:16' };
  export: {
    container: 'mp4';
    videoCodec: 'h264';
    progressive: true;
    audioCodec: 'aac';
    audioSampleRateHz: 48_000;
  };
  /** Conservative project action-safe area, not a platform UI guarantee. */
  safeArea: SafeAreaInsets;
  classification: { maximumDurationSeconds?: number; minimumAspectRatio?: '1:1' };
  notes: readonly string[];
}

const ACTION_SAFE: SafeAreaInsets = { top: 0.05, right: 0.05, bottom: 0.05, left: 0.05 };
const DELIVERY = {
  container: 'mp4' as const,
  videoCodec: 'h264' as const,
  progressive: true as const,
  audioCodec: 'aac' as const,
  audioSampleRateHz: 48_000 as const,
};

export const BINSCRIPTS_PLATFORM_PRESETS: Readonly<Record<BinScriptsPlatformPresetId, BinScriptsExportPreset>> = {
  'youtube-long': {
    id: 'youtube-long', platform: 'youtube', format: 'long-form',
    canvas: { width: 1920, height: 1080, aspectRatio: '16:9' },
    export: DELIVERY, safeArea: ACTION_SAFE, classification: {},
    notes: ['Standard desktop delivery uses 16:9 at 1920×1080.'],
  },
  'youtube-shorts': {
    id: 'youtube-shorts', platform: 'youtube', format: 'shorts',
    canvas: { width: 1080, height: 1920, aspectRatio: '9:16' },
    export: DELIVERY, safeArea: ACTION_SAFE,
    classification: { maximumDurationSeconds: 180, minimumAspectRatio: '1:1' },
    notes: ['Square or vertical uploads up to three minutes classify as Shorts; this preset chooses 9:16.'],
  },
  'rumble-regular': {
    id: 'rumble-regular', platform: 'rumble', format: 'long-form',
    canvas: { width: 1920, height: 1080, aspectRatio: '16:9' },
    export: DELIVERY, safeArea: ACTION_SAFE, classification: {},
    notes: ['Keep regular Rumble exports at 16:9.'],
  },
  'rumble-shorts': {
    id: 'rumble-shorts', platform: 'rumble', format: 'shorts',
    canvas: { width: 1080, height: 1920, aspectRatio: '9:16' },
    export: DELIVERY, safeArea: ACTION_SAFE,
    classification: { maximumDurationSeconds: 180, minimumAspectRatio: '1:1' },
    notes: ['Rumble Shorts are at most 180 seconds and 1:1 or taller; 9:16 is recommended.', '720×1280 is the documented minimum frame/thumbnail guidance; this preset exports 1080×1920.'],
  },
};

export function binScriptsPlatformPreset(id: BinScriptsPlatformPresetId): BinScriptsExportPreset {
  return BINSCRIPTS_PLATFORM_PRESETS[id];
}
