/**
 * Geometry-driven reframe focus: derive per-frame focal points from the
 * visual-geometry cache (segment subject/face centers) instead of energy-grid
 * sampling. Faster than pixel sampling and more accurate on complex
 * backgrounds; falls back to the caller's heuristic when geometry is absent.
 */

import { sourceFrameAt, type SourceTimingItem } from '../editor/sourceLimit';
import {
  DEFAULT_REFRAME_INTERVAL_FRAMES,
  DEFAULT_REFRAME_MAX_SAMPLES,
  DEFAULT_REFRAME_SMOOTH,
  sampleFrames,
  smoothFocalPath,
  type DetectedKeyframe,
} from './detect';
import type { VisualGeometryAsset } from '../geometry/visual-geometry';
import {
  focalPointOnPath,
  safeFocalPoint,
  type NormalizedPoint,
  type SafeAreaInsets,
  type TrackedFocalPoint,
} from './safe-focus';

export type GeometryFocusMode = 'subject' | 'face' | 'center';

export interface GeometryFocusOptions {
  /** Item-local timeline frame at which reframing starts. */
  srcInFrame?: number;
  playbackRate?: number;
  /** Sample every N frames (default 15 ≈ 0.5s at 30fps). */
  intervalFrames?: number;
  /** Hard cap for long clips. */
  maxSamples?: number;
  /** Temporal EMA on focal points (0 = raw, 1 = maximally sticky). */
  smooth?: number;
  /** Aspect-derived crop magnification applied to every keyframe. */
  magnification?: number;
  /** Select subject, face, or an explicit compatibility center fallback. */
  focusMode?: GeometryFocusMode;
  /** User-selected focal point; takes priority over geometry selection. */
  selectedFocalPoint?: NormalizedPoint;
  /** Tracker-produced item-local path; takes priority over a static selection. */
  trackedFocalPath?: readonly TrackedFocalPoint[];
  /** Enables crop/safe-area clamping when source and output dimensions are set. */
  framing?: {
    source: { width: number; height: number };
    output: { width: number; height: number };
    safeArea: SafeAreaInsets;
  };
}

const clamp01 = (x: number): number => Math.max(0, Math.min(1, x));

function geometryAt(geometry: VisualGeometryAsset, sourceSec: number) {
  let best: VisualGeometryAsset['segments'][number] | null = null;
  for (const segment of geometry.segments) {
    // Left-closed, right-open (with left tolerance): a boundary time belongs to
    // the segment that starts there, never to both.
    if (sourceSec >= segment.startSec - 0.01 && sourceSec < segment.endSec) {
      best = segment;
      break;
    }
  }
  // Tail of the last segment (sourceSec == last endSec) belongs to it.
  if (!best && geometry.segments.length) {
    const lastSegment = geometry.segments[geometry.segments.length - 1]!;
    if (sourceSec >= lastSegment.endSec) best = lastSegment;
  }
  return best;
}

function centerOf(rect: { x: number; y: number; w: number; h: number }): NormalizedPoint {
  return { x: clamp01(rect.x + rect.w / 2), y: clamp01(rect.y + rect.h / 2) };
}

/** Explicit geometry selection; subject mode falls back to face, then center. */
function focalOf(segment: ReturnType<typeof geometryAt>, mode: GeometryFocusMode): NormalizedPoint {
  if (mode === 'center' || !segment) return { x: 0.5, y: 0.5 };
  if (mode === 'face') return segment.zone.face ? centerOf(segment.zone.face) : { x: 0.5, y: 0.5 };
  if (segment.zone.subject) return centerOf(segment.zone.subject);
  if (segment.zone.face) return centerOf(segment.zone.face);
  return { x: 0.5, y: 0.5 };
}

/**
 * Build reframe keyframes from segment geometry. Each sampled timeline frame
 * maps to its source time (srcInFrame + playbackRate), picks the enclosing
 * segment, and uses the subject (or face) center as the focal point.
 * Magnification is left to the caller (aspect-driven), matching detect.ts.
 */
export function focalFramesFromGeometry(
  geometry: VisualGeometryAsset,
  durationInFrames: number,
  fps: number,
  options: GeometryFocusOptions = {},
): DetectedKeyframe[] {
  const interval = Math.max(1, Math.floor(options.intervalFrames ?? DEFAULT_REFRAME_INTERVAL_FRAMES));
  const maxSamples = Math.max(1, Math.floor(options.maxSamples ?? DEFAULT_REFRAME_MAX_SAMPLES));
  const frames = sampleFrames(durationInFrames, interval, maxSamples);
  if (!frames.length) return [];
  const item: SourceTimingItem = {
    srcInFrame: options.srcInFrame ?? 0,
    playbackRate: options.playbackRate ?? 1,
  };
  const raw = frames.map((frame) => {
    const sourceFrame = sourceFrameAt(item, frame);
    const segment = geometryAt(geometry, fps > 0 ? sourceFrame / fps : 0);
    const tracked = focalPointOnPath(options.trackedFocalPath ?? [], frame);
    const desired = tracked ?? options.selectedFocalPoint ?? focalOf(segment, options.focusMode ?? 'subject');
    if (!options.framing) return desired;
    const region = segment?.zone.subject ?? segment?.zone.face;
    const focalRegion = region
      ? { x: region.x, y: region.y, width: region.w, height: region.h }
      : { x: desired.x, y: desired.y, width: 0, height: 0 };
    return safeFocalPoint({
      ...options.framing,
      desiredFocalPoint: desired,
      focalRegion,
      magnification: options.magnification,
    }).focalPoint;
  });
  const points = smoothFocalPath(raw, options.smooth ?? DEFAULT_REFRAME_SMOOTH);
  const magnification = Math.max(0.05, Math.min(16, options.magnification ?? 1));
  return frames.map((frame, index) => ({
    frame,
    focalPointX: points[index]!.x,
    focalPointY: points[index]!.y,
    magnification,
  }));
}
