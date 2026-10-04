/**
 * A short checkout-lane beep for "that scan registered".
 *
 * Synthesized with the Web Audio API rather than shipped as an audio file:
 * there is nothing to download or cache, and no new dependency. The context
 * is created on first use and reused, because browsers cap how many can
 * exist and a new one per scan would run into that on a busy day.
 *
 * Sound is a convenience, never a signal anything depends on. Browsers
 * block audio until the page has had a click — here the "Scan with camera"
 * press counts — and some devices have no audio output at all. Every
 * failure is swallowed so a silent browser still scans.
 */
let context = null;

export function beep() {
  try {
    const AudioContext = window.AudioContext || window.webkitAudioContext;
    if (!AudioContext) return;
    context ||= new AudioContext();
    if (context.state === "suspended") context.resume();

    const oscillator = context.createOscillator();
    const gain = context.createGain();
    oscillator.type = "square";
    oscillator.frequency.value = 1800;
    // Ramp down instead of cutting off: an abrupt stop clicks.
    gain.gain.setValueAtTime(0.15, context.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, context.currentTime + 0.12);
    oscillator.connect(gain).connect(context.destination);
    oscillator.start();
    oscillator.stop(context.currentTime + 0.12);
  } catch {
    // Silent is fine.
  }
}
