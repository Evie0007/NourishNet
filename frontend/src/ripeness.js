/**
 * Banana ripeness from colour alone.
 *
 * Why this runs in the browser and not the API: the input is a camera frame
 * the browser already holds, the answer is a handful of pixel counts, and it
 * needs no image library on the server (none is pinned). It also keeps
 * working with no network — the same reasoning as barcode decoding in
 * BarcodeScanner.
 *
 * Why HSV: ripeness is a hue story (green -> yellow -> brown) and RGB mixes
 * hue with brightness, so a shadowed yellow banana reads as "brown" in RGB
 * thresholds. Hue separates the two; value only has to tell brown from dark.
 *
 * What this is not: a safety judgement. Colour says how ripe the fruit is,
 * not whether it is fit to eat, and storage temperature moves the real answer
 * as much as colour does. The days-left figure is a range for prioritising
 * what to donate first — the printed date still governs, and a person
 * confirms anything that reaches inventory.
 */

/** Below this share of the frame, treat the shot as "no banana in view". */
const MIN_FRUIT_SHARE = 0.03;

/** Pixels this unsaturated or dark are background, not fruit. */
const MIN_SATURATION = 0.25;
const MIN_VALUE = 0.1;

/**
 * Ripeness stages in order. `daysLeft` is [low, high] of useful shelf life at
 * room temperature — deliberately a range, because one photo cannot know
 * the storage history.
 */
export const STAGES = [
  { key: "underripe", label: "Underripe (green)", daysLeft: [5, 8] },
  { key: "ripe", label: "Ripe (yellow)", daysLeft: [3, 5] },
  { key: "very_ripe", label: "Very ripe (brown spots)", daysLeft: [1, 3] },
  { key: "overripe", label: "Overripe (mostly brown)", daysLeft: [0, 1] },
  { key: "past_best", label: "Past its best (brown/black)", daysLeft: [0, 0] },
];

function stage(key) {
  return STAGES.find((s) => s.key === key);
}

/** RGB 0-255 -> { h: degrees 0-360, s: 0-1, v: 0-1 }. */
export function rgbToHsv(r, g, b) {
  const rn = r / 255;
  const gn = g / 255;
  const bn = b / 255;
  const max = Math.max(rn, gn, bn);
  const min = Math.min(rn, gn, bn);
  const d = max - min;

  let h = 0;
  if (d !== 0) {
    if (max === rn) h = ((gn - bn) / d) % 6;
    else if (max === gn) h = (bn - rn) / d + 2;
    else h = (rn - gn) / d + 4;
    h *= 60;
    if (h < 0) h += 360;
  }
  return { h, s: max === 0 ? 0 : d / max, v: max };
}

/**
 * Which colour bucket a fruit pixel falls in, or null for background.
 *
 * Brown is orange-ish hue at low-to-mid brightness; the same hue at high
 * brightness is the orange-yellow of a ripe peel, so value is what splits
 * them. Near-black with any saturation counts as brown too — bruising.
 */
function classify({ h, s, v }) {
  if (s < MIN_SATURATION || v < MIN_VALUE) return null;
  if (h >= 70 && h < 170) return "green";
  // A yellow peel in shade or under a dim webcam keeps its hue and only loses
  // brightness, so hue decides yellow here and value only has to rule out
  // near-black. Judging by brightness first read ordinary yellow bananas as
  // brown. Warm indoor light also drags yellow toward orange, hence 38.
  if (h >= 38 && h < 70 && v >= 0.25) return "yellow";
  if (h >= 8 && h < 38 && v >= 0.62) return "yellow";
  if (h >= 8 && h < 70) return "brown";
  return null;
}

/**
 * Analyse an ImageData-shaped object ({ data, width, height }, RGBA).
 *
 * Returns { found: false, reason } when no banana-coloured region is in
 * view, otherwise the colour shares, a stage, and a days-left range.
 */
export function analyzeRipeness({ data, width, height }) {
  const total = width * height;
  const counts = { green: 0, yellow: 0, brown: 0 };

  for (let i = 0; i < data.length; i += 4) {
    if (data[i + 3] === 0) continue;
    const bucket = classify(rgbToHsv(data[i], data[i + 1], data[i + 2]));
    if (bucket) counts[bucket] += 1;
  }

  const fruit = counts.green + counts.yellow + counts.brown;
  if (total === 0 || fruit / total < MIN_FRUIT_SHARE) {
    return {
      found: false,
      reason:
        "No fruit found in the frame. Put it on a plain background, fill more of the picture, and use even light.",
    };
  }

  const share = {
    green: counts.green / fruit,
    yellow: counts.yellow / fruit,
    brown: counts.brown / fruit,
  };

  let picked;
  if (share.green >= 0.35) picked = stage("underripe");
  // Thresholds are loose on purpose: shadow at the edges and a stray bit of
  // table count as "brown", so a handful of percent is noise, not spots.
  else if (share.brown >= 0.7) picked = stage("past_best");
  else if (share.brown >= 0.45) picked = stage("overripe");
  else if (share.brown >= 0.3) picked = stage("very_ripe");
  else picked = stage("ripe");

  return {
    found: true,
    stage: picked.key,
    label: picked.label,
    daysLeft: picked.daysLeft,
    share,
    fruitShare: fruit / total,
  };
}

/** "3–5 days", "about 1 day", "today" — never a falsely precise number. */
export function describeDaysLeft([low, high]) {
  if (high === 0) return "donate or discard today";
  if (low === high) return `about ${high} day${high === 1 ? "" : "s"}`;
  return `${low}–${high} days`;
}
