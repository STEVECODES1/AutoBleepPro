import assert from 'node:assert/strict';
import { focalPointOnPath, safeFocalPoint } from './safe-focus';

const subject = { x: 0.74, y: 0.18, width: 0.12, height: 0.34 };
const vertical = safeFocalPoint({
  source: { width: 1920, height: 1080 },
  output: { width: 1080, height: 1920 },
  desiredFocalPoint: { x: 0.8, y: 0.35 },
  focalRegion: subject,
  safeArea: { top: 0.05, right: 0.05, bottom: 0.05, left: 0.05 },
});

assert.equal(vertical.regionInsideSafeArea, true, 'non-centered subject must fit the vertical action-safe crop');
assert.ok(vertical.focalPoint.x > 0.7, 'vertical reframe follows the right-side subject instead of center-cropping');
assert.ok(subject.x >= vertical.visibleCrop.x && subject.x + subject.width <= vertical.visibleCrop.x + vertical.visibleCrop.width,
  'subject remains in the visible crop');
assert.ok(subject.x >= vertical.safeCrop.x && subject.x + subject.width <= vertical.safeCrop.x + vertical.safeCrop.width,
  'subject remains inside horizontal safe bounds');
assert.ok(subject.y >= vertical.safeCrop.y && subject.y + subject.height <= vertical.safeCrop.y + vertical.safeCrop.height,
  'subject remains inside vertical safe bounds');

assert.deepEqual(
  focalPointOnPath([{ frame: 30, x: 0.8, y: 0.4 }, { frame: 0, x: 0.2, y: 0.2 }], 15),
  { x: 0.5, y: 0.30000000000000004 },
  'tracked paths are sorted and interpolated deterministically',
);

const impossible = safeFocalPoint({
  source: { width: 1920, height: 1080 },
  output: { width: 1080, height: 1920 },
  desiredFocalPoint: { x: 0.1, y: 0.5 },
  focalRegion: { x: 0.05, y: 0.1, width: 0.8, height: 0.8 },
  safeArea: { top: 0.05, right: 0.05, bottom: 0.05, left: 0.05 },
});
assert.equal(impossible.regionInsideSafeArea, false, 'oversized subjects report that safe containment is impossible');
assert.ok(impossible.visibleCrop.x >= 0 && impossible.visibleCrop.x + impossible.visibleCrop.width <= 1,
  'best-effort crop remains within source bounds');

console.log('safe-focus.verify: all assertions passed');
