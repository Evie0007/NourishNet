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
    throw new Error(body.detail || "Your session ended. Sign in again.");
  }

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed: ${res.status}`);
  }

  // 204 No Content (logout) has no body to parse.
  if (res.status === 204) return null;
  return res.json();
}

const post = (path, data) =>
  request(path, { method: "POST", body: data === undefined ? undefined : JSON.stringify(data) });

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

  // ---- pantries ----
  registerPantry: (data) => post("/pantries", data),

  // ---- reservations ----
  createReservation: (itemId, holdMinutes = 180) =>
    post("/reservations", { item_id: itemId, hold_minutes: holdMinutes }),
  myReservations: () => request("/reservations/mine"),
  allReservations: () => request("/reservations"),
  cancelReservation: (id) => post(`/reservations/${id}/cancel`),
  confirmPickup: (qrCode) => post(`/reservations/pickup/${encodeURIComponent(qrCode)}`),
};

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
