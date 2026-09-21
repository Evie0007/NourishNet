import { useCallback, useEffect, useMemo, useState } from "react";
import { QRCodeSVG } from "qrcode.react";
import { api, parseUtc, toLocalInputValue } from "../api";
import { useAuth } from "../auth";
import Shell, { Card, Empty, ErrorBanner, StatusBadge } from "../components/Shell";

// Mirrors schemas.SCHEDULE_HORIZON and PICKUP_GRACE on the backend. The
// server is authoritative — these only shape the control so it cannot
// offer a value that would come back 422.
const SCHEDULE_HORIZON_HOURS = 24;
const GRACE_MINUTES = 30;

// Shaved off the far edge so a picker left open for a few minutes does not
// produce a time the server has since ruled out.
const HORIZON_BUFFER_MINUTES = 5;

export default function OrganizerDashboard() {
  const { user } = useAuth();
  const [available, setAvailable] = useState([]);
  const [reservations, setReservations] = useState([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [sortBy, setSortBy] = useState("sell_by");
  const [category, setCategory] = useState("");
  // Which row has its pickup-time picker open. One at a time — a list of
  // half-filled forms is worse than a single focused one.
  const [schedulingId, setSchedulingId] = useState(null);

  const verified = user?.pantry_verified === true;

  const refresh = useCallback(async () => {
    try {
      const [items, mine] = await Promise.all([api.listItems(), api.myReservations()]);
      setAvailable(items);
      setReservations(mine);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const categories = useMemo(
    () => [...new Set(available.map((i) => i.category).filter(Boolean))].sort(),
    [available],
  );

  // FR-8.3
  const visible = useMemo(() => {
    const rows = category ? available.filter((i) => i.category === category) : available;
    return [...rows].sort((a, b) =>
      sortBy === "name"
        ? a.name.localeCompare(b.name)
        : (parseUtc(a.sell_by_date)?.getTime() ?? Infinity) -
          (parseUtc(b.sell_by_date)?.getTime() ?? Infinity),
    );
  }, [available, category, sortBy]);

  // Soonest pickup first — the API orders by when the reservation was
  // made, which is not the order anyone collects in.
  const active = reservations
    .filter((r) => r.status === "pending")
    .sort(
      (a, b) =>
        (parseUtc(a.scheduled_pickup_at)?.getTime() ?? Infinity) -
        (parseUtc(b.scheduled_pickup_at)?.getTime() ?? Infinity),
    );
  const past = reservations.filter((r) => r.status !== "pending");

  async function reserve(itemId, scheduledLocal) {
    setError("");
    try {
      await api.createReservation(itemId, scheduledLocal);
      setSchedulingId(null);
      await refresh();
    } catch (err) {
      setError(err.message);
    }
  }

  async function cancel(reservationId) {
    setError("");
    try {
      await api.cancelReservation(reservationId);
      await refresh();
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <Shell
      title={user?.pantry_name || "Donation portal"}
      subtitle="Browse what's available, reserve what you can collect, and show the code at the shelf."
    >
      <ErrorBanner message={error} onDismiss={() => setError("")} />

      {/* UC-02 flow 3a: browsing is allowed, reserving is not. */}
      {!verified && (
        <div className="mb-6 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          <p className="font-medium">Your organization is pending verification.</p>
          <p className="mt-1">
            You can browse everything below, but reserving stays disabled until a
            NourishNet admin confirms your EIN. We'll email you when that's done.
          </p>
        </div>
      )}

      {loading ? (
        <p className="text-sm text-gray-500">Loading…</p>
      ) : (
        <div className="grid gap-6 lg:grid-cols-5">
          <div className="lg:col-span-3">
            <Card
              title={`Available donations (${visible.length})`}
              action={
                <div className="flex gap-2">
                  <select
                    value={category}
                    onChange={(e) => setCategory(e.target.value)}
                    aria-label="Filter by category"
                    className="rounded-lg border border-gray-300 px-2 py-1 text-sm"
                  >
                    <option value="">All categories</option>
                    {categories.map((c) => (
                      <option key={c} value={c}>
                        {c}
                      </option>
                    ))}
                  </select>
                  <select
                    value={sortBy}
                    onChange={(e) => setSortBy(e.target.value)}
                    aria-label="Sort by"
                    className="rounded-lg border border-gray-300 px-2 py-1 text-sm"
                  >
                    <option value="sell_by">Soonest sell-by</option>
                    <option value="name">Name</option>
                  </select>
                </div>
              }
            >
              {visible.length === 0 ? (
                <Empty>Nothing available right now. Check back later today.</Empty>
              ) : (
                <ul className="divide-y divide-gray-100">
                  {visible.map((item) => (
                    <li key={item.id} className="py-3">
                      <div className="flex flex-wrap items-center justify-between gap-3">
                        {/* FR-8.2 */}
                        <div className="min-w-0">
                          <div className="font-medium">{item.name}</div>
                          <div className="mt-0.5 text-xs text-gray-500">
                            {item.category || "Uncategorized"} · sell-by{" "}
                            {formatDate(item.sell_by_date)}
                          </div>
                        </div>
                        <button
                          onClick={() =>
                            setSchedulingId(schedulingId === item.id ? null : item.id)
                          }
                          disabled={!verified}
                          aria-expanded={schedulingId === item.id}
                          title={
                            verified ? undefined : "Available once your organization is verified"
                          }
                          className="rounded-lg bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-emerald-700 disabled:cursor-not-allowed disabled:bg-gray-300"
                        >
                          {schedulingId === item.id ? "Close" : "Reserve"}
                        </button>
                      </div>

                      {schedulingId === item.id && (
                        <SchedulePicker
                          item={item}
                          onCancel={() => setSchedulingId(null)}
                          onConfirm={(scheduledLocal) => reserve(item.id, scheduledLocal)}
                        />
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          </div>

          <div className="space-y-6 lg:col-span-2">
            <Card title={`Your pickups (${active.length})`}>
              {active.length === 0 ? (
                <Empty>No active reservations.</Empty>
              ) : (
                <div className="space-y-4">
                  {active.map((r) => (
                    <ReservationCard key={r.id} reservation={r} onCancel={() => cancel(r.id)} />
                  ))}
                </div>
              )}
            </Card>

            {past.length > 0 && (
              <Card title="History">
                <ul className="divide-y divide-gray-100">
                  {past.map((r) => (
                    <li key={r.id} className="flex items-center justify-between gap-3 py-2 text-sm">
                      <div className="min-w-0">
                        <div className="truncate font-medium">{r.item_name}</div>
                        <div className="text-xs text-gray-500">
                          {formatDateTime(r.picked_up_at || r.hold_expires_at)}
                        </div>
                      </div>
                      <StatusBadge status={r.status} />
                    </li>
                  ))}
                </ul>
              </Card>
            )}
          </div>
        </div>
      )}
    </Shell>
  );
}

/**
 * FR-8.6 — the organization commits to a time, and the hold follows from
 * it rather than from the moment they clicked.
 *
 * The bounds are computed once on mount rather than on a ticking clock:
 * re-deriving them every second would move the control's floor out from
 * under a value the person had already chosen.
 */
function SchedulePicker({ item, onConfirm, onCancel }) {
  const [value, setValue] = useState(() => toLocalInputValue(defaultSlot()));
  const [problem, setProblem] = useState("");

  const min = useMemo(() => toLocalInputValue(new Date()), []);

  // The far edge is 24 hours out, or the moment the food has to leave the
  // shelf, whichever comes first. Without the second bound an overnight
  // booking on short-dated stock would be accepted here and then killed by
  // the discard sweep before anyone arrived.
  const discardAt = parseUtc(item.discard_after);
  const max = useMemo(() => {
    const horizon =
      Date.now() + (SCHEDULE_HORIZON_HOURS * 60 - HORIZON_BUFFER_MINUTES) * 60_000;
    const ceiling = discardAt ? Math.min(horizon, discardAt.getTime()) : horizon;
    return toLocalInputValue(new Date(ceiling));
  }, [discardAt]);

  const cappedByDiscard = discardAt && discardAt.getTime() < Date.now() + SCHEDULE_HORIZON_HOURS * 3600_000;

  function submit(e) {
    e.preventDefault();
    // min/max are advisory for typed input in some browsers, so check
    // before spending a request. The server stays authoritative.
    if (!value) return setProblem("Choose a pickup time.");
    if (value < min) return setProblem("That time has already passed.");
    if (value > max) {
      return setProblem(
        cappedByDiscard
          ? "This item has to be off the shelf before then. Pick an earlier time."
          : "Pickups can be booked up to 24 hours ahead.",
      );
    }
    setProblem("");
    onConfirm(value);
  }

  return (
    <form onSubmit={submit} className="mt-3 rounded-lg bg-gray-50 p-3">
      <label
        htmlFor={`pickup-${item.id}`}
        className="block text-xs font-medium text-gray-700"
      >
        When will you collect this?
      </label>
      <div className="mt-1.5 flex flex-wrap items-center gap-2">
        <input
          id={`pickup-${item.id}`}
          type="datetime-local"
          value={value}
          min={min}
          max={max}
          step="300"
          required
          onChange={(e) => setValue(e.target.value)}
          className="rounded-lg border border-gray-300 px-2 py-1.5 text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-100"
        />
        <button className="rounded-lg bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-emerald-700">
          Confirm reservation
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="text-xs font-medium text-gray-500 underline hover:text-gray-800"
        >
          Cancel
        </button>
      </div>
      <p className="mt-2 text-xs text-gray-500">
        Book up to 24 hours ahead. The hold is released {GRACE_MINUTES} minutes
        after your slot, and the item goes back into the pool.
        {cappedByDiscard && (
          <>
            {" "}
            This one has to be off the shelf by {formatDateTime(item.discard_after)}.
          </>
        )}
      </p>
      {problem && <p className="mt-1.5 text-xs font-medium text-amber-700">{problem}</p>}
    </form>
  );
}

/** The next half-hour boundary at least an hour out — the common case is
 *  "later today", and a sensible default makes that one tap. */
function defaultSlot() {
  const d = new Date(Date.now() + 60 * 60_000);
  d.setSeconds(0, 0);
  d.setMinutes(d.getMinutes() > 30 ? 60 : 30);
  return d;
}

function ReservationCard({ reservation, onCancel }) {
  const [remaining, setRemaining] = useState(() => timeLeft(reservation.hold_expires_at));

  useEffect(() => {
    const id = setInterval(() => setRemaining(timeLeft(reservation.hold_expires_at)), 30_000);
    return () => clearInterval(id);
  }, [reservation.hold_expires_at]);

  // The hold is the slot plus a 30-minute grace, so "under 30 minutes
  // left" now means precisely "the scheduled time has arrived or passed" —
  // which is exactly when this card should start looking urgent.
  const urgent = remaining.totalMinutes <= GRACE_MINUTES;

  return (
    <div className="rounded-lg border border-gray-200 p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="font-medium">{reservation.item_name}</div>
          <div className="mt-0.5 text-xs text-gray-500">
            {reservation.shelf_name || "Shelf not set"}
            {reservation.item_category ? ` · ${reservation.item_category}` : ""}
          </div>
        </div>
        <StatusBadge status={reservation.status} />
      </div>

      {reservation.scheduled_pickup_at && (
        <div className="mt-3 rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-900">
          Scheduled for{" "}
          <span className="font-medium">
            {formatDateTime(reservation.scheduled_pickup_at)}
          </span>
        </div>
      )}

      {/* NFR-4.5.4: large and high-contrast — this gets read off a phone in
          store lighting. */}
      {reservation.qr_code && (
        <div className="mt-4 flex flex-col items-center gap-2 rounded-lg bg-white p-4">
          <QRCodeSVG value={reservation.qr_code} size={200} level="M" marginSize={2} />
          <code className="select-all text-center text-xs tracking-wide text-gray-500">
            {reservation.qr_code}
          </code>
          <p className="text-center text-xs text-gray-500">
            Show this to store staff at the shelf.
          </p>
        </div>
      )}

      <div
        className={`mt-3 text-sm ${urgent ? "font-medium text-amber-700" : "text-gray-600"}`}
      >
        {remaining.expired
          ? "Hold window has lapsed — this item may have returned to the pool."
          : `${remaining.label} left to collect — the hold is released at ${formatTime(
              reservation.hold_expires_at,
            )}, ${GRACE_MINUTES} minutes after your slot.`}
      </div>

      <button
        onClick={onCancel}
        className="mt-3 text-xs font-medium text-gray-500 underline hover:text-gray-800"
      >
        Cancel reservation
      </button>
    </div>
  );
}

/* ---------------- date helpers ---------------- */

function timeLeft(value) {
  const d = parseUtc(value);
  if (!d) return { expired: true, label: "—", totalMinutes: 0 };
  const totalMinutes = Math.round((d.getTime() - Date.now()) / 60000);
  if (totalMinutes <= 0) return { expired: true, label: "0m", totalMinutes: 0 };
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  return {
    expired: false,
    totalMinutes,
    label: hours > 0 ? `${hours}h ${minutes}m` : `${minutes}m`,
  };
}

function formatDate(value) {
  const d = parseUtc(value);
  return d ? d.toLocaleDateString(undefined, { month: "short", day: "numeric" }) : "no date";
}

function formatTime(value) {
  const d = parseUtc(value);
  return d ? d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" }) : "—";
}

function formatDateTime(value) {
  const d = parseUtc(value);
  return d
    ? d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })
    : "—";
}
