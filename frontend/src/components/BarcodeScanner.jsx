import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Live code scanning from a camera.
 *
 * Two jobs, chosen with `mode`: retail barcodes at the intake desk
 * (`"retail"`, the default) and pickup QR codes at the shelf (`"qr"`).
 * Everything that differs between them lives in the MODES table below —
 * see the note there on why this is one named mode rather than a handful
 * of independent props.
 *
 * Two decoders, picked at runtime:
 *
 *  1. `BarcodeDetector`, the browser's own. Native, hardware-accelerated,
 *     and free — no JavaScript ships for it. Available on Chrome and Edge
 *     on most platforms, and on Android.
 *  2. ZXing, lazy-loaded only if the first is missing. It's ~200KB, which
 *     is why it is behind a dynamic import rather than a top-level one:
 *     browsers that have the native API never download it.
 *
 * Feature detection is a live probe, not a `'BarcodeDetector' in window`
 * check. Some browsers expose the constructor and then throw on
 * construction or support no formats, and a check that passes before
 * failing at the first frame is worse than no check.
 *
 * Two ways to get a read, because donated food is not a checkout lane:
 *
 *  - Live decoding off the preview, for a flat box held steady.
 *  - "Capture", which decodes one full-resolution still. A crinkled foil
 *    bag under store lighting rarely offers a sharp frame at 5 fps, and
 *    letting someone steady the shot and press a button beats asking them
 *    to hold a curved package still for ten seconds.
 *
 * Why this is separate from the label photo in Intake: that one takes a
 * still and sends it to the server for OCR, because reading a date needs
 * a real OCR engine. A barcode is decoded here in the browser, off the
 * live preview, because the answer is a checksummed number and a round
 * trip per frame would make it unusable.
 */

const SCAN_INTERVAL_MS = 200;

// How long a scan goes quiet before the status line stops repeating itself
// and starts suggesting what to change. Saying the same thing at second 1
// and second 60 is how a person concludes the scanner is broken rather
// than that they are holding it wrong.
const COACH_AFTER_MS = 8000;

/**
 * The two things this component is pointed at, and everything that
 * differs between them.
 *
 * A single `formats` prop would not have been enough, and would have
 * failed quietly: the format list is one of six decisions that have to
 * move together. Pass QR formats while `normalize` still strips
 * non-digits and the scanner reads the code perfectly, hands back the
 * four digits that survived, and the server reports an invalid token.
 *
 * `aimBox` is normalized against the video frame. The box a person aims
 * with and the box the decoder reads have to be the same rectangle or the
 * guidance is a lie, so both the overlay and the crop derive from it.
 */
const MODES = {
  // UPC-A, UPC-E, EAN-13, EAN-8. A narrow set is faster per frame and
  // cannot return a shipping label's tracking code when someone means to
  // scan the product.
  retail: {
    subject: "barcode",
    formats: ["upc_a", "upc_e", "ean_13", "ean_8"],
    zxingReader: "oned",
    zxingFormats: ["UPC_A", "UPC_E", "EAN_13", "EAN_8"],
    normalize: (raw) => String(raw || "").replace(/\D/g, ""),
    // A single frame can decode a 1D symbol wrong under motion blur; two
    // identical reads in a row essentially cannot. The check digit catches
    // the rest.
    confirmations: 2,
    // A letterbox: retail barcodes are wide and short.
    aimBox: { x: 0.1, y: 0.35, w: 0.8, h: 0.3 },
    aimHint: "Fill the white box with the barcode, hold steady, and give it good light.",
    coachHint:
      "Still looking. Move closer so the barcode fills the white box, flatten any " +
      "curve in the package, add light — or hold it steady and press Capture.",
    captureFail:
      "Couldn't read that one. Flatten the package so the bars aren't curved, fill " +
      "the white box, and try again — or type the digits under the barcode.",
    manualHint:
      "Can't get a read? Close this and type the digits under the barcode — it's the same thing.",
  },
  // Pickup tokens. These are base64url, so the retail digit-strip would
  // destroy them.
  qr: {
    subject: "QR code",
    formats: ["qr_code"],
    zxingReader: "qr",
    zxingFormats: ["QR_CODE"],
    normalize: (raw) => String(raw || "").trim(),
    // One read is enough. QR carries Reed–Solomon error correction, so a
    // decode either satisfies the ECC or throws — there is no
    // misread-under-blur failure mode to defend against, and asking for a
    // second read only makes the scan feel sluggish at the shelf.
    confirmations: 1,
    // Nearly square, and taller: a QR fills the frame rather than
    // stretching across it, and the retail letterbox would crop its top
    // and bottom off.
    aimBox: { x: 0.2, y: 0.15, w: 0.6, h: 0.7 },
    aimHint: "Hold the phone's QR code inside the white box.",
    coachHint:
      "Still looking. Move the phone closer or further back so the whole code sits " +
      "inside the white box, and turn its screen brightness up.",
    captureFail:
      "Couldn't read that one. Turn the phone's brightness up, avoid glare on the " +
      "screen, and fill the white box — or enter the code by hand.",
    manualHint: "Can't get a read? Close this and enter the code by hand instead.",
  },
};

export default function BarcodeScanner({ onDetected, onClose, mode = "retail" }) {
  const cfg = MODES[mode] || MODES.retail;
  const videoRef = useRef(null);
  const streamRef = useRef(null);
  const stopRef = useRef(null);
  // Two canvases, not one. The live loop redraws its own every 200ms while
  // Capture is drawing five crops into the other; sharing one would mean a
  // pending detect() reading a frame that had already been overwritten.
  const canvasRef = useRef(null);
  const captureCanvasRef = useRef(null);
  const detectorRef = useRef(null);
  const zxingReaderRef = useRef(null);
  const [status, setStatus] = useState("starting");
  const [message, setMessage] = useState("");
  const [devices, setDevices] = useState([]);
  const [deviceId, setDeviceId] = useState("");
  const [engine, setEngine] = useState("");
  const [torchAvailable, setTorchAvailable] = useState(false);
  const [torchOn, setTorchOn] = useState(false);
  const [capturing, setCapturing] = useState(false);
  const [coaching, setCoaching] = useState(false);
  const [aimStyle, setAimStyle] = useState(null);

  // Held in a ref, not state: the decode loop reads it every frame, and a
  // state update per frame would re-render the component five times a
  // second for a value nothing displays.
  const lastRead = useRef({ code: null, count: 0 });
  const finished = useRef(false);

  const handleHit = useCallback(
    (raw) => {
      const code = cfg.normalize(raw);
      if (!code || finished.current) return;

      if (lastRead.current.code === code) {
        lastRead.current.count += 1;
      } else {
        lastRead.current = { code, count: 1 };
      }

      if (lastRead.current.count >= cfg.confirmations) {
        finished.current = true;
        // The server still validates whatever comes out of here — this is
        // a convenience layer over the keyboard, never a source of trust.
        onDetected(code);
      }
    },
    [onDetected, cfg],
  );

  const stop = useCallback(() => {
    stopRef.current?.();
    stopRef.current = null;
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    detectorRef.current = null;
    zxingReaderRef.current = null;
  }, []);

  // Enumerate cameras once permission exists. Before the user grants it,
  // labels come back empty, so this runs after the stream is open.
  const listCameras = useCallback(async () => {
    try {
      const all = await navigator.mediaDevices.enumerateDevices();
      setDevices(all.filter((d) => d.kind === "videoinput"));
    } catch {
      // Not being able to list cameras doesn't stop the one already open.
    }
  }, []);

  useEffect(() => {
    let cancelled = false;

    async function start() {
      if (!navigator.mediaDevices?.getUserMedia) {
        setStatus("error");
        setMessage(
          `This browser won't give a page camera access. Enter the ${cfg.subject} by hand instead.`,
        );
        return;
      }

      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          video: deviceId
            ? { deviceId: { exact: deviceId } }
            : {
                // Prefers the rear camera on a phone and is ignored on a
                // laptop, which has only the one.
                facingMode: { ideal: "environment" },
                // A barcode's bars are a few pixels wide at 480p. Asking
                // for more resolution is the single biggest thing that
                // decides whether a webcam can read one at all.
                width: { ideal: 1920 },
                height: { ideal: 1080 },
              },
          audio: false,
        });
        if (cancelled) {
          stream.getTracks().forEach((t) => t.stop());
          return;
        }

        streamRef.current = stream;
        if (videoRef.current) {
          videoRef.current.srcObject = stream;
          await videoRef.current.play().catch(() => {});
        }
        setStatus("scanning");
        setTorchOn(false);
        listCameras();
        setTorchAvailable(await applyTrackTuning(stream.getVideoTracks()[0]));

        const detector = await makeNativeDetector(cfg.formats);
        if (detector) {
          detectorRef.current = detector;
          setEngine("browser");
          stopRef.current = runNative(detector, videoRef, canvasRef, handleHit, cfg.aimBox);
        } else {
          setEngine("zxing");
          stopRef.current = await runZxing(videoRef, stream, zxingReaderRef, handleHit, cfg);
        }
      } catch (err) {
        if (cancelled) return;
        setStatus("error");
        setMessage(describeCameraError(err, cfg.subject));
      }
    }

    start();
    return () => {
      cancelled = true;
      stop();
    };
  }, [deviceId, handleHit, listCameras, stop, cfg]);

  // Keep the overlay glued to the picture. The <video> is object-contain
  // inside a max-height box, so the element is letterboxed and an inset in
  // element percentages lands somewhere else than the same percentage of
  // the frame — badly so on a laptop, where the bars are worst.
  useEffect(() => {
    if (status !== "scanning") return undefined;

    function reposition() {
      const rect = aimOverlayRect(videoRef.current, cfg.aimBox);
      if (rect) setAimStyle(rect);
    }

    reposition();
    const timer = setInterval(reposition, 500);
    window.addEventListener("resize", reposition);
    return () => {
      clearInterval(timer);
      window.removeEventListener("resize", reposition);
    };
  }, [status, cfg]);

  // Nudge after a silent stretch. Reset by an actual read, which only
  // happens on the way out, so in practice this fires whenever someone is
  // struggling and never when they aren't.
  useEffect(() => {
    if (status !== "scanning") return undefined;
    const timer = setTimeout(() => setCoaching(true), COACH_AFTER_MS);
    // Reset on the way out rather than on the way in, so switching cameras
    // mid-struggle restarts the clock without a synchronous setState.
    return () => {
      clearTimeout(timer);
      setCoaching(false);
    };
  }, [status, deviceId]);

  async function toggleTorch() {
    const track = streamRef.current?.getVideoTracks()[0];
    if (!track) return;
    const next = !torchOn;
    try {
      await track.applyConstraints({ advanced: [{ torch: next }] });
      setTorchOn(next);
    } catch {
      // Some devices advertise torch and then refuse it while streaming.
      setTorchAvailable(false);
    }
  }

  /**
   * Decode one deliberately-steadied still.
   *
   * Accepts on a single read, unlike the live path's two. The two-read
   * rule exists to defeat motion blur, and a full-resolution frame someone
   * held still for has none by construction; the server's GS1 check digit
   * is still the real guard. Demanding two captures would mean two button
   * presses per package, which is the whole problem this solves.
   */
  async function captureStill() {
    const video = videoRef.current;
    if (!video || video.readyState < 2 || finished.current) return;

    setCapturing(true);
    setMessage("");
    try {
      const box = cfg.aimBox;
      const attempts = [
        () => cropFrame(video, captureCanvasRef, box, 1),
        // Upscaled with smoothing off: thin bars survive as hard edges
        // rather than being averaged into grey, which both decoders need.
        () => cropFrame(video, captureCanvasRef, box, 2),
        // For someone who aimed badly rather than held badly.
        () => cropFrame(video, captureCanvasRef, FULL_FRAME, 1),
        () => cropFrame(video, captureCanvasRef, box, 1, 90),
        () => cropFrame(video, captureCanvasRef, box, 1, 270),
      ];

      for (const build of attempts) {
        const canvas = build();
        if (!canvas) continue;
        const code = await decodeCanvas(canvas, detectorRef.current, zxingReaderRef.current);
        if (code) {
          finished.current = true;
          onDetected(cfg.normalize(code));
          return;
        }
      }

      setMessage(cfg.captureFail);
    } finally {
      setCapturing(false);
    }
  }

  return (
    <div className="mt-4 rounded-lg border border-gray-200 bg-gray-50 p-3">
      <div className="relative overflow-hidden rounded-lg bg-black">
        <video
          ref={videoRef}
          playsInline
          muted
          // Mirroring is right for a selfie and wrong for holding a package
          // up to a laptop: a mirrored barcode is harder for a person to
          // aim, even though the decoder doesn't care.
          className="block max-h-80 w-full object-contain"
        />
        {/* Aiming guide. A webcam's autofocus hunts on a plain background,
            so giving people a box to fill is most of the battle — and this
            box is the rectangle actually handed to the decoder. */}
        {aimStyle && (
          <div
            aria-hidden="true"
            style={aimStyle}
            className="pointer-events-none absolute rounded-lg border-2 border-white/70"
          />
        )}
      </div>

      <div role="status" aria-live="polite" className="mt-2 text-xs text-gray-600">
        {status === "starting" && "Waiting for the camera…"}
        {status === "scanning" &&
          (coaching ? (
            <span className="text-gray-700">{cfg.coachHint}</span>
          ) : (
            <>
              {cfg.aimHint}{" "}
              <span className="text-gray-400">
                ({engine === "browser" ? "browser decoder" : "ZXing"})
              </span>
            </>
          ))}
        {status === "error" && <span className="font-medium text-amber-800">{message}</span>}
      </div>

      {status === "scanning" && message && (
        <p className="mt-1 text-xs font-medium text-amber-800">{message}</p>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={captureStill}
          disabled={status !== "scanning" || capturing}
          className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
        >
          {capturing ? "Reading…" : "Capture"}
        </button>
        {torchAvailable && (
          <button
            type="button"
            onClick={toggleTorch}
            aria-pressed={torchOn}
            className="rounded-lg border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
          >
            {torchOn ? "Light off" : "Light on"}
          </button>
        )}
        {devices.length > 1 && (
          <select
            value={deviceId}
            onChange={(e) => setDeviceId(e.target.value)}
            aria-label="Camera"
            className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-100"
          >
            <option value="">Default camera</option>
            {devices.map((device, index) => (
              <option key={device.deviceId} value={device.deviceId}>
                {device.label || `Camera ${index + 1}`}
              </option>
            ))}
          </select>
        )}
        <button
          type="button"
          onClick={onClose}
          className="rounded-lg border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
        >
          Stop scanning
        </button>
        <span className="text-xs text-gray-500">{cfg.manualHint}</span>
      </div>
    </div>
  );
}

/* ---------------- framing ---------------- */

const FULL_FRAME = { x: 0, y: 0, w: 1, h: 1 };

/**
 * Where the aiming box belongs on screen, in pixels relative to the
 * wrapper.
 *
 * `object-contain` letterboxes: the picture is centred inside the element
 * with bars on two sides. Positioning the overlay against the element
 * would put it over the bars on a laptop, where the 16:9 stream sits in a
 * much wider box. This finds the picture first, then applies the mode's
 * aiming box to it, so the overlay and the crop are the same rectangle by
 * construction.
 */
function aimOverlayRect(video, aimBox) {
  if (!video || !video.videoWidth || !video.clientWidth) return null;

  const scale = Math.min(
    video.clientWidth / video.videoWidth,
    video.clientHeight / video.videoHeight,
  );
  const shownW = video.videoWidth * scale;
  const shownH = video.videoHeight * scale;
  const offsetX = (video.clientWidth - shownW) / 2;
  const offsetY = (video.clientHeight - shownH) / 2;

  return {
    left: `${offsetX + shownW * aimBox.x}px`,
    top: `${offsetY + shownH * aimBox.y}px`,
    width: `${shownW * aimBox.w}px`,
    height: `${shownH * aimBox.h}px`,
  };
}

/**
 * Draw a normalized sub-rectangle of the frame onto a canvas, in intrinsic
 * pixels, optionally scaled and rotated.
 *
 * The canvas is reused across calls rather than allocated per tick: this
 * runs five times a second for as long as the preview is open.
 */
function cropFrame(video, canvasRef, box, scale = 1, rotation = 0) {
  const fw = video.videoWidth;
  const fh = video.videoHeight;
  if (!fw || !fh) return null;

  const sx = Math.round(fw * box.x);
  const sy = Math.round(fh * box.y);
  const sw = Math.round(fw * box.w);
  const sh = Math.round(fh * box.h);
  if (sw < 1 || sh < 1) return null;

  const dw = Math.round(sw * scale);
  const dh = Math.round(sh * scale);
  const swapped = rotation === 90 || rotation === 270;

  const canvas = (canvasRef.current ||= document.createElement("canvas"));
  canvas.width = swapped ? dh : dw;
  canvas.height = swapped ? dw : dh;

  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  if (!ctx) return null;
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  // Smoothing averages a thin bar into the space beside it. Off, an
  // upscale keeps the edges the decoder is looking for.
  ctx.imageSmoothingEnabled = false;

  if (rotation) {
    ctx.translate(canvas.width / 2, canvas.height / 2);
    ctx.rotate((rotation * Math.PI) / 180);
    ctx.translate(-dw / 2, -dh / 2);
  }

  ctx.drawImage(video, sx, sy, sw, sh, 0, 0, dw, dh);
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  return canvas;
}

/**
 * Ask the camera for continuous focus, and report whether it has a torch.
 *
 * Both are best-effort. `getCapabilities` doesn't exist on Firefox or
 * older Safari at all, and some devices advertise a capability and then
 * reject the constraint — neither is a reason to fail the scan, so every
 * step swallows its own error.
 */
async function applyTrackTuning(track) {
  if (!track?.getCapabilities) return false;

  let capabilities;
  try {
    capabilities = track.getCapabilities();
  } catch {
    return false;
  }

  // A fixed-focus laptop webcam ignores this; a phone held 15cm from a bag
  // very much does not.
  if (capabilities.focusMode?.includes?.("continuous")) {
    try {
      await track.applyConstraints({ advanced: [{ focusMode: "continuous" }] });
    } catch {
      // Focus stays wherever the driver put it.
    }
  }

  return capabilities.torch === true;
}

/* ---------------- decoders ---------------- */

/**
 * Build a native BarcodeDetector, or null if this browser can't.
 *
 * Constructing it and asking for supported formats is the only honest
 * test: Safari and some Chromium builds expose the global and then fail.
 */
async function makeNativeDetector(wanted) {
  if (typeof window === "undefined" || !("BarcodeDetector" in window)) return null;
  try {
    const supported = await window.BarcodeDetector.getSupportedFormats();
    const formats = wanted.filter((f) => supported.includes(f));
    if (formats.length === 0) return null;
    return new window.BarcodeDetector({ formats });
  } catch {
    return null;
  }
}

/** Run one canvas past whichever decoder is live. Returns the raw decoded
 *  text, or null — the caller normalizes it for its mode. */
async function decodeCanvas(canvas, detector, zxingReader) {
  if (detector) {
    try {
      const found = await detector.detect(canvas);
      if (found.length > 0) return found[0].rawValue;
    } catch {
      // Fall through: an unreadable crop is the expected case here.
    }
    return null;
  }

  if (zxingReader) {
    try {
      return zxingReader.decodeFromCanvas(canvas)?.getText() ?? null;
    } catch {
      // ZXing throws NotFoundException for "no barcode", which is not an
      // error condition when we are trying five crops in a row.
    }
  }
  return null;
}

/**
 * Poll frames through the native detector. Returns a stop function.
 *
 * Only the aiming box is handed over. Searching a whole 1080p frame for a
 * symbol covering a tenth of it is both slower and less reliable than
 * searching the region the person was told to aim with.
 */
function runNative(detector, videoRef, canvasRef, onHit, aimBox) {
  let stopped = false;
  // detect() is async and the canvas is reused, so a tick that overruns
  // the interval would have its frame redrawn underneath it. Skipping the
  // tick is right anyway: a backlog of stale frames helps nobody.
  let inFlight = false;

  const timer = setInterval(async () => {
    const video = videoRef.current;
    // readyState < 2 means there is no frame yet; detect() on one throws.
    if (stopped || inFlight || !video || video.readyState < 2) return;
    inFlight = true;
    try {
      const canvas = cropFrame(video, canvasRef, aimBox, 1);
      if (!canvas) return;
      const found = await detector.detect(canvas);
      if (found.length > 0) onHit(found[0].rawValue);
    } catch {
      // A dropped frame is normal — the next tick tries again.
    } finally {
      inFlight = false;
    }
  }, SCAN_INTERVAL_MS);

  return () => {
    stopped = true;
    clearInterval(timer);
  };
}

/**
 * ZXing fallback, loaded on demand. Returns a stop function.
 *
 * Restricted to the mode's formats for the same reason as the native
 * path, and because ZXing gets materially faster when it isn't trying
 * every symbology on every frame. That is also why this picks a
 * symbology-specific reader rather than BrowserMultiFormatReader — and in
 * QR mode it is not merely an optimization: the OneD reader cannot decode
 * a 2D symbol at all.
 *
 * The live path streams the whole element — `decodeFromStream` owns the
 * video and can't be given a crop — so ZXing browsers get the aiming box
 * on the Capture path instead, via the reader stashed in `readerRef`.
 */
async function runZxing(videoRef, stream, readerRef, onHit, cfg) {
  // The dynamic import above gives React time to unmount underneath us —
  // 400KB is a slow first load on a store network. Without this guard
  // ZXing is handed a null element and throws where the user sees it.
  if (!videoRef.current) return () => {};

  const [{ BrowserMultiFormatOneDReader, BrowserQRCodeReader }, { BarcodeFormat, DecodeHintType }] =
    await Promise.all([import("@zxing/browser"), import("@zxing/library")]);

  if (!videoRef.current) return () => {};

  const hints = new Map([
    [DecodeHintType.POSSIBLE_FORMATS, cfg.zxingFormats.map((name) => BarcodeFormat[name])],
    // More scan lines per pass. Worth the CPU on a curved package, where
    // the row through the middle is the one most likely to be distorted.
    [DecodeHintType.TRY_HARDER, true],
  ]);

  // Both extend BrowserCodeReader and take the same (hints, options), so
  // decodeCanvas and decodeFromStream work unchanged across the two.
  const Reader = cfg.zxingReader === "qr" ? BrowserQRCodeReader : BrowserMultiFormatOneDReader;
  const reader = new Reader(hints, {
    delayBetweenScanAttempts: SCAN_INTERVAL_MS,
  });
  readerRef.current = reader;

  const controls = await reader.decodeFromStream(stream, videoRef.current, (result) => {
    if (result) onHit(result.getText());
  });

  return () => controls.stop();
}

/**
 * Turn a getUserMedia rejection into something a person can act on.
 *
 * NFR-4.5.5: every error says what happened and what to do about it. The
 * insecure-context case especially — "NotAllowedError" gives no hint that
 * the real problem is the URL, and someone will otherwise spend an
 * afternoon re-granting a permission that was never the issue.
 */
function describeCameraError(err, subject = "barcode") {
  const name = err?.name || "";

  if (!window.isSecureContext) {
    return (
      "The browser only allows camera access over HTTPS or on localhost. " +
      `This page is on ${window.location.origin} — open it as http://localhost instead.`
    );
  }
  if (name === "NotAllowedError" || name === "SecurityError") {
    return "Camera access was blocked. Allow it for this site in the browser's address bar, then try again.";
  }
  if (name === "NotFoundError" || name === "OverconstrainedError") {
    return `No camera found. Plug one in, or enter the ${subject} by hand instead.`;
  }
  if (name === "NotReadableError") {
    return "The camera is in use by another app. Close it (Teams, Zoom, Camera) and try again.";
  }
  return `The camera couldn't start: ${err?.message || name || "unknown error"}.`;
}
