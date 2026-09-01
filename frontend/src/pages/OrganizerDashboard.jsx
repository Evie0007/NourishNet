import { useCallback, useEffect, useMemo, useState } from "react";
import { QRCodeSVG } from "qrcode.react";
import { api, parseUtc } from "../api";
import { useAuth } from "../auth";
import Shell, { Card, Empty, ErrorBanner, StatusBadge } from "../components/Shell";

export default function OrganizerDashboard() {
  const { user } = useAuth();
  const [available, setAvailable] = useState([]);
  const [reservations, setReservations] = useState([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [sortBy, setSortBy] = useState("sell_by");
  const [category, setCategory] = useState("");

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

  const active = reservations.filter((r) => r.status === "pending");
  const past = reservations.filter((r) => r.status !== "pending");

  async function reserve(itemId) {
    setError("");
    try {
      await api.createReservation(itemId);
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
                    <li
                      key={item.id}
                      className="flex flex-wrap items-center justify-between gap-3 py-3"
                    >
                      {/* FR-8.2 */}
                      <div className="min-w-0">
                        <div className="font-medium">{item.name}</div>
                        <div className="mt-0.5 text-xs text-gray-500">
                          {item.category || "Uncategorized"} · sell-by{" "}
                          {formatDate(item.sell_by_date)}
                        </div>
                      </div>
                      <button
                        onClick={() => reserve(item.id)}
                        disabled={!verified}
                        title={verified ? undefined : "Available once your organization is verified"}
                        className="rounded-lg bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-emerald-700 disabled:cursor-not-allowed disabled:bg-gray-300"
                      >
                        Reserve
                      </button>
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

function ReservationCard({ reservation, onCancel }) {
  const [remaining, setRemaining] = useState(() => timeLeft(reservation.hold_expires_at));

  useEffect(() => {
    const id = setInterval(() => setRemaining(timeLeft(reservation.hold_expires_at)), 30_000);
    return () => clearInterval(id);
  }, [reservation.hold_expires_at]);

  const urgent = remaining.totalMinutes <= 30;

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
          : `Collect within ${remaining.label} (by ${formatTime(reservation.hold_expires_at)})`}
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
