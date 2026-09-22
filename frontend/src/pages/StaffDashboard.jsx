import { useCallback, useEffect, useMemo, useState } from "react";
import { api, formatCurrency, parseUtc } from "../api";
import BarcodeScanner from "../components/BarcodeScanner";
import Shell, { Card, Empty, ErrorBanner, StatusBadge } from "../components/Shell";
import Intake from "./Intake";

const TABS = [
  { key: "overview", label: "Overview" },
  { key: "intake", label: "Intake" },
  { key: "review", label: "Review queue" },
  { key: "inventory", label: "Inventory" },
  { key: "rules", label: "Expiration rules" },
  { key: "shelves", label: "Shelves" },
  { key: "donations", label: "Donation report" },
];

/** Reading older than this means the sensor or its network is down, not
 *  that conditions are fine (FR-4.7, NFR-4.4.4). */
const STALE_READING_MINUTES = 15;

export default function StaffDashboard() {
  const [items, setItems] = useState([]);
  const [shelves, setShelves] = useState([]);
  const [nearExpiry, setNearExpiry] = useState([]);
  const [reservations, setReservations] = useState([]);
  const [error, setError] = useState("");
  const [tab, setTab] = useState("overview");
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const [i, s, n, r] = await Promise.all([
        api.listItems(),
        api.listShelves(),
        api.listNearExpiry(48),
        api.allReservations(),
      ]);
      setItems(i);
      setShelves(s);
      setNearExpiry(n);
      setReservations(r);
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
              reservations={reservations}
              shelves={shelves}
              shelfName={shelfName}
              onError={setError}
              onChanged={refresh}
            />
          )}
          {tab === "intake" && <Intake shelves={shelves} onError={setError} onChanged={refresh} />}
          {tab === "review" && (
            <ReviewQueue queue={reviewQueue} shelfName={shelfName} onError={setError} onChanged={refresh} />
          )}
          {tab === "inventory" && (
            <Inventory items={items} shelves={shelves} shelfName={shelfName} onError={setError} onChanged={refresh} />
          )}
          {tab === "rules" && <ExpirationRules onError={setError} onChanged={refresh} />}
          {tab === "shelves" && <Shelves shelves={shelves} onError={setError} onChanged={refresh} />}
          {tab === "donations" && <DonationReport onError={setError} />}
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

function Overview({ counts, nearExpiry, reservations, shelves, shelfName, onError, onChanged }) {
  // Scan state lives here, not in either card, because both the Confirm
  // pickup button and the Pickup schedule button open the same camera.
  // Two mounted scanners would mean two getUserMedia calls, and on a
  // single-camera laptop the second one fails with NotReadableError.
  const [scanOpen, setScanOpen] = useState(false);
  const [outcome, setOutcome] = useState(null);

  const confirm = useCallback(
    async (code) => {
      onError("");
      setOutcome(null);
      try {
        const reservation = await api.confirmPickup(String(code).trim());
        setScanOpen(false);
        setOutcome({ ok: true, reservation });
        onChanged();
      } catch (err) {
        // Kept in the card rather than only in the page-level banner: a
        // staff member holding a phone at the shelf is looking at the
        // scanner, not above the fold.
        setScanOpen(false);
        setOutcome({ ok: false, message: err.message });
      }
    },
    [onError, onChanged],
  );

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

      <div className="grid gap-6 lg:grid-cols-3">
        <PickupScan
          outcome={outcome}
          scanOpen={scanOpen}
          onScan={() => {
            setOutcome(null);
            setScanOpen((open) => !open);
          }}
          onConfirmCode={confirm}
        />

        <div className="lg:col-span-2">
          <PickupSchedule
            reservations={reservations}
            scanOpen={scanOpen}
            onScan={() => {
              setOutcome(null);
              setScanOpen((open) => !open);
            }}
          />
        </div>
      </div>

      {/* One scanner for both buttons, full width because a 320px-tall
          video does not belong in a narrow grid column. Unmounted rather
          than hidden, so the camera light actually goes off. */}
      {scanOpen && (
        <BarcodeScanner
          mode="qr"
          onDetected={confirm}
          onClose={() => setScanOpen(false)}
        />
      )}

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

/* ---------------- Pickup (FR-3.10, FR-9.4, FR-9.10) ---------------- */

/**
 * Camera first, keyboard second.
 *
 * The code is already a QR on the organizer's phone, so typing out
 * twenty-two base64 characters at the shelf was work nobody needed to do.
 * Manual entry stays, folded away: a dead phone battery, a cracked lens or
 * a browser without camera permission must not be able to block a handoff
 * (FR-9.10).
 */
function PickupScan({ outcome, scanOpen, onScan, onConfirmCode }) {
  const [manualOpen, setManualOpen] = useState(false);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);

  async function handleSubmit(e) {
    e.preventDefault();
    setBusy(true);
    try {
      await onConfirmCode(code);
      setCode("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="Confirm pickup">
      <p className="mb-3 text-sm text-gray-500">
        Scan the QR code on the organizer's phone. Staff confirm the handoff —
        the pantry cannot confirm its own.
      </p>

      <button
        type="button"
        onClick={onScan}
        aria-expanded={scanOpen}
        className="w-full rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-700"
      >
        {scanOpen ? "Close camera" : "Scan QR code"}
      </button>

      {outcome?.ok && (
        <div className="mt-3 rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-900">
          Collected: <span className="font-medium">{outcome.reservation.item_name}</span>{" "}
          by {outcome.reservation.pantry_name} at{" "}
          {formatTime(outcome.reservation.picked_up_at)}.
        </div>
      )}
      {outcome && !outcome.ok && (
        <div className="mt-3 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-900">
          {outcome.message}
        </div>
      )}

      <button
        type="button"
        onClick={() => setManualOpen((open) => !open)}
        aria-expanded={manualOpen}
        className="mt-3 text-xs font-medium text-gray-500 underline hover:text-gray-800"
      >
        {manualOpen ? "Hide manual entry" : "Can't scan? Enter code"}
      </button>

      {manualOpen && (
        <form onSubmit={handleSubmit} className="mt-2 flex gap-2">
          <input
            value={code}
            onChange={(e) => setCode(e.target.value)}
            required
            placeholder="Pickup code"
            aria-label="Pickup code"
            className="min-w-0 flex-1 rounded-lg border border-gray-300 px-3 py-2 font-mono text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-100"
          />
          <button
            disabled={busy}
            className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-60"
          >
            {busy ? "…" : "Confirm"}
          </button>
        </form>
      )}
    </Card>
  );
}

/**
 * What the store should expect at the shelf, and when (FR-8.10).
 *
 * Shows pending pickups plus anything resolved recently, so a manager who
 * looks up a minute after a handoff still sees what happened rather than a
 * row that silently vanished.
 */
const RESOLVED_VISIBLE_HOURS = 4;
const SCHEDULE_ROW_LIMIT = 12;

function PickupSchedule({ reservations, scanOpen, onScan }) {
  const rows = useMemo(() => {
    const cutoff = Date.now() - RESOLVED_VISIBLE_HOURS * 3600_000;
    return reservations
      .filter((r) => {
        if (r.status === "pending") return true;
        const resolved = parseUtc(r.picked_up_at || r.hold_expires_at);
        return resolved ? resolved.getTime() >= cutoff : false;
      })
      .sort(
        (a, b) =>
          // Nulls last: a reservation predating scheduling has no slot to
          // sort by, and guessing one would put it somewhere misleading.
          (parseUtc(a.scheduled_pickup_at)?.getTime() ?? Infinity) -
          (parseUtc(b.scheduled_pickup_at)?.getTime() ?? Infinity),
      );
  }, [reservations]);

  const shown = rows.slice(0, SCHEDULE_ROW_LIMIT);

  return (
    <Card
      title="Pickup schedule"
      action={
        <button
          type="button"
          onClick={onScan}
          aria-expanded={scanOpen}
          className="rounded-lg bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-emerald-700"
        >
          {scanOpen ? "Close camera" : "Scan QR code"}
        </button>
      }
    >
      {shown.length === 0 ? (
        <Empty>No pickups scheduled.</Empty>
      ) : (
        <>
          <ul className="divide-y divide-gray-100">
            {shown.map((r) => (
              <ScheduleRow key={r.id} reservation={r} />
            ))}
          </ul>
          {rows.length > shown.length && (
            <p className="mt-2 text-xs text-gray-500">
              +{rows.length - shown.length} more scheduled.
            </p>
          )}
        </>
      )}
    </Card>
  );
}

function ScheduleRow({ reservation }) {
  const slot = parseUtc(reservation.scheduled_pickup_at);
  // The point of the card for a manager: who was due and hasn't turned up.
  const overdue = reservation.status === "pending" && slot && slot.getTime() < Date.now();

  return (
    <li
      className={`flex flex-wrap items-center justify-between gap-3 py-2 text-sm ${
        overdue ? "bg-amber-50" : ""
      }`}
    >
      <div className="min-w-0">
        <div className={`font-medium ${overdue ? "text-amber-900" : ""}`}>
          {slot ? formatDateTime(reservation.scheduled_pickup_at) : "Not scheduled"}
          {overdue && <span className="ml-2 text-xs font-normal">· overdue</span>}
        </div>
        <div className="mt-0.5 truncate text-xs text-gray-500">
          {reservation.item_name} · {reservation.pantry_name}
        </div>
      </div>
      <StatusBadge status={reservation.status} />
    </li>
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
  const [form, setForm] = useState({ name: "", sku: "", category: "", shelf_id: "", sell_by_date: "", unit_value: "" });
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
        // Left blank, this inherits the matched product's catalog value
        // instead (crud.create_item) — only set here to override it.
        unit_value: form.unit_value === "" ? null : Number(form.unit_value),
      });
      setForm({ name: "", sku: "", category: "", shelf_id: "", sell_by_date: "", unit_value: "" });
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
          <div>
            <label className="block text-xs text-gray-500">Unit value ($)</label>
            <input
              type="number"
              min="0"
              step="0.01"
              placeholder="from catalog"
              className={`${input} w-28`}
              value={form.unit_value}
              onChange={(e) => setForm({ ...form, unit_value: e.target.value })}
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
                  <th className="pb-2 pr-4 font-medium">Date</th>
                  {/* What the scheduler will do next, and when. An automatic
                      status change that nobody could see coming is the thing
                      that makes staff stop trusting the automation. */}
                  <th className="pb-2 pr-4 font-medium">Next automatic move</th>
                  <th className="pb-2 pr-4 font-medium">Unit value</th>
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
                        {item.upc ? ` · UPC ${item.upc.replace(/^0/, "")}` : ""}
                      </div>
                    </td>
                    <td className="py-2 pr-4 text-gray-600">{shelfName(item.shelf_id)}</td>
                    <td className="py-2 pr-4 text-gray-600">
                      {formatDate(item.use_by_date || item.sell_by_date)}
                      <div className="text-xs text-gray-400">
                        {item.use_by_date ? "use by" : item.sell_by_date ? "sell by" : "no date"}
                        {item.date_source === "shelf_life" && " · estimated"}
                      </div>
                    </td>
                    <td className="py-2 pr-4">
                      <NextMove item={item} />
                    </td>
                    <td className="py-2 pr-4 tabular-nums text-gray-600">{formatCurrency(item.unit_value)}</td>
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

      <ProductCatalog onError={onError} />

      {/* FR-3.11: development-only, never in a deployed build. */}
      {import.meta.env.DEV && <OcrSimulator items={items} onError={onError} onChanged={onChanged} />}
    </div>
  );
}

/**
 * The catalog's per-unit value (FR-11.1) — set here once per product, then
 * inherited by every item that scans in against it, instead of retyped at
 * every intake.
 */
function ProductCatalog({ onError }) {
  const [products, setProducts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [form, setForm] = useState({ upc: "", name: "", unit_value: "" });
  const [busy, setBusy] = useState(false);

  const load = useCallback(
    (q) => api.listProducts(q || undefined).then(setProducts).catch((err) => onError(err.message)),
    [onError],
  );

  useEffect(() => {
    load().finally(() => setLoading(false));
  }, [load]);

  async function handleCreate(e) {
    e.preventDefault();
    onError("");
    setBusy(true);
    try {
      await api.createProduct({
        upc: form.upc,
        name: form.name,
        unit_value: form.unit_value === "" ? null : Number(form.unit_value),
      });
      setForm({ upc: "", name: "", unit_value: "" });
      await load(search);
    } catch (err) {
      onError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function saveValue(product, value) {
    onError("");
    try {
      await api.updateProduct(product.id, { unit_value: value === "" ? null : Number(value) });
      await load(search);
    } catch (err) {
      onError(err.message);
    }
  }

  const input = "rounded-lg border border-gray-300 px-3 py-1.5 text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-100";

  return (
    <Card
      title="Product catalog values"
      action={
        <input
          className={input}
          placeholder="Search catalog"
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            load(e.target.value);
          }}
          aria-label="Search catalog"
        />
      }
    >
      <p className="mb-3 text-sm text-gray-500">
        Set a unit value on a product here and every item scanned or entered
        against it inherits that value automatically — that's what a
        donation gets valued at on the tax report.
      </p>

      <form onSubmit={handleCreate} className="mb-4 flex flex-wrap items-end gap-2 border-b border-gray-100 pb-4">
        <input className={input} placeholder="UPC" required value={form.upc} onChange={(e) => setForm({ ...form, upc: e.target.value })} />
        <input className={input} placeholder="Product name" required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
        <input
          type="number"
          min="0"
          step="0.01"
          className={`${input} w-28`}
          placeholder="Unit value ($)"
          value={form.unit_value}
          onChange={(e) => setForm({ ...form, unit_value: e.target.value })}
        />
        <button disabled={busy} className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-60">
          Add product
        </button>
      </form>

      {loading ? (
        <p className="text-sm text-gray-500">Loading…</p>
      ) : products.length === 0 ? (
        <Empty>No products in the catalog yet.</Empty>
      ) : (
        <ul className="divide-y divide-gray-100">
          {products.map((product) => (
            <ProductRow key={product.id} product={product} onSave={(value) => saveValue(product, value)} />
          ))}
        </ul>
      )}
    </Card>
  );
}

function ProductRow({ product, onSave }) {
  const [value, setValue] = useState(product.unit_value ?? "");
  const [busy, setBusy] = useState(false);
  const dirty = String(product.unit_value ?? "") !== String(value);

  async function save() {
    setBusy(true);
    try {
      await onSave(value);
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="flex flex-wrap items-center justify-between gap-3 py-2 text-sm">
      <div>
        <div className="font-medium">{product.name}</div>
        <div className="text-xs text-gray-500">{product.upc}{product.category ? ` · ${product.category}` : ""}</div>
      </div>
      <div className="flex items-center gap-2">
        <span className="text-gray-400">$</span>
        <input
          type="number"
          min="0"
          step="0.01"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          className="w-24 rounded-lg border border-gray-300 px-2 py-1 text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-100"
        />
        <button
          onClick={save}
          disabled={busy || !dirty}
          className="rounded-lg border border-gray-300 px-3 py-1 text-xs font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-60"
        >
          Save
        </button>
      </div>
    </li>
  );
}

/**
 * The next thing the expiration sweep will do to this item.
 *
 * Terminal items have no next move. An item with no deadlines has none
 * either, and that is worth saying out loud rather than leaving blank: a
 * blank cell reads as "nothing due yet," when it actually means the sweep
 * cannot see this item at all and it will sit there indefinitely.
 */
function NextMove({ item }) {
  if (["picked_up", "discarded", "expired_hold"].includes(item.status)) {
    return <span className="text-xs text-gray-400">—</span>;
  }

  const donate = parseUtc(item.donate_after);
  const discard = parseUtc(item.discard_after);

  if (!donate && !discard) {
    return (
      <span className="text-xs font-medium text-amber-700">
        No date — won't move on its own
      </span>
    );
  }

  const publishable = ["in_stock", "near_expiry"].includes(item.status);
  if (publishable && donate && donate.getTime() > Date.now()) {
    return (
      <span className="text-xs text-gray-600">
        Offer to pantries {relativeTo(item.donate_after)}
        <span className="block text-gray-400">{formatDate(item.donate_after)}</span>
      </span>
    );
  }

  if (discard) {
    return (
      <span className="text-xs text-gray-600">
        Off the shelf {relativeTo(item.discard_after)}
        <span className="block text-gray-400">{formatDate(item.discard_after)}</span>
      </span>
    );
  }

  // donate_after has passed but the status hasn't changed: either the
  // category needs a person to publish it, or the sweep hasn't ticked yet.
  return <span className="text-xs text-gray-500">Waiting on staff to publish</span>;
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

/* ---------------- Expiration rules ---------------- */

/**
 * The policy behind every status change the system makes on its own.
 *
 * Read-only for staff, editable by a manager. It is here rather than
 * buried in an admin screen for a specific reason: staff who cannot see
 * why an item moved stop trusting that it moved for a reason, and start
 * keeping their own list on paper.
 */
function ExpirationRules({ onError, onChanged }) {
  const [rules, setRules] = useState([]);
  const [loading, setLoading] = useState(true);
  const [sweeping, setSweeping] = useState(false);
  const [lastSweep, setLastSweep] = useState(null);

  useEffect(() => {
    api
      .listExpirationRules()
      .then(setRules)
      .catch((err) => onError(err.message))
      .finally(() => setLoading(false));
  }, [onError]);

  async function sweepNow() {
    onError("");
    setSweeping(true);
    try {
      setLastSweep(await api.runExpirationSweep());
      onChanged();
    } catch (err) {
      onError(err.message);
    } finally {
      setSweeping(false);
    }
  }

  if (loading) return <p className="text-sm text-gray-500">Loading…</p>;

  return (
    <div className="space-y-6">
      <Card
        title="Automatic expiration rules"
        action={
          <button onClick={sweepNow} disabled={sweeping} className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-60">
            {sweeping ? "Running…" : "Run the sweep now"}
          </button>
        }
      >
        <p className="mb-4 text-sm text-gray-600">
          These numbers decide when an item is flagged, offered to the donation
          network, and taken off the shelf. The server applies them every minute —
          the button above just runs it early. Managers can edit them.
        </p>

        {lastSweep && (
          <div className="mb-4 rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-900">
            Sweep finished: {lastSweep.near_expiry} flagged near expiry,{" "}
            {lastSweep.published} published, {lastSweep.discarded} discarded,{" "}
            {lastSweep.released_from_reserved} released from a lapsed hold,{" "}
            {lastSweep.reservations_expired} reservation(s) expired.
          </div>
        )}

        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="text-xs uppercase tracking-wide text-gray-500">
              <tr>
                <th className="pb-2 pr-4 font-medium">Category</th>
                <th className="pb-2 pr-4 font-medium">Flag near expiry</th>
                <th className="pb-2 pr-4 font-medium">Offer to pantries</th>
                <th className="pb-2 pr-4 font-medium">Take off the shelf</th>
                <th className="pb-2 font-medium">Publishes on its own</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {rules.map((rule) => (
                <tr key={rule.id}>
                  <td className="py-2 pr-4">
                    <div className="font-medium">
                      {rule.category === "*" ? "Everything else" : rule.category}
                    </div>
                    {rule.notes && <div className="text-xs text-gray-500">{rule.notes}</div>}
                  </td>
                  <td className="py-2 pr-4 text-gray-600">{offsetLabel(-rule.near_expiry_hours)}</td>
                  <td className="py-2 pr-4 text-gray-600">{offsetLabel(rule.publish_offset_hours)}</td>
                  <td className="py-2 pr-4 text-gray-600">
                    {offsetLabel(rule.discard_after_hours)}
                    <div className="text-xs text-gray-400">or the use-by date, whichever is first</div>
                  </td>
                  <td className="py-2">
                    {rule.auto_publish ? (
                      "Yes"
                    ) : (
                      <span className="font-medium text-amber-700">No — staff approve each one</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <p className="mt-4 text-xs text-gray-500">
          All times are relative to the item's sell-by date. A use-by date always
          wins: nothing is offered to a pantry past one, whatever the category says.
        </p>
      </Card>
    </div>
  );
}

/** "12h before" / "2d after" / "on the date" — hours are not a unit staff think in. */
function offsetLabel(hours) {
  if (hours === 0) return "on the date";
  const abs = Math.abs(hours);
  const amount = abs % 24 === 0 ? `${abs / 24}d` : `${abs}h`;
  return hours < 0 ? `${amount} before` : `${amount} after`;
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

/* ---------------- Donation report (FR-11.1 / FR-11.3) ---------------- */

/**
 * What the business pulls at tax time: every completed donation, valued at
 * the item's unit value when it was handed off, filterable by date range
 * and pantry, exportable as a CSV for an accountant.
 */
function DonationReport({ onError }) {
  const [pantries, setPantries] = useState([]);
  const [records, setRecords] = useState([]);
  const [loading, setLoading] = useState(true);
  const [exporting, setExporting] = useState(false);
  const [filters, setFilters] = useState({ from: "", to: "", pantryId: "" });

  const load = useCallback(
    (f) =>
      api
        .listDonations({
          from: f.from ? `${f.from}T00:00:00Z` : undefined,
          to: f.to ? `${f.to}T23:59:59Z` : undefined,
          pantryId: f.pantryId || undefined,
        })
        .then(setRecords)
        .catch((err) => onError(err.message)),
    [onError],
  );

  useEffect(() => {
    Promise.all([api.listPantries().then(setPantries).catch((err) => onError(err.message)), load(filters)]).finally(
      () => setLoading(false),
    );
    // Only on mount — filter changes are applied by the Apply button below,
    // not on every keystroke.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function applyFilters(e) {
    e.preventDefault();
    onError("");
    setLoading(true);
    load(filters).finally(() => setLoading(false));
  }

  async function exportCsv() {
    onError("");
    setExporting(true);
    try {
      await api.downloadDonationsCsv({
        from: filters.from ? `${filters.from}T00:00:00Z` : undefined,
        to: filters.to ? `${filters.to}T23:59:59Z` : undefined,
        pantryId: filters.pantryId || undefined,
      });
    } catch (err) {
      onError(err.message);
    } finally {
      setExporting(false);
    }
  }

  const totalValue = records.reduce((sum, r) => sum + (r.unit_value_at_handoff ? Number(r.unit_value_at_handoff) : 0), 0);
  const input = "rounded-lg border border-gray-300 px-3 py-1.5 text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-100";

  return (
    <div className="space-y-6">
      <Card
        title="Donation report"
        action={
          <button
            onClick={exportCsv}
            disabled={exporting || records.length === 0}
            className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-60"
          >
            {exporting ? "Exporting…" : "Export CSV"}
          </button>
        }
      >
        <p className="mb-4 text-sm text-gray-600">
          Every confirmed donation, valued at the catalog price it carried at
          handoff (FR-11.1) — the record to hand an accountant for a tax
          deduction.
        </p>

        <form onSubmit={applyFilters} className="mb-4 flex flex-wrap items-end gap-2">
          <div>
            <label className="block text-xs text-gray-500">From</label>
            <input type="date" className={input} value={filters.from} onChange={(e) => setFilters({ ...filters, from: e.target.value })} />
          </div>
          <div>
            <label className="block text-xs text-gray-500">To</label>
            <input type="date" className={input} value={filters.to} onChange={(e) => setFilters({ ...filters, to: e.target.value })} />
          </div>
          <div>
            <label className="block text-xs text-gray-500">Pantry</label>
            <select
              className={input}
              value={filters.pantryId}
              onChange={(e) => setFilters({ ...filters, pantryId: e.target.value })}
            >
              <option value="">All pantries</option>
              {pantries.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.org_name}
                </option>
              ))}
            </select>
          </div>
          <button className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-700">
            Apply
          </button>
        </form>

        {loading ? (
          <p className="text-sm text-gray-500">Loading…</p>
        ) : records.length === 0 ? (
          <Empty>No donations recorded for this filter.</Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="text-xs uppercase tracking-wide text-gray-500">
                <tr>
                  <th className="pb-2 pr-4 font-medium">Confirmed</th>
                  <th className="pb-2 pr-4 font-medium">Item</th>
                  <th className="pb-2 pr-4 font-medium">Pantry</th>
                  <th className="pb-2 font-medium">Value</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {records.map((r) => (
                  <tr key={r.id}>
                    <td className="py-2 pr-4 text-gray-600">{formatDateTime(r.confirmed_at)}</td>
                    <td className="py-2 pr-4">
                      <div className="font-medium">{r.item_name}</div>
                      <div className="text-xs text-gray-500">
                        {r.item_category || "Uncategorized"}
                        {r.item_sku ? ` · ${r.item_sku}` : ""}
                      </div>
                    </td>
                    <td className="py-2 pr-4 text-gray-600">{r.pantry_name_at_handoff}</td>
                    <td className="py-2 tabular-nums">{formatCurrency(r.unit_value_at_handoff)}</td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr className="border-t border-gray-200 font-medium">
                  <td className="pt-2 pr-4" colSpan={3}>
                    {records.length} donation{records.length === 1 ? "" : "s"}
                  </td>
                  <td className="pt-2 tabular-nums">{formatCurrency(totalValue)}</td>
                </tr>
              </tfoot>
            </table>
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

/** Day and time together, for the pickup schedule — a bare time is
 *  ambiguous once bookings can be up to 24 hours out. */
function formatDateTime(value) {
  const d = parseUtc(value);
  if (!d) return "—";
  const today = new Date().toDateString() === d.toDateString();
  const time = d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  return today
    ? `Today ${time}`
    : `${d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" })} ${time}`;
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
