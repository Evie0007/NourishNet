import { useEffect, useState } from "react";
import { api } from "../api";

export default function PantryPage() {
  const [pantries, setPantries] = useState([]);
  const [availableItems, setAvailableItems] = useState([]);
  const [selectedPantry, setSelectedPantry] = useState("");
  const [error, setError] = useState("");
  const [lastReservation, setLastReservation] = useState(null);

  const [pantryForm, setPantryForm] = useState({
    org_name: "",
    ein: "",
    contact_email: "",
  });
  const [qrInput, setQrInput] = useState("");

  async function refresh() {
    try {
      const [p, items] = await Promise.all([
        api.listPantries(),
        api.listItems("available"),
      ]);
      setPantries(p);
      setAvailableItems(items);
    } catch (err) {
      setError(err.message);
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  async function handleRegisterPantry(e) {
    e.preventDefault();
    setError("");
    try {
      const pantry = await api.createPantry(pantryForm);
      setPantryForm({ org_name: "", ein: "", contact_email: "" });
      setSelectedPantry(pantry.id);
      refresh();
    } catch (err) {
      setError(err.message);
    }
  }

  async function handleReserve(itemId) {
    setError("");
    if (!selectedPantry) {
      setError("Select or register a pantry first.");
      return;
    }
    try {
      const reservation = await api.createReservation({
        item_id: itemId,
        pantry_id: selectedPantry,
      });
      setLastReservation(reservation);
      refresh();
    } catch (err) {
      setError(err.message);
    }
  }

  async function handleConfirmPickup(e) {
    e.preventDefault();
    setError("");
    try {
      const reservation = await api.confirmPickup(qrInput.trim());
      setLastReservation(reservation);
      setQrInput("");
      refresh();
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <div className="space-y-8">
      <h1 className="text-2xl font-semibold">Pantry view</h1>
      {error && <p className="rounded bg-red-50 p-3 text-red-700">{error}</p>}

      <section className="space-y-3">
        <h2 className="font-medium">Your pantry</h2>
        <select
          className="w-full rounded border border-gray-300 px-3 py-1.5"
          value={selectedPantry}
          onChange={(e) => setSelectedPantry(e.target.value)}
        >
          <option value="">Select an existing pantry</option>
          {pantries.map((p) => (
            <option key={p.id} value={p.id}>
              {p.org_name}
            </option>
          ))}
        </select>

        <form onSubmit={handleRegisterPantry} className="flex flex-wrap gap-2">
          <input
            className="rounded border border-gray-300 px-3 py-1.5"
            placeholder="Organization name"
            value={pantryForm.org_name}
            onChange={(e) =>
              setPantryForm({ ...pantryForm, org_name: e.target.value })
            }
            required
          />
          <input
            className="rounded border border-gray-300 px-3 py-1.5"
            placeholder="EIN"
            value={pantryForm.ein}
            onChange={(e) => setPantryForm({ ...pantryForm, ein: e.target.value })}
            required
          />
          <input
            className="rounded border border-gray-300 px-3 py-1.5"
            placeholder="Contact email"
            type="email"
            value={pantryForm.contact_email}
            onChange={(e) =>
              setPantryForm({ ...pantryForm, contact_email: e.target.value })
            }
            required
          />
          <button className="rounded bg-gray-900 px-3 py-1.5 text-white">
            Register new pantry
          </button>
        </form>
      </section>

      <section className="space-y-3">
        <h2 className="font-medium">Available donations</h2>
        <ul className="divide-y divide-gray-200 rounded border border-gray-200 bg-white">
          {availableItems.length === 0 && (
            <li className="px-3 py-2 text-sm text-gray-500">
              Nothing available right now.
            </li>
          )}
          {availableItems.map((item) => (
            <li
              key={item.id}
              className="flex items-center justify-between px-3 py-2 text-sm"
            >
              <span>{item.name}</span>
              <button
                onClick={() => handleReserve(item.id)}
                className="rounded border border-gray-300 px-2 py-1 text-xs hover:bg-gray-100"
              >
                Reserve
              </button>
            </li>
          ))}
        </ul>
      </section>

      {lastReservation && (
        <section className="space-y-1 rounded border border-gray-200 bg-white p-4 text-sm">
          <h2 className="font-medium">Latest reservation</h2>
          <p>Status: {lastReservation.status}</p>
          <p>QR code: {lastReservation.qr_code}</p>
          <p>Hold expires: {new Date(lastReservation.hold_expires_at).toLocaleString()}</p>
        </section>
      )}

      <section className="space-y-3">
        <h2 className="font-medium">Confirm pickup</h2>
        <form onSubmit={handleConfirmPickup} className="flex gap-2">
          <input
            className="flex-1 rounded border border-gray-300 px-3 py-1.5"
            placeholder="Paste QR code"
            value={qrInput}
            onChange={(e) => setQrInput(e.target.value)}
            required
          />
          <button className="rounded bg-gray-900 px-3 py-1.5 text-white">
            Confirm pickup
          </button>
        </form>
      </section>
    </div>
  );
}
