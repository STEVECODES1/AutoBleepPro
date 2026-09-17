import type { ResolvedCueAsset } from './types.js';

export type BinScriptsAssetPurpose = 'bleep' | 'reaction' | 'sound-effect';

export type BinScriptsAssetRequest =
  | { kind: 'project-asset'; assetId: string; name: string }
  | { kind: 'auto-bleep-pro'; name: string; preset?: string }
  | { kind: 'fal-generation'; name: string; mediaKind: 'audio' | 'image' | 'video'; prompt: string; model?: string };

export interface BinScriptsAssetResolverContext {
  cueId: string;
  purpose: BinScriptsAssetPurpose;
}

export interface BinScriptsAssetResolver {
  readonly id: string;
  supports(request: BinScriptsAssetRequest): boolean;
  resolve(request: BinScriptsAssetRequest, context: BinScriptsAssetResolverContext): Promise<ResolvedCueAsset>;
}

export class BinScriptsAssetResolutionError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'BinScriptsAssetResolutionError';
  }
}

export const projectAssetResolver: BinScriptsAssetResolver = {
  id: 'project-asset',
  supports: (request) => request.kind === 'project-asset',
  resolve: async (request) => {
    if (request.kind !== 'project-asset') throw new BinScriptsAssetResolutionError('Unsupported project asset request');
    return { assetId: request.assetId, name: request.name };
  },
};

/**
 * AutoBleepPro integration boundary. No public API/CLI contract is assumed:
 * the host adapter must locate/export the bleep and import it into the media
 * pool, then return that existing project asset reference.
 */
export function createAutoBleepProResolver(adapter: {
  resolveImportedAsset(request: Extract<BinScriptsAssetRequest, { kind: 'auto-bleep-pro' }>, context: BinScriptsAssetResolverContext): Promise<ResolvedCueAsset>;
}): BinScriptsAssetResolver {
  return {
    id: 'auto-bleep-pro-adapter',
    supports: (request) => request.kind === 'auto-bleep-pro',
    resolve: async (request, context) => {
      if (request.kind !== 'auto-bleep-pro') throw new BinScriptsAssetResolutionError('Unsupported AutoBleepPro request');
      return adapter.resolveImportedAsset(request, context);
    },
  };
}

export const FAL_ENV_PLACEHOLDERS = {
  apiKey: 'FAL_KEY',
  defaultModel: 'FAL_MODEL_ID',
} as const;

/** Optional fal.ai boundary; transport and endpoint contracts remain host-owned. */
export function createFalGenerationResolver(options: {
  env: Partial<Record<(typeof FAL_ENV_PLACEHOLDERS)[keyof typeof FAL_ENV_PLACEHOLDERS], string>>;
  generate(request: Extract<BinScriptsAssetRequest, { kind: 'fal-generation' }>, context: BinScriptsAssetResolverContext, config: { apiKey: string; model: string }): Promise<ResolvedCueAsset>;
}): BinScriptsAssetResolver {
  return {
    id: 'fal-generation-adapter',
    supports: (request) => request.kind === 'fal-generation',
    resolve: async (request, context) => {
      if (request.kind !== 'fal-generation') throw new BinScriptsAssetResolutionError('Unsupported fal.ai request');
      const apiKey = options.env.FAL_KEY;
      const model = request.model ?? options.env.FAL_MODEL_ID;
      if (!apiKey || !model) {
        throw new BinScriptsAssetResolutionError('fal.ai generation requires FAL_KEY and a request model or FAL_MODEL_ID');
      }
      return options.generate(request, context, { apiKey, model });
    },
  };
}

export async function resolveBinScriptsAsset(
  request: BinScriptsAssetRequest,
  context: BinScriptsAssetResolverContext,
  resolvers: readonly BinScriptsAssetResolver[],
): Promise<ResolvedCueAsset> {
  const resolver = resolvers.find((candidate) => candidate.supports(request));
  if (!resolver) throw new BinScriptsAssetResolutionError(`No resolver supports ${request.kind} for cue ${context.cueId}`);
  const resolved = await resolver.resolve(request, context);
  if (!resolved.assetId || !resolved.name) throw new BinScriptsAssetResolutionError(`Resolver ${resolver.id} returned an incomplete asset`);
  return resolved;
}
