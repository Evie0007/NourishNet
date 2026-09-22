const BASE_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";

const TOKEN_KEY = "nourishnet.token";

export const getToken = () => localStorage.getItem(TOKEN_KEY);
export const setToken = (token) => localStorage.setItem(TOKEN_KEY, token);
export const clearToken = () => localStorage.removeItem(TOKEN_KEY);

// Set by AuthProvider. Any 401 means the token is gone or expired, so the
// whole app drops back to the login screen rather than each page inventing
// its own handling.
let onUnauthorized = () => {};
export const setUnauthorizedHandler = (fn) => {
  onUnauthorized = fn;
};

/**
 * Pull a displayable message out of an error body.
 *
 * FastAPI returns a string `detail` for the errors we raise by hand, but
 * Pydantic validation failures return an *array* of {loc, msg, type}
 * objects. Passing that array straight to `new Error` renders the string
 * "[object Object]", which is what the user would have seen for every
 * out-of-range pickup time.
 */
function detailMessage(body, fallback) {
  const detail = body?.detail;
  if (Array.isArray(detail)) {
    const messages = detail
      // Pydantic prefixes messages raised from a validator with
      // "Value error, ", which is noise to the person reading it.
      .map((e) => String(e?.msg || "").replace(/^Value error, /, ""))
      .filter(Boolean);
    return messages.join(" ") || fallback;
  }
  return detail || fallback;
}

async function request(path, options = {}) {
  const token = getToken();
  const res = await fetch(`${BASE_URL}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...options.headers,
    },
  });

  if (res.status === 401) {
    clearToken();
    onUnauthorized();
    const body = await res.json().catch(() => ({}));
    throw new Error(detailMessage(body, "Your session ended. Sign in again."));
  }

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(detailMessage(body, `Request failed: ${res.status}`));
  }

  // 204 No Content (logout) has no body to parse.
  if (res.status === 204) return null;
  return res.json();
}

const post = (path, data) =>
  request(path, { method: "POST", body: data === undefined ? undefined : JSON.stringify(data) });

const patch = (path, data) =>
  request(path, { method: "PATCH", body: JSON.stringify(data) });

/**
 * Multipart upload for the label photo. Deliberately not routed through
 * `request`: that helper sets Content-Type to application/json, and a
 * multipart body needs the browser to set it, boundary included. Setting
 * it by hand produces a request the server cannot parse.
 */
async function postFile(path, file, field = "image") {
  const token = getToken();
  const body = new FormData();
  body.append(field, file, file.name || "label.jpg");

  const res = await fetch(`${BASE_URL}${path}`, {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body,
  });

  if (res.status === 401) {
    clearToken();
    onUnauthorized();
    throw new Error("Your session ended. Sign in again.");
  }
  if (!res.ok) {
    const payload = await res.json().catch(() => ({}));
    throw new Error(detailMessage(payload, `Upload failed: ${res.status}`));
  }
  return res.json();
}

export const api = {
  // ---- auth ----
  login: (email, password) => post("/auth/login", { email, password }),
  me: () => request("/auth/me"),
  logout: () => post("/auth/logout"),

  // ---- shelves (staff) ----
  listShelves: () => request("/shelves"),
  createShelf: (data) => post("/shelves", data),

  // ---- items ----
  // The backend forces status=available for organizers, so this same call
  // returns the donation pool for them and the full inventory for staff.
  listItems: (status) => request(status ? `/items?status=${status}` : "/items"),
  createItem: (data) => post("/items", data),
  updateItemStatus: (itemId, status) =>
    request(`/items/${itemId}/status`, { method: "PATCH", body: JSON.stringify({ status }) }),
  listNearExpiry: (withinHours = 48) => request(`/items/near-expiry?within_hours=${withinHours}`),
  submitOcrResult: (itemId, data) => post(`/items/${itemId}/ocr-result`, data),
  // Manager-only. The scheduler runs this every minute on its own; this is
  // for seeing the rules act without waiting for the next tick.
  runExpirationSweep: () => post("/items/run-expiration-sweep"),

  // ---- catalog (UPC scanner + expiration rules) ----
  // A code nothing recognizes comes back 200 with product: null — an
  // ordinary outcome at the dock, not an error to show the user.
  lookupUpc: (upc) => request(`/catalog/upc/${encodeURIComponent(upc)}`),
  listProducts: (search) =>
    request(search ? `/catalog/products?search=${encodeURIComponent(search)}` : "/catalog/products"),
  createProduct: (data) => post("/catalog/products", data),
  updateProduct: (id, data) => patch(`/catalog/products/${id}`, data),
  listExpirationRules: () => request("/catalog/expiration-rules"),
  updateExpirationRule: (id, data, recompute = false) =>
    patch(`/catalog/expiration-rules/${id}?recompute=${recompute}`, data),

  // ---- intake (UPC → OCR date → manual confirmation) ----
  openScan: (data) => post("/intake/scans", data),
  listScans: (status) =>
    request(status ? `/intake/scans?status=${status}` : "/intake/scans"),
  submitScanDate: (scanId, data) => post(`/intake/scans/${scanId}/date`, data),
  submitScanImage: (scanId, file) => postFile(`/intake/scans/${scanId}/ocr-image`, file),
  confirmScan: (scanId, data) => post(`/intake/scans/${scanId}/confirm`, data),
  rejectScan: (scanId, notes) => post(`/intake/scans/${scanId}/reject`, { notes: notes || null }),

  // ---- pantries ----
  registerPantry: (data) => post("/pantries", data),
  listPantries: () => request("/pantries"),

  // ---- reservations ----
  // `scheduledPickupLocal` is the raw value out of an
  // <input type="datetime-local">. The hold is derived from it server-side
  // (slot + 30 minutes), so there is no hold length to pass.
  createReservation: (itemId, scheduledPickupLocal) =>
    post("/reservations", {
      item_id: itemId,
      scheduled_pickup_at: toUtcIso(scheduledPickupLocal),
    }),
  myReservations: () => request("/reservations/mine"),
  allReservations: () => request("/reservations"),
  cancelReservation: (id) => post(`/reservations/${id}/cancel`),
  confirmPickup: (qrCode) => post(`/reservations/pickup/${encodeURIComponent(qrCode)}`),

  // ---- donation reports (FR-11.1 / FR-11.3), staff-only ----
  listDonations: (filters) => request(`/reports/donations${donationQuery(filters)}`),
  // A plain <a href> can't carry the bearer token, so this fetches the CSV
  // as a blob and hands the browser a throwaway object URL to save it from.
  downloadDonationsCsv: async (filters) => {
    const token = getToken();
    const res = await fetch(`${BASE_URL}/reports/donations/export${donationQuery(filters)}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (res.status === 401) {
      clearToken();
      onUnauthorized();
      throw new Error("Your session ended. Sign in again.");
    }
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(detailMessage(body, `Export failed: ${res.status}`));
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `donations_${new Date().toISOString().slice(0, 10)}.csv`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  },
};

function donationQuery({ from, to, pantryId } = {}) {
  const params = new URLSearchParams();
  if (from) params.set("date_from", from);
  if (to) params.set("date_to", to);
  if (pantryId) params.set("pantry_id", pantryId);
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}

/**
 * The API serializes datetime.utcnow() with no timezone suffix, e.g.
 * "2026-08-31T19:30:00". `new Date()` reads that as *local* time, which
 * silently shifts every hold-expiry countdown by the viewer's UTC offset —
 * 7 hours in California. Appending Z forces the correct reading.
 *
 * Delete this once the backend moves to timezone-aware datetimes
 * (NFR-4.7.5 already calls for datetime.now(timezone.utc)).
 */
export function parseUtc(value) {
  if (!value) return null;
  const hasZone = /(Z|[+-]\d{2}:?\d{2})$/.test(value);
  return new Date(hasZone ? value : `${value}Z`);
}

/**
 * An <input type="datetime-local"> value ("2026-09-19T14:30") is local
 * wall time with no zone. Convert it to an offset-bearing instant before
 * sending — the API rejects a naive datetime outright, precisely so this
 * conversion cannot be skipped by accident.
 *
 * `new Date()` on a string is correct here and nowhere else in this file:
 * the offsetless datetime-local form is specified to be read as local, and
 * this value came from a form rather than from the API. The house rule
 * against `new Date()` is about values the backend sent us.
 */
export function toUtcIso(localValue) {
  if (!localValue) return null;
  const d = new Date(localValue);
  return Number.isNaN(d.getTime()) ? null : d.toISOString();
}

/**
 * The inverse: a Date to the string a datetime-local control expects.
 *
 * Deliberately not `toISOString().slice(0, 16)`, which yields UTC. The
 * control reads its value, min and max as local wall time, so a UTC string
 * shifts every bound by the viewer's offset — the same class of bug
 * parseUtc exists to prevent, just pointing the other way.
 */
/** `null`/`undefined` renders as "—" — most items have no catalog value set,
 * and that's an ordinary, expected state, not a zero dollar amount. */
export function formatCurrency(value) {
  if (value === null || value === undefined || value === "") return "—";
  const n = Number(value);
  return Number.isNaN(n) ? "—" : new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(n);
}

export function toLocalInputValue(date) {
  if (!date || Number.isNaN(date.getTime())) return "";
  const pad = (n) => String(n).padStart(2, "0");
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    `T${pad(date.getHours())}:${pad(date.getMinutes())}`
  );
}
