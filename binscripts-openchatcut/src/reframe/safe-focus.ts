export interface NormalizedPoint {
  x: number;
  y: number;
}

export interface NormalizedRect extends NormalizedPoint {
  width: number;
  height: number;
}

export interface SafeAreaInsets {
  top: number;
  right: number;
  bottom: number;
  left: number;
}

export interface TrackedFocalPoint extends NormalizedPoint {
  frame: number;
}

export interface SafeFocalResult {
  focalPoint: NormalizedPoint;
  visibleCrop: NormalizedRect;
  safeCrop: NormalizedRect;
  regionInsideSafeArea: boolean;
}

const clamp = (value: number, min: number, max: number): number => Math.max(min, Math.min(max, value));
const clamp01 = (value: number): number => clamp(value, 0, 1);

const normalizedInsets = (insets: SafeAreaInsets): SafeAreaInsets => ({
  top: clamp01(insets.top),
  right: clamp01(insets.right),
  bottom: clamp01(insets.bottom),
  left: clamp01(insets.left),
});

/** Linear sampling of a user-selected or tracker-produced focal path. */
export function focalPointOnPath(path: readonly TrackedFocalPoint[], frame: number): NormalizedPoint | null {
  if (!path.length) return null;
  const ordered = [...path].sort((a, b) => a.frame - b.frame || a.x - b.x || a.y - b.y);
  if (frame <= ordered[0]!.frame) return { x: clamp01(ordered[0]!.x), y: clamp01(ordered[0]!.y) };
  const last = ordered[ordered.length - 1]!;
  if (frame >= last.frame) return { x: clamp01(last.x), y: clamp01(last.y) };
  for (let index = 1; index < ordered.length; index += 1) {
    const right = ordered[index]!;
    if (frame > right.frame) continue;
    const left = ordered[index - 1]!;
    const progress = (frame - left.frame) / Math.max(1, right.frame - left.frame);
    return {
      x: clamp01(left.x + (right.x - left.x) * progress),
      y: clamp01(left.y + (right.y - left.y) * progress),
    };
  }
  return { x: clamp01(last.x), y: clamp01(last.y) };
}

/** Base cover crop in source-normalized coordinates, reduced by magnification. */
export function cropSizeForAspect(
  sourceWidth: number,
  sourceHeight: number,
  outputWidth: number,
  outputHeight: number,
  magnification = 1,
): { width: number; height: number } {
  if (!(sourceWidth > 0 && sourceHeight > 0 && outputWidth > 0 && outputHeight > 0)) {
    throw new Error('Crop dimensions must be positive');
  }
  const sourceAspect = sourceWidth / sourceHeight;
  const outputAspect = outputWidth / outputHeight;
  const zoom = clamp(magnification, 0.05, 16);
  const base = sourceAspect > outputAspect
    ? { width: outputAspect / sourceAspect, height: 1 }
    : { width: 1, height: sourceAspect / outputAspect };
  return { width: clamp01(base.width / zoom), height: clamp01(base.height / zoom) };
}

function axisCenter(
  desired: number,
  regionStart: number,
  regionEnd: number,
  cropSize: number,
  safeStart: number,
  safeEnd: number,
): number {
  const sourceMin = cropSize / 2;
  const sourceMax = 1 - cropSize / 2;
  const regionMinCenter = regionEnd - cropSize * safeEnd;
  const regionMaxCenter = regionStart - cropSize * safeStart;
  const feasibleMin = Math.max(sourceMin, regionMinCenter);
  const feasibleMax = Math.min(sourceMax, regionMaxCenter);
  if (feasibleMin <= feasibleMax) return clamp(desired, feasibleMin, feasibleMax);
  // The region is larger than the safe window or touches incompatible source
  // bounds. Center the best possible crop on the region instead of reverting to
  // a blind composition-center crop.
  return clamp((regionStart + regionEnd) / 2, sourceMin, sourceMax);
}

const contains = (outer: NormalizedRect, inner: NormalizedRect): boolean => {
  const epsilon = 1e-9;
  return inner.x + epsilon >= outer.x
    && inner.y + epsilon >= outer.y
    && inner.x + inner.width <= outer.x + outer.width + epsilon
    && inner.y + inner.height <= outer.y + outer.height + epsilon;
};

/**
 * Clamp a selected/tracked focal point so the focal region remains visible and
 * inside the output safe area whenever geometry makes that possible.
 */
export function safeFocalPoint(input: {
  source: { width: number; height: number };
  output: { width: number; height: number };
  desiredFocalPoint: NormalizedPoint;
  focalRegion: NormalizedRect;
  safeArea: SafeAreaInsets;
  magnification?: number;
}): SafeFocalResult {
  const cropSize = cropSizeForAspect(
    input.source.width,
    input.source.height,
    input.output.width,
    input.output.height,
    input.magnification,
  );
  const safe = normalizedInsets(input.safeArea);
  if (safe.left + safe.right >= 1 || safe.top + safe.bottom >= 1) {
    throw new Error('Safe-area insets must leave a positive interior');
  }
  const region: NormalizedRect = {
    x: clamp01(input.focalRegion.x),
    y: clamp01(input.focalRegion.y),
    width: clamp(input.focalRegion.width, 0, 1 - clamp01(input.focalRegion.x)),
    height: clamp(input.focalRegion.height, 0, 1 - clamp01(input.focalRegion.y)),
  };
  const center = {
    x: axisCenter(input.desiredFocalPoint.x, region.x, region.x + region.width, cropSize.width, safe.left, 1 - safe.right),
    y: axisCenter(input.desiredFocalPoint.y, region.y, region.y + region.height, cropSize.height, safe.top, 1 - safe.bottom),
  };
  const visibleCrop: NormalizedRect = {
    x: center.x - cropSize.width / 2,
    y: center.y - cropSize.height / 2,
    width: cropSize.width,
    height: cropSize.height,
  };
  const safeCrop: NormalizedRect = {
    x: visibleCrop.x + cropSize.width * safe.left,
    y: visibleCrop.y + cropSize.height * safe.top,
    width: cropSize.width * (1 - safe.left - safe.right),
    height: cropSize.height * (1 - safe.top - safe.bottom),
  };
  return {
    focalPoint: center,
    visibleCrop,
    safeCrop,
    regionInsideSafeArea: contains(safeCrop, region),
  };
}
