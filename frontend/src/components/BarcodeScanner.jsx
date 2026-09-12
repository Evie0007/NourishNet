import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Live barcode scanning from a camera.
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
 * Why this is separate from the label photo in Intake: that one takes a
 * still and sends it to the server for OCR, because reading a date needs
 * a real OCR engine. A barcode is decoded here in the browser, off the
 * live preview, because the answer is a checksummed number and a round
 * trip per frame would make it unusable.
 */

// UPC-A, UPC-E, EAN-13, EAN-8. Deliberately not QR or Code 128: a
// narrower set is faster per frame and cannot return a shipping label's
// tracking code when someone means to scan the product.
const RETAIL_FORMATS = ["upc_a", "upc_e", "ean_13", "ean_8"];

const SCAN_INTERVAL_MS = 200;

// The same code has to be read this many times in a row before it counts.
// A single frame can decode wrong under motion blur; two identical reads
// in a row essentially cannot. The check digit catches the rest.
const CONFIRMATIONS_REQUIRED = 2;

export default function BarcodeScanner({ onDetected, onClose }) {
  const videoRef = useRef(null);
  const streamRef = useRef(null);
  const stopRef = useRef(null);
  const [status, setStatus] = useState("starting");
  const [message, setMessage] = useState("");
  const [devices, setDevices] = useState([]);
  const [deviceId, setDeviceId] = useState("");
  const [engine, setEngine] = useState("");

  // Held in a ref, not state: the decode loop reads it every frame, and a
  // state update per frame would re-render the component five times a
  // second for a value nothing displays.
  const lastRead = useRef({ code: null, count: 0 });
  const finished = useRef(false);

  const handleHit = useCallback(
    (raw) => {
      const code = String(raw || "").replace(/\D/g, "");
      if (!code || finished.current) return;

      if (lastRead.current.code === code) {
        lastRead.current.count += 1;
      } else {
        lastRead.current = { code, count: 1 };
      }

      if (lastRead.current.count >= CONFIRMATIONS_REQUIRED) {
        finished.current = true;
        // The server still normalizes and checks the check digit — this is
        // a convenience layer over the keyboard, never a source of trust.
        onDetected(code);
      }
    },
    [onDetected],
  );

  const stop = useCallback(() => {
    stopRef.current?.();
    stopRef.current = null;
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
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
          "This browser won't give a page camera access. Type the barcode instead.",
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
        listCameras();

        const detector = await makeNativeDetector();
        if (detector) {
          setEngine("browser");
          stopRef.current = runNative(detector, videoRef, handleHit);
        } else {
          setEngine("zxing");
          stopRef.current = await runZxing(videoRef, stream, handleHit);
        }
      } catch (err) {
        if (cancelled) return;
        setStatus("error");
        setMessage(describeCameraError(err));
      }
    }

    start();
    return () => {
      cancelled = true;
      stop();
    };
  }, [deviceId, handleHit, listCameras, stop]);

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
            so giving people a box to fill is most of the battle. */}
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-x-[10%] inset-y-[35%] rounded-lg border-2 border-white/70"
        />
      </div>

      <div role="status" aria-live="polite" className="mt-2 text-xs text-gray-600">
        {status === "starting" && "Waiting for the camera…"}
        {status === "scanning" && (
          <>
            Fill the white box with the barcode, hold steady, and give it good
            light.{" "}
            <span className="text-gray-400">
              ({engine === "browser" ? "browser decoder" : "ZXing"})
            </span>
          </>
        )}
        {status === "error" && <span className="font-medium text-amber-800">{message}</span>}
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2">
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
        <span className="text-xs text-gray-500">
          Can't get a read? Close this and type the digits under the barcode —
          it's the same thing.
        </span>
      </div>
    </div>
  );
}

/* ---------------- decoders ---------------- */

/**
 * Build a native BarcodeDetector, or null if this browser can't.
 *
 * Constructing it and asking for supported formats is the only honest
 * test: Safari and some Chromium builds expose the global and then fail.
 */
async function makeNativeDetector() {
  if (typeof window === "undefined" || !("BarcodeDetector" in window)) return null;
  try {
    const supported = await window.BarcodeDetector.getSupportedFormats();
    const formats = RETAIL_FORMATS.filter((f) => supported.includes(f));
    if (formats.length === 0) return null;
    return new window.BarcodeDetector({ formats });
  } catch {
    return null;
  }
}

/** Poll frames through the native detector. Returns a stop function. */
function runNative(detector, videoRef, onHit) {
  let stopped = false;

  const timer = setInterval(async () => {
    const video = videoRef.current;
    // readyState < 2 means there is no frame yet; detect() on one throws.
    if (stopped || !video || video.readyState < 2) return;
    try {
      const found = await detector.detect(video);
      if (found.length > 0) onHit(found[0].rawValue);
    } catch {
      // A dropped frame is normal — the next tick tries again.
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
 * Restricted to the retail 1D formats for the same reason as the native
 * path, and because ZXing gets materially faster when it isn't trying
 * every symbology on every frame.
 */
async function runZxing(videoRef, stream, onHit) {
  // The dynamic import above gives React time to unmount underneath us —
  // 400KB is a slow first load on a store network. Without this guard
  // ZXing is handed a null element and throws where the user sees it.
  if (!videoRef.current) return () => {};

  const [{ BrowserMultiFormatOneDReader }, { BarcodeFormat, DecodeHintType }] =
    await Promise.all([import("@zxing/browser"), import("@zxing/library")]);

  if (!videoRef.current) return () => {};

  const hints = new Map([
    [
      DecodeHintType.POSSIBLE_FORMATS,
      [
        BarcodeFormat.UPC_A,
        BarcodeFormat.UPC_E,
        BarcodeFormat.EAN_13,
        BarcodeFormat.EAN_8,
      ],
    ],
  ]);

  const reader = new BrowserMultiFormatOneDReader(hints, {
    delayBetweenScanAttempts: SCAN_INTERVAL_MS,
  });

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
function describeCameraError(err) {
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
    return "No camera found. Plug one in, or type the barcode instead.";
  }
  if (name === "NotReadableError") {
    return "The camera is in use by another app. Close it (Teams, Zoom, Camera) and try again.";
  }
  return `The camera couldn't start: ${err?.message || name || "unknown error"}.`;
}
