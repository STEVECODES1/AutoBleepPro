import { BinScriptsAssetResolutionError } from './resolvers.js';

export type FalMediaKind = 'audio' | 'image' | 'video';

export interface FalGenerateInput {
  apiKey: string;
  model: string;
  prompt: string;
  mediaKind: FalMediaKind;
  requestName: string;
  /** Defaults to https://fal.run. */
  baseUrl?: string;
  /** Injectable fetch for tests; defaults to global fetch. */
  fetchImpl?: typeof fetch;
}

export interface FalGenerateOutput {
  url: string;
  mediaKind: FalMediaKind;
}

/**
 * Pull a media URL out of the shapes fal.ai returns for images, audio, and
 * video. Handles `{images:[{url}]}`, `{audio:{url}}`, `{video:{url}}`, and a
 * bare `{url}` fallback.
 */
export function extractFalMediaUrl(payload: unknown, mediaKind: FalMediaKind): string | null {
  if (!payload || typeof payload !== 'object') return null;
  const record = payload as Record<string, unknown>;
  if (Array.isArray(record.images)) {
    const first = record.images[0] as Record<string, unknown> | undefined;
    if (first && typeof first.url === 'string') return first.url;
  }
  for (const key of [mediaKind, 'audio', 'image', 'video']) {
    const field = record[key];
    if (field && typeof field === 'object') {
      const url = (field as Record<string, unknown>).url;
      if (typeof url === 'string') return url;
    }
  }
  if (typeof record.url === 'string') return record.url;
  return null;
}

/** POST one prompt to fal.ai and return the generated media URL. */
export async function generateFalMedia(input: FalGenerateInput): Promise<FalGenerateOutput> {
  const baseUrl = (input.baseUrl ?? 'https://fal.run').replace(/\/+$/, '');
  const fetchImpl = input.fetchImpl ?? fetch;
  const response = await fetchImpl(`${baseUrl}/${input.model}`, {
    method: 'POST',
    headers: {
      Authorization: `Key ${input.apiKey}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ prompt: input.prompt }),
  });
  if (!response.ok) {
    const body = await response.text().catch(() => '');
    throw new BinScriptsAssetResolutionError(
      `fal.ai request "${input.requestName}" failed (${response.status}): ${body.slice(0, 240)}`,
    );
  }
  const payload = (await response.json()) as unknown;
  const url = extractFalMediaUrl(payload, input.mediaKind);
  if (!url) {
    throw new BinScriptsAssetResolutionError(
      `fal.ai request "${input.requestName}" returned no ${input.mediaKind} URL`,
    );
  }
  return { url, mediaKind: input.mediaKind };
}
