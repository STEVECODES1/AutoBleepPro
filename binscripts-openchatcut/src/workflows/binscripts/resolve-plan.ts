import type { BinScriptsAssetRequest, BinScriptsAssetResolver } from './resolvers.js';
import { resolveBinScriptsAsset } from './resolvers.js';
import type { BinScriptsDraftEditPlanV1, BinScriptsEditPlanV1 } from './types.js';

/** Resolve all external media requests in stable cue order before compilation. */
export async function resolveBinScriptsEditPlan(
  draft: BinScriptsDraftEditPlanV1,
  resolvers: readonly BinScriptsAssetResolver[],
): Promise<BinScriptsEditPlanV1> {
  const resolveCues = async <Cue extends { id: string; asset: BinScriptsAssetRequest }>(
    cues: readonly Cue[] | undefined,
    purpose: 'bleep' | 'reaction' | 'sound-effect',
  ) => {
    const resolved = new Map<string, Awaited<ReturnType<typeof resolveBinScriptsAsset>>>();
    for (const cue of [...(cues ?? [])].sort((a, b) => a.id.localeCompare(b.id))) {
      resolved.set(cue.id, await resolveBinScriptsAsset(cue.asset, { cueId: cue.id, purpose }, resolvers));
    }
    return (cues ?? []).map((cue) => ({ ...cue, asset: resolved.get(cue.id)! }));
  };

  return {
    ...draft,
    bleeps: await resolveCues(draft.bleeps, 'bleep'),
    reactions: await resolveCues(draft.reactions, 'reaction'),
    soundEffects: await resolveCues(draft.soundEffects, 'sound-effect'),
  };
}
