import { useCallback, useEffect, useMemo, useState } from "react";
import { api, parseUtc } from "../api";
import Shell, { Card, Empty, ErrorBanner, StatusBadge } from "../components/Shell";

const TABS = [
  { key: "overview", label: "Overview" },
  { key: "review", label: "Review queue" },
  { key: "inventory", label: "Inventory" },
  { key: "shelves", label: "Shelves" },
];

/** Reading older than this means the sensor or its network is down, not
 *  that conditions are fine (FR-4.7, NFR-4.4.4). */
const STALE_READING_MINUTES = 15;

export default function StaffDashboard() {
  const [items, setItems] = useState([]);
  const [shelves, setShelves] = useState([]);
  const [nearExpiry, setNearExpiry] = useState([]);
  const [error, setError] = useState("");
  const [tab, setTab] = useState("overview");
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const [i, s, n] = await Promise.all([
        api.listItems(),
        api.listShelves(),
        api.listNearExpiry(48),
      ]);
      setItems(i);
      setShelves(s);
      setNearExpiry(n);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const reviewQueue = useMemo(
    () =>
      items
        .filter((i) => i.status === "needs_review")
        // FR-3.2: oldest first — the longest-waiting item is the most urgent.
        .sort((a, b) => new Date(a.created_at) - new Date(b.created_at)),
    [items],
  );

  const counts = useMemo(() => {
    const acc = {};
    for (const item of items) acc[item.status] = (acc[item.status] || 0) + 1;
    return acc;
  }, [items]);

  const shelfName = useCallback(
    (id) => shelves.find((s) => s.id === id)?.name || "Unassigned",
    [shelves],
  );

  return (
    <Shell
      title="Store dashboard"
      subtitle="Review label reads, publish donations, and confirm pickups at the shelf."
    >
      <ErrorBanner message={error} onDismiss={() => setError("")} />

      <nav className="mb-6 flex flex-wrap gap-1 border-b border-gray-200">
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            aria-current={tab === t.key ? "page" : undefined}
            className={`-mb-px border-b-2 px-4 py-2 text-sm font-medium ${
              tab === t.key
                ? "border-emerald-600 text-emerald-700"
                : "border-transparent text-gray-500 hover:text-gray-800"
            }`}
          >
            {t.label}
            {t.key === "review" && reviewQueue.length > 0 && (
              <span className="ml-2 rounded-full bg-amber-100 px-1.5 py-0.5 text-xs text-amber-800">
                {reviewQueue.length}
              </span>
            )}
          </button>
        ))}
      </nav>

      {loading ? (
        <p className="text-sm text-gray-500">Loading…</p>
      ) : (
        <>
          {tab === "overview" && (
            <Overview
              counts={counts}
              nearExpiry={nearExpiry}
              shelves={shelves}
              shelfName={shelfName}
              onError={setError}
              onChanged={refresh}
            />
          )}
          {tab === "review" && (
            <ReviewQueue queue={reviewQueue} shelfName={shelfName} onError={setError} onChanged={refresh} />
          )}
          {tab === "inventory" && (
            <Inventory items={items} shelves={shelves} shelfName={shelfName} onError={setError} onChanged={refresh} />
          )}
          {tab === "shelves" && <Shelves shelves={shelves} onError={setError} onChanged={refresh} />}
        </>
      )}
    </Shell>
  );
}

/* ---------------- Overview ---------------- */

const SUMMARY_ORDER = [
  ["needs_review", "Needs review"],
  ["available", "Available"],
  ["reserved", "Reserved"],
  ["in_stock", "In stock"],
  ["picked_up", "Picked up"],
  ["discarded", "Discarded"],
];

function Overview({ counts, nearExpiry, shelves, shelfName, onError, onChanged }) {
  return (
    <div className="space-y-6">
      {/* FR-3.1 */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        {SUMMARY_ORDER.map(([key, label]) => (
          <div key={key} className="rounded-xl border border-gray-200 bg-white p-4">
            <div className="text-2xl font-semibold tabular-nums">{counts[key] || 0}</div>
            <div className="mt-1 text-xs text-gray-500">{label}</div>
          </div>
        ))}
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <PickupScan onError={onError} onChanged={onChanged} />

        {/* FR-3.4 — the backend endpoint existed all along, unused. */}
        <Card title="Near expiry (next 48h)">
          {nearExpiry.length === 0 ? (
            <Empty>Nothing approaching its sell-by date.</Empty>
          ) : (
            <ul className="divide-y divide-gray-100">
              {nearExpiry.map((item) => (
                <li key={item.id} className="flex items-center justify-between gap-3 py-2 text-sm">
                  <div>
                    <div className="font-medium">{item.name}</div>
                    <div className="text-xs text-gray-500">{shelfName(item.shelf_id)}</div>
                  </div>
                  <div className="text-right text-xs text-gray-600">
                    <div>{formatDate(item.sell_by_date)}</div>
                    <div className="text-gray-400">{relativeTo(item.sell_by_date)}</div>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      {/* FR-3.6 */}
      <Card title="Shelf conditions">
        {shelves.length === 0 ? (
          <Empty>No shelves registered yet.</Empty>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {shelves.map((shelf) => (
              <ShelfConditionCard key={shelf.id} shelf={shelf} />
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}

function ShelfConditionCard({ shelf }) {
  const readingAt = parseUtc(shelf.last_reading_at);
  const ageMinutes = readingAt ? (Date.now() - readingAt.getTime()) / 60000 : null;
  const stale = ageMinutes === null || ageMinutes > STALE_READING_MINUTES;

  return (
    <div className="rounded-lg border border-gray-200 p-3">
      <div className="font-medium">{shelf.name}</div>
      <div className="text-xs text-gray-500">{shelf.location || "No location set"}</div>
      <div className="mt-2 flex items-baseline gap-3 text-sm">
        <span className="tabular-nums">
          {shelf.current_temperature_c != null ? `${shelf.current_temperature_c}°C` : "—"}
        </span>
        <span className="tabular-nums text-gray-500">
          {shelf.current_humidity_pct != null ? `${shelf.current_humidity_pct}% RH` : "—"}
        </span>
      </div>
      <div className={`mt-1 text-xs ${stale ? "font-medium text-amber-700" : "text-gray-400"}`}>
        {stale ? "⚠ No recent reading — check the sensor" : `Updated ${relativeTo(shelf.last_reading_at)}`}
      </div>
    </div>
  );
}

/* ---------------- Pickup (FR-9.4, FR-9.10) ---------------- */

function PickupScan({ onError, onChanged }) {
  const [code, setCode] = useState("");
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);

  async function handleSubmit(e) {
    e.preventDefault();
    onError("");
    setResult(null);
    setBusy(true);
    try {
      const reservation = await api.confirmPickup(code.trim());
      setResult(reservation);
      setCode("");
      onChanged();
    } catch (err) {
      onError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="Confirm pickup">
      <p className="mb-3 text-sm text-gray-500">
        Scan or type the code from the organizer's phone. Staff confirm the
        handoff — the pantry cannot confirm its own.
      </p>
      <form onSubmit={handleSubmit} className="flex gap-2">
        <input
          value={code}
          onChange={(e) => setCode(e.target.value)}
          required
          placeholder="Pickup code"
          aria-label="Pickup code"
          className="flex-1 rounded-lg border border-gray-300 px-3 py-2 font-mono text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-100"
        />
        <button
          disabled={busy}
          className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-60"
        >
          {busy ? "…" : "Confirm"}
        </button>
      </form>
      {result && (
        <div className="mt-3 rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-900">
          Collected: <span className="font-medium">{result.item_name}</span> by{" "}
          {result.pantry_name} at {formatTime(result.picked_up_at)}.
        </div>
      )}
    </Card>
  );
}

/* ---------------- Review queue (FR-3.2, FR-3.3) ---------------- */

function ReviewQueue({ queue, shelfName, onError, onChanged }) {
  if (queue.length === 0) {
    return (
      <Card title="Review queue">
        <Empty>Nothing waiting on review. Every label read cleared the confidence branch.</Empty>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      <p className="text-sm text-gray-600">
        These label reads didn't clear the 95% confidence branch, so no one gets
        this food until a person confirms the date.
      </p>
      {queue.map((item) => (
        <ReviewCard
          key={item.id}
          item={item}
          shelfName={shelfName}
          onError={onError}
          onChanged={onChanged}
        />
      ))}
    </div>
  );
}

function ReviewCard({ item, shelfName, onError, onChanged }) {
  const [sellBy, setSellBy] = useState(toDateInput(item.sell_by_date));
  const [busy, setBusy] = useState(false);

  const lowConfidence = item.ocr_confidence != null && item.ocr_confidence < 0.95;

  async function decide(status) {
    onError("");
    setBusy(true);
    try {
      // Correct the date first so the published item carries the value the
      // reviewer actually confirmed, not the one OCR guessed (FR-3.3).
      if (status === "available" && sellBy !== toDateInput(item.sell_by_date)) {
        await api.submitOcrResult(item.id, {
          ocr_raw_text: item.ocr_raw_text,
          // A human confirmed it, so the read is now trustworthy by
          // definition — that's what clears the branch in crud.py.
          ocr_confidence: 1.0,
          sku_match_confirmed: true,
          sell_by_date: new Date(`${sellBy}T00:00:00Z`).toISOString(),
        });
      } else {
        await api.updateItemStatus(item.id, status);
      }
      onChanged();
    } catch (err) {
      onError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h3 className="font-medium">{item.name}</h3>
            <StatusBadge status={item.status} />
          </div>
          <p className="mt-0.5 text-xs text-gray-500">
            {shelfName(item.shelf_id)} · SKU {item.sku || "unresolved"} · added{" "}
            {relativeTo(item.created_at)}
          </p>

          <dl className="mt-3 space-y-1 text-sm">
            <div className="flex gap-2">
              <dt className="text-gray-500">Confidence</dt>
              <dd className={lowConfidence ? "font-medium text-amber-700" : ""}>
                {item.ocr_confidence != null ? `${(item.ocr_confidence * 100).toFixed(0)}%` : "—"}
                {lowConfidence && " — below the 95% threshold"}
                {!lowConfidence && !item.sku_match_confirmed && " — but the SKU cross-check failed"}
              </dd>
            </div>
            <div className="flex gap-2">
              <dt className="shrink-0 text-gray-500">Label read</dt>
              <dd className="min-w-0 break-words font-mono text-xs text-gray-700">
                {item.ocr_raw_text || "No text captured"}
              </dd>
            </div>
          </dl>
        </div>

        {item.image_url && (
          <img
            src={item.image_url}
            alt={`Captured label for ${item.name}`}
            className="h-28 w-28 shrink-0 rounded-lg border border-gray-200 object-cover"
          />
        )}
      </div>

      <div className="mt-4 flex flex-wrap items-end gap-3 border-t border-gray-100 pt-4">
        <div>
          <label
            htmlFor={`sellby-${item.id}`}
            className="block text-xs font-medium text-gray-600"
          >
            Confirm sell-by date
          </label>
          <input
            id={`sellby-${item.id}`}
            type="date"
            value={sellBy}
            onChange={(e) => setSellBy(e.target.value)}
            className="mt-1 rounded-lg border border-gray-300 px-3 py-1.5 text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-100"
          />
        </div>
        <button
          onClick={() => decide("available")}
          disabled={busy || !sellBy}
          className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-60"
        >
          Publish to donation network
        </button>
        {/* FR-6.7: discarding must never be impeded — no confirmation gate,
            no required reason. */}
        <button
          onClick={() => decide("discarded")}
          disabled={busy}
          className="rounded-lg border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-60"
        >
          Discard
        </button>
      </div>
    </Card>
  );
}

/* ---------------- Inventory ---------------- */

function Inventory({ items, shelves, shelfName, onError, onChanged }) {
  const [filter, setFilter] = useState("");
  const [shelfFilter, setShelfFilter] = useState("");
  const [form, setForm] = useState({ name: "", sku: "", category: "", shelf_id: "", sell_by_date: "" });
  const [busy, setBusy] = useState(false);

  const visible = items.filter(
    (i) => (!filter || i.status === filter) && (!shelfFilter || i.shelf_id === shelfFilter),
  );

  async function handleCreate(e) {
    e.preventDefault();
    onError("");
    setBusy(true);
    try {
      await api.createItem({
        name: form.name,
        sku: form.sku || null,
        category: form.category || null,
        shelf_id: form.shelf_id || null,
        sell_by_date: form.sell_by_date
          ? new Date(`${form.sell_by_date}T00:00:00Z`).toISOString()
          : null,
      });
      setForm({ name: "", sku: "", category: "", shelf_id: "", sell_by_date: "" });
      onChanged();
    } catch (err) {
      onError(err.message);
    } finally {
      setBusy(false);
    }
  }

  const input = "rounded-lg border border-gray-300 px-3 py-1.5 text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-100";

  return (
    <div className="space-y-6">
      {/* FR-3.8 — now carries category and sell-by date, not just name/SKU. */}
      <Card title="Add an item">
        <form onSubmit={handleCreate} className="flex flex-wrap items-end gap-2">
          <input
            className={input}
            placeholder="Item name"
            required
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
          />
          <input
            className={input}
            placeholder="SKU"
            value={form.sku}
            onChange={(e) => setForm({ ...form, sku: e.target.value })}
          />
          <input
            className={input}
            placeholder="Category"
            value={form.category}
            onChange={(e) => setForm({ ...form, category: e.target.value })}
          />
          <select
            className={input}
            value={form.shelf_id}
            onChange={(e) => setForm({ ...form, shelf_id: e.target.value })}
            aria-label="Shelf"
          >
            <option value="">No shelf</option>
            {shelves.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
          <div>
            <label className="block text-xs text-gray-500">Sell-by</label>
            <input
              type="date"
              className={input}
              value={form.sell_by_date}
              onChange={(e) => setForm({ ...form, sell_by_date: e.target.value })}
            />
          </div>
          <button
            disabled={busy}
            className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-60"
          >
            Add item
          </button>
        </form>
      </Card>

      {/* FR-3.5 */}
      <Card
        title={`Inventory (${visible.length})`}
        action={
          <div className="flex gap-2">
            <select
              className={input}
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              aria-label="Filter by status"
            >
              <option value="">All statuses</option>
              {[...new Set(items.map((i) => i.status))].map((s) => (
                <option key={s} value={s}>
                  {s.replace(/_/g, " ")}
                </option>
              ))}
            </select>
            <select
              className={input}
              value={shelfFilter}
              onChange={(e) => setShelfFilter(e.target.value)}
              aria-label="Filter by shelf"
            >
              <option value="">All shelves</option>
              {shelves.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </div>
        }
      >
        {visible.length === 0 ? (
          <Empty>No items match this filter.</Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="text-xs uppercase tracking-wide text-gray-500">
                <tr>
                  <th className="pb-2 pr-4 font-medium">Item</th>
                  <th className="pb-2 pr-4 font-medium">Shelf</th>
                  <th className="pb-2 pr-4 font-medium">Sell-by</th>
                  <th className="pb-2 pr-4 font-medium">Status</th>
                  <th className="pb-2 font-medium"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {visible.map((item) => (
                  <tr key={item.id}>
                    <td className="py-2 pr-4">
                      <div className="font-medium">{item.name}</div>
                      <div className="text-xs text-gray-500">
                        {item.category || "Uncategorized"}
                        {item.sku ? ` · ${item.sku}` : ""}
                      </div>
                    </td>
                    <td className="py-2 pr-4 text-gray-600">{shelfName(item.shelf_id)}</td>
                    <td className="py-2 pr-4 text-gray-600">{formatDate(item.sell_by_date)}</td>
                    <td className="py-2 pr-4">
                      <StatusBadge status={item.status} />
                    </td>
                    <td className="py-2 text-right">
                      {["in_stock", "needs_review", "available"].includes(item.status) && (
                        <button
                          onClick={async () => {
                            onError("");
                            try {
                              await api.updateItemStatus(item.id, "discarded");
                              onChanged();
                            } catch (err) {
                              onError(err.message);
                            }
                          }}
                          className="text-xs font-medium text-gray-500 underline hover:text-gray-800"
                        >
                          Discard
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* FR-3.11: development-only, never in a deployed build. */}
      {import.meta.env.DEV && <OcrSimulator items={items} onError={onError} onChanged={onChanged} />}
    </div>
  );
}

function OcrSimulator({ items, onError, onChanged }) {
  const candidates = items.filter((i) => i.status === "in_stock");
  if (candidates.length === 0) return null;

  async function simulate(itemId, confidence, skuMatch) {
    onError("");
    try {
      await api.submitOcrResult(itemId, {
        ocr_raw_text: "SELL BY 09/14/2026",
        ocr_confidence: confidence,
        sku_match_confirmed: skuMatch,
      });
      onChanged();
    } catch (err) {
      onError(err.message);
    }
  }

  return (
    <Card title="Simulate an OCR read (dev only)">
      <p className="mb-3 text-xs text-gray-500">
        Stands in for the camera pipeline until it's wired up. This panel is
        stripped from any production build.
      </p>
      <ul className="divide-y divide-gray-100">
        {candidates.map((item) => (
          <li key={item.id} className="flex items-center justify-between gap-3 py-2 text-sm">
            <span>{item.name}</span>
            <span className="flex gap-2">
              <button
                onClick={() => simulate(item.id, 0.97, true)}
                className="rounded border border-gray-300 px-2 py-1 text-xs hover:bg-gray-50"
              >
                Confident read → publishes
              </button>
              <button
                onClick={() => simulate(item.id, 0.83, false)}
                className="rounded border border-gray-300 px-2 py-1 text-xs hover:bg-gray-50"
              >
                Uncertain read → review
              </button>
            </span>
          </li>
        ))}
      </ul>
    </Card>
  );
}

/* ---------------- Shelves (FR-3.7) ---------------- */

function Shelves({ shelves, onError, onChanged }) {
  const [form, setForm] = useState({ name: "", location: "", camera_id: "" });
  const [busy, setBusy] = useState(false);

  const input = "rounded-lg border border-gray-300 px-3 py-1.5 text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-100";

  async function handleCreate(e) {
    e.preventDefault();
    onError("");
    setBusy(true);
    try {
      await api.createShelf(form);
      setForm({ name: "", location: "", camera_id: "" });
      onChanged();
    } catch (err) {
      onError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <Card title="Register a shelf">
        <form onSubmit={handleCreate} className="flex flex-wrap gap-2">
          <input
            className={input}
            placeholder="Shelf name"
            required
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
          />
          <input
            className={input}
            placeholder="Location"
            value={form.location}
            onChange={(e) => setForm({ ...form, location: e.target.value })}
          />
          <input
            className={input}
            placeholder="Camera ID"
            value={form.camera_id}
            onChange={(e) => setForm({ ...form, camera_id: e.target.value })}
          />
          <button
            disabled={busy}
            className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-60"
          >
            Add shelf
          </button>
        </form>
      </Card>

      <Card title={`Shelves (${shelves.length})`}>
        {shelves.length === 0 ? (
          <Empty>No shelves registered yet.</Empty>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {shelves.map((shelf) => (
              <ShelfConditionCard key={shelf.id} shelf={shelf} />
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}

/* ---------------- date helpers ---------------- */

function formatDate(value) {
  const d = parseUtc(value);
  return d ? d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }) : "—";
}

function formatTime(value) {
  const d = parseUtc(value);
  return d ? d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" }) : "—";
}

function toDateInput(value) {
  const d = parseUtc(value);
  return d ? d.toISOString().slice(0, 10) : "";
}

function relativeTo(value) {
  const d = parseUtc(value);
  if (!d) return "—";
  const minutes = Math.round((d.getTime() - Date.now()) / 60000);
  const abs = Math.abs(minutes);
  const unit = abs < 60 ? `${abs}m` : abs < 1440 ? `${Math.round(abs / 60)}h` : `${Math.round(abs / 1440)}d`;
  return minutes < 0 ? `${unit} ago` : `in ${unit}`;
}
