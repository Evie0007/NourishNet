/**
 * Open the camera this desk prefers, falling back to whatever the browser
 * would have picked.
 *
 * The desk has an Acer A640 webcam that reads barcodes and fruit far better
 * than the laptop's built-in one, but browsers default to the built-in
 * camera. Device labels are blank until the page has camera permission, so
 * the preferred camera can't be looked up first: open the default, then
 * switch if the preferred one turns out to be present. Any failure in the
 * switch keeps the camera that already works — a missing Acer must never
 * block receiving.
 */
const PREFERRED_CAMERA = /acer\s*a640/i;

async function findPreferredCameraId() {
  try {
    const all = await navigator.mediaDevices.enumerateDevices();
    return all.find((d) => d.kind === "videoinput" && PREFERRED_CAMERA.test(d.label))?.deviceId;
  } catch {
    return undefined;
  }
}

/** `video` is the usual getUserMedia video constraints object, minus deviceId. */
export async function openPreferredCamera(video) {
  const stream = await navigator.mediaDevices.getUserMedia({ video, audio: false });

  const preferredId = await findPreferredCameraId();
  const currentId = stream.getVideoTracks()[0]?.getSettings().deviceId;
  if (!preferredId || preferredId === currentId) return stream;

  try {
    const preferred = await navigator.mediaDevices.getUserMedia({
      video: { ...video, deviceId: { exact: preferredId } },
      audio: false,
    });
    stream.getTracks().forEach((t) => t.stop());
    return preferred;
  } catch {
    return stream;
  }
}
