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

/** Share of the frame ignored on each side; the guide box is what's left. */
const FRAME_INSET = 0.15;

const SHARE_COLORS = { green: "bg-lime-500", yellow: "bg-yellow-400", brown: "bg-amber-800" };

export default function RipenessCheck() {
  const videoRef = useRef(null);
  const streamRef = useRef(null);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);
  const [snapshot, setSnapshot] = useState("");
  const [devices, setDevices] = useState([]);
  const [deviceId, setDeviceId] = useState("");

  function stopCamera() {
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
  }

  // Release the camera when the tab is left, or the webcam light stays on.
  useEffect(() => stopCamera, []);

  // Labels are blank until the page has camera permission, so list cameras
  // after a stream is open. Failing to list never stops the open camera.
  async function listCameras() {
    try {
      const all = await navigator.mediaDevices.enumerateDevices();
      setDevices(all.filter((d) => d.kind === "videoinput"));
    } catch {
      // The camera already open keeps working.
    }
  }

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
      setDeviceId(stream.getVideoTracks()[0]?.getSettings().deviceId || "");
      setOpen(true);
      listCameras();
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

  async function switchCamera(id) {
    setError("");
    try {
      const next = await navigator.mediaDevices.getUserMedia({
        video: { deviceId: { exact: id }, width: { ideal: 1280 }, height: { ideal: 720 } },
        audio: false,
      });
      // Only release the old camera once the new one is open, so a camera
      // that refuses to start leaves the working one on screen.
      stopCamera();
      streamRef.current = next;
      setDeviceId(id);
      if (videoRef.current) {
        videoRef.current.srcObject = next;
        videoRef.current.play().catch(() => {});
      }
    } catch (err) {
      setError(describeCameraError(err, "ripeness"));
    }
  }

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
    // Only the middle of the frame is judged. The table, a hand and the
    // shadowed edges all read as "brown", and the fruit is meant to be in
    // the guide box, so the rest of the picture is ignored.
    const sx = video.videoWidth * FRAME_INSET;
    const sy = video.videoHeight * FRAME_INSET;
    const sw = video.videoWidth * (1 - 2 * FRAME_INSET);
    const sh = video.videoHeight * (1 - 2 * FRAME_INSET);
    const canvas = document.createElement("canvas");
    canvas.width = ANALYSIS_WIDTH;
    canvas.height = Math.round((sh / sw) * ANALYSIS_WIDTH);
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    ctx.drawImage(video, sx, sy, sw, sh, 0, 0, canvas.width, canvas.height);

    setError("");
    setSnapshot(canvas.toDataURL("image/jpeg", 0.8));
    setResult(analyzeRipeness(ctx.getImageData(0, 0, canvas.width, canvas.height)));
  }

  return (
    <Card title="Fruit freshness check">
      <p className="mb-3 text-sm text-gray-600">
        Hold a piece of fruit inside the white box against a plain background, in even light.
        The estimate comes from skin colour only — it helps decide what to donate first, and the
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
          <div className="relative w-full max-w-md overflow-hidden rounded-lg bg-black">
            <video ref={videoRef} playsInline muted className="block w-full" />
            <div
              aria-hidden="true"
              style={{ inset: `${FRAME_INSET * 100}%` }}
              className="pointer-events-none absolute rounded-lg border-2 border-white/70"
            />
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button onClick={capture} className={primary}>
              Check fruit
            </button>
            <button onClick={closeCamera} className={secondary}>
              Stop camera
            </button>
            {devices.length > 1 && (
              <select
                value={deviceId}
                onChange={(e) => switchCamera(e.target.value)}
                aria-label="Camera"
                className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-100"
              >
                {devices.map((device, index) => (
                  <option key={device.deviceId} value={device.deviceId}>
                    {device.label || `Camera ${index + 1}`}
                  </option>
                ))}
              </select>
            )}
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
