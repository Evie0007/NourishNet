import { useEffect, useRef, useState } from "react";
import { analyzeRipeness, describeDaysLeft } from "../ripeness";
import { describeCameraError } from "../cameraError";
import { openPreferredCamera } from "../preferredCamera";
import { Card } from "./Shell";

/**
 * Freshness check for a banana, from the computer's camera.
 *
 * Advisory only. It never writes an Item — it tells the person at the desk
 * how ripe the fruit looks so they can decide what to donate first, and the
 * printed date and their own judgement still govern. That keeps it clear of
 * the rule that nothing reaches inventory without a confirmation.
 *
 * Every failure here degrades to "look at the banana": no camera, blocked
 * permission, or an unreadable frame just shows a message, and nothing else
 * on the intake page depends on this card.
 */

const primary =
  "rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-60";
const secondary =
  "rounded-lg border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-60";

/** Frames are shrunk before analysis: colour shares don't need 1080p, and a
 *  320px-wide frame is ~75k pixels, which is instant. */
const ANALYSIS_WIDTH = 320;

const SHARE_COLORS = { green: "bg-lime-500", yellow: "bg-yellow-400", brown: "bg-amber-800" };

export default function RipenessCheck() {
  const videoRef = useRef(null);
  const streamRef = useRef(null);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);
  const [snapshot, setSnapshot] = useState("");

  function stopCamera() {
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
  }

  // Release the camera when the tab is left, or the webcam light stays on.
  useEffect(() => stopCamera, []);

  async function startCamera() {
    setError("");
    setResult(null);
    setSnapshot("");
    if (!navigator.mediaDevices?.getUserMedia) {
      setError(describeCameraError(null, "ripeness"));
      return;
    }
    try {
      const stream = await openPreferredCamera({
        width: { ideal: 1280 },
        height: { ideal: 720 },
      });
      streamRef.current = stream;
      setOpen(true);
    } catch (err) {
      setError(describeCameraError(err, "ripeness"));
    }
  }

  // The <video> only exists once `open` is true, so attach the stream then.
  useEffect(() => {
    if (open && videoRef.current && streamRef.current) {
      videoRef.current.srcObject = streamRef.current;
      videoRef.current.play().catch(() => {});
    }
  }, [open]);

  function closeCamera() {
    stopCamera();
    setOpen(false);
  }

  function capture() {
    const video = videoRef.current;
    if (!video || !video.videoWidth) {
      setError("The camera isn't ready yet — wait a second and try again.");
      return;
    }
    const scale = ANALYSIS_WIDTH / video.videoWidth;
    const canvas = document.createElement("canvas");
    canvas.width = ANALYSIS_WIDTH;
    canvas.height = Math.round(video.videoHeight * scale);
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);

    setError("");
    setSnapshot(canvas.toDataURL("image/jpeg", 0.8));
    setResult(analyzeRipeness(ctx.getImageData(0, 0, canvas.width, canvas.height)));
  }

  return (
    <Card title="Banana freshness check">
      <p className="mb-3 text-sm text-gray-600">
        Hold a banana in front of the camera against a plain background, in even light. The
        estimate comes from peel colour only — it helps decide what to donate first, and the
        printed date still governs.
      </p>

      {error && (
        <div role="alert" className="mb-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">
          {error}
        </div>
      )}

      {!open ? (
        <button onClick={startCamera} className={primary}>
          Start camera
        </button>
      ) : (
        <div className="space-y-3">
          <video
            ref={videoRef}
            playsInline
            muted
            className="w-full max-w-md rounded-lg bg-black"
          />
          <div className="flex gap-2">
            <button onClick={capture} className={primary}>
              Check banana
            </button>
            <button onClick={closeCamera} className={secondary}>
              Stop camera
            </button>
          </div>
        </div>
      )}

      {result && (
        <div className="mt-4 flex flex-wrap items-start gap-4">
          {snapshot && (
            <img src={snapshot} alt="Captured frame" className="w-48 rounded-lg border border-gray-200" />
          )}
          {result.found ? (
            <div className="min-w-0 space-y-2 text-sm">
              <div className="text-lg font-semibold">{result.label}</div>
              <div>
                Estimated shelf life: <strong>{describeDaysLeft(result.daysLeft)}</strong>
              </div>
              <div className="flex h-3 w-64 overflow-hidden rounded-full bg-gray-100">
                {["green", "yellow", "brown"].map((k) => (
                  <div
                    key={k}
                    className={SHARE_COLORS[k]}
                    style={{ width: `${(result.share[k] * 100).toFixed(1)}%` }}
                  />
                ))}
              </div>
              <div className="text-xs text-gray-500">
                {["green", "yellow", "brown"]
                  .map((k) => `${Math.round(result.share[k] * 100)}% ${k}`)
                  .join(" · ")}
              </div>
            </div>
          ) : (
            <div className="text-sm text-amber-700">{result.reason}</div>
          )}
        </div>
      )}
    </Card>
  );
}
