const BASE_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";

async function request(path, options = {}) {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed: ${res.status}`);
  }
  return res.json();
}

export const api = {
  listShelves: () => request("/shelves"),
  createShelf: (data) =>
    request("/shelves", { method: "POST", body: JSON.stringify(data) }),

  listItems: (status) =>
    request(status ? `/items?status=${status}` : "/items"),
  createItem: (data) =>
    request("/items", { method: "POST", body: JSON.stringify(data) }),
  submitOcrResult: (itemId, data) =>
    request(`/items/${itemId}/ocr-result`, {
      method: "POST",
      body: JSON.stringify(data),
    }),

  listPantries: () => request("/pantries"),
  createPantry: (data) =>
    request("/pantries", { method: "POST", body: JSON.stringify(data) }),

  createReservation: (data) =>
    request("/reservations", { method: "POST", body: JSON.stringify(data) }),
  confirmPickup: (qrCode) =>
    request(`/reservations/pickup/${qrCode}`, { method: "POST" }),
};
