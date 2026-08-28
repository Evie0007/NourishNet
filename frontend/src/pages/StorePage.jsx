import { useEffect, useState } from "react";
import { api } from "../api";

export default function StorePage() {
  const [shelves, setShelves] = useState([]);
  const [items, setItems] = useState([]);
  const [error, setError] = useState("");

  const [shelfForm, setShelfForm] = useState({ name: "", location: "" });
  const [itemForm, setItemForm] = useState({ name: "", sku: "", shelf_id: "" });

  async function refresh() {
    try {
      const [s, i] = await Promise.all([api.listShelves(), api.listItems()]);
      setShelves(s);
      setItems(i);
    } catch (err) {
      setError(err.message);
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  async function handleCreateShelf(e) {
    e.preventDefault();
    setError("");
    try {
      await api.createShelf(shelfForm);
      setShelfForm({ name: "", location: "" });
      refresh();
    } catch (err) {
      setError(err.message);
    }
  }

  async function handleCreateItem(e) {
    e.preventDefault();
    setError("");
    try {
      await api.createItem(itemForm);
      setItemForm({ name: "", sku: "", shelf_id: "" });
      refresh();
    } catch (err) {
      setError(err.message);
    }
  }

  async function handleSimulateOcr(itemId) {
    setError("");
    try {
      await api.submitOcrResult(itemId, {
        ocr_confidence: 0.97,
        sku_match_confirmed: true,
      });
      refresh();
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <div className="space-y-8">
      <h1 className="text-2xl font-semibold">Store view</h1>
      {error && <p className="rounded bg-red-50 p-3 text-red-700">{error}</p>}

      <section className="space-y-3">
        <h2 className="font-medium">Shelves</h2>
        <form onSubmit={handleCreateShelf} className="flex flex-wrap gap-2">
          <input
            className="rounded border border-gray-300 px-3 py-1.5"
            placeholder="Shelf name"
            value={shelfForm.name}
            onChange={(e) => setShelfForm({ ...shelfForm, name: e.target.value })}
            required
          />
          <input
            className="rounded border border-gray-300 px-3 py-1.5"
            placeholder="Location"
            value={shelfForm.location}
            onChange={(e) =>
              setShelfForm({ ...shelfForm, location: e.target.value })
            }
          />
          <button className="rounded bg-gray-900 px-3 py-1.5 text-white">
            Add shelf
          </button>
        </form>
        <ul className="divide-y divide-gray-200 rounded border border-gray-200 bg-white">
          {shelves.map((s) => (
            <li key={s.id} className="px-3 py-2 text-sm">
              {s.name} — {s.location || "no location"}
            </li>
          ))}
        </ul>
      </section>

      <section className="space-y-3">
        <h2 className="font-medium">Items</h2>
        <form onSubmit={handleCreateItem} className="flex flex-wrap gap-2">
          <input
            className="rounded border border-gray-300 px-3 py-1.5"
            placeholder="Item name"
            value={itemForm.name}
            onChange={(e) => setItemForm({ ...itemForm, name: e.target.value })}
            required
          />
          <input
            className="rounded border border-gray-300 px-3 py-1.5"
            placeholder="SKU"
            value={itemForm.sku}
            onChange={(e) => setItemForm({ ...itemForm, sku: e.target.value })}
          />
          <select
            className="rounded border border-gray-300 px-3 py-1.5"
            value={itemForm.shelf_id}
            onChange={(e) =>
              setItemForm({ ...itemForm, shelf_id: e.target.value })
            }
            required
          >
            <option value="">Select shelf</option>
            {shelves.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
          <button className="rounded bg-gray-900 px-3 py-1.5 text-white">
            Add item
          </button>
        </form>

        <ul className="divide-y divide-gray-200 rounded border border-gray-200 bg-white">
          {items.map((item) => (
            <li
              key={item.id}
              className="flex items-center justify-between px-3 py-2 text-sm"
            >
              <span>
                {item.name} — <span className="text-gray-500">{item.status}</span>
              </span>
              {item.status === "in_stock" && (
                <button
                  onClick={() => handleSimulateOcr(item.id)}
                  className="rounded border border-gray-300 px-2 py-1 text-xs hover:bg-gray-100"
                >
                  Simulate OCR scan
                </button>
              )}
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
