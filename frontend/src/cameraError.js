/**
 * Turn a getUserMedia rejection into something a person can act on.
 *
 * NFR-4.5.5: every error says what happened and what to do about it. The
 * insecure-context case especially — "NotAllowedError" gives no hint that
 * the real problem is the URL, and someone will otherwise spend an
 * afternoon re-granting a permission that was never the issue.
 */
export function describeCameraError(err, subject = "barcode") {
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
