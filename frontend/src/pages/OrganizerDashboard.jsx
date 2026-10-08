import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { QRCodeSVG } from "qrcode.react";
import { api, parseUtc, toLocalInputValue } from "../api";
import { useAuth } from "../auth";
import Shell, { Card, Empty, ErrorBanner, StatusBadge } from "../components/Shell";
import ShelfFinder from "../components/ShelfFinder";

// Mirrors schemas.SCHEDULE_HORIZON and PICKUP_GRACE on the backend. The
// server is authoritative — these only shape the control so it cannot
// offer a value that would come back 422.
const SCHEDULE_HORIZON_HOURS = 24;
const GRACE_MINUTES = 30;

// Shaved off the far edge so a picker left open for a few minutes does not
// produce a time the server has since ruled out.
const HORIZON_BUFFER_MINUTES = 5;

// The map and the list reflect the stores as they are now. A minute is about
// how long a pantry takes to walk from one shelf to the next, so polling at
// this rate is as live as the screen needs to be without a socket.
const MAP_REFRESH_MS = 45_000;

export default function OrganizerDashboard() {
  const { user } = useAuth();
  const [available, setAvailable] = useState([]);
  const [reservations, setReservations] = useState([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [sortBy, setSortBy] = useState("sell_by");
  const [category, setCategory] = useState("");
  // Shelf locations for the map and its side panel, each with its own
  // availability. The ones not on the map (no coordinates) can't be picked
  // there; the list below still shows their food.
  const [locations, setLocations] = useState([]);
  const [selectedStoreId, setSelectedStoreId] = useState(null);
  // The zip being searched, and where it is. A ref as well as state, so the
  // timed refresh keeps using the search without being rebuilt every time.
  const [center, setCenter] = useState(null);
  const zipRef = useRef("");
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState("");
  // Which row has its pickup-time picker open. One at a time — a list of
  // half-filled forms is worse than a single focused one.
  const [schedulingId, setSchedulingId] = useState(null);
  // Items added to the pickup in progress. Held here rather than on the
  // server: nothing is claimed until the organization confirms a time, so
  // browsing with a full basket takes nothing off anyone else.
  const [basket, setBasket] = useState([]);
  const [basketNotice, setBasketNotice] = useState("");

  const verified = user?.pantry_verified === true;

  const refresh = useCallback(async () => {
    try {
      const [items, mine, places] = await Promise.all([
        api.listItems(),
        api.myReservations(),
        // A zip that stops resolving mid-session (the lookup service blips)
        // falls back to the plain list rather than failing the whole refresh.
        api.pickupLocations(zipRef.current).catch(() => api.pickupLocations()),
      ]);
      setAvailable(items);
      setReservations(mine);
      setLocations(places.locations);
      setCenter(places.center);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  async function searchZip(zip) {
    setSearching(true);
    setSearchError("");
    try {
      const result = await api.pickupLocations(zip);
      zipRef.current = result.center.zip;
      setCenter(result.center);
      setLocations(result.locations);
      setSelectedStoreId(null);
    } catch (err) {
      setSearchError(err.message);
    } finally {
      setSearching(false);
    }
  }

  function clearZip() {
    zipRef.current = "";
    setSearchError("");
    refresh();
  }

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    const id = setInterval(refresh, MAP_REFRESH_MS);
    return () => clearInterval(id);
  }, [refresh]);

  // Someone else may claim an item while it sits in the basket. Drop it and
  // say so, rather than letting the whole order fail on it later.
  useEffect(() => {
    const gone = basket.filter((b) => !available.some((i) => i.id === b.id));
    if (gone.length === 0) return;
    setBasket(basket.filter((b) => available.some((i) => i.id === b.id)));
    setBasketNotice(
      gone.length === 1
        ? `“${gone[0].name}” was taken or is no longer available, so it was removed from your pickup.`
        : `${gone.length} items were taken or are no longer available, so they were removed from your pickup.`,
    );
    // Only when the donation list changes — not on every basket edit.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [available]);

  const storeNames = useMemo(
    () => Object.fromEntries(locations.map((s) => [s.id, s.name])),
    [locations],
  );
  const basketStoreId = basket[0]?.store_id ?? null;

  const categories = useMemo(
    () => [...new Set(available.map((i) => i.category).filter(Boolean))].sort(),
    [available],
  );

  // FR-8.3
  const visible = useMemo(() => {
    const rows = available.filter(
      (i) =>
        (!category || i.category === category) &&
        (!selectedStoreId || i.store_id === selectedStoreId),
    );
    return [...rows].sort((a, b) =>
      sortBy === "name"
        ? a.name.localeCompare(b.name)
        : (parseUtc(a.sell_by_date)?.getTime() ?? Infinity) -
          (parseUtc(b.sell_by_date)?.getTime() ?? Infinity),
    );
  }, [available, category, selectedStoreId, sortBy]);

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

  // One card per trip: reservations sharing an order are shown together under
  // their one QR code; a lone reservation is its own card.
  const pickups = groupPickups(active);

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

  function toggleInBasket(item) {
    setError("");
    setBasketNotice("");
    if (basket.some((b) => b.id === item.id)) {
      setBasket(basket.filter((b) => b.id !== item.id));
      return;
    }
    // One trip goes to one shelf. Say which, so the fix is obvious.
    if (basket.length > 0 && item.store_id !== basketStoreId) {
      setError(
        `Your pickup is at ${storeNames[basketStoreId] || "another shelf"}, and a pickup covers one ` +
          `location. Reserve or clear it first to add items from ${storeNames[item.store_id] || "this shelf"}.`,
      );
      return;
    }
    setSchedulingId(null);
    setBasket([...basket, item]);
  }

  async function placeOrder(scheduledLocal) {
    setError("");
    try {
      await api.createOrder(
        basket.map((b) => b.id),
        scheduledLocal,
      );
      setBasket([]);
      setBasketNotice("");
      await refresh();
    } catch (err) {
      setError(err.message);
      // The refresh also drops anything that was just taken, so the basket
      // on screen matches what can actually be reserved.
      await refresh();
    }
  }

  async function cancelPickup(group) {
    setError("");
    try {
      const first = group[0];
      if (first.order_id) await api.cancelOrder(first.order_id);
      else await api.cancelReservation(first.id);
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
          {locations.length > 0 && (
            <Card
              title="Where the food is"
              className="lg:col-span-5"
              action={
                selectedStoreId && (
                  <button
                    onClick={() => setSelectedStoreId(null)}
                    className="text-xs font-medium text-gray-500 underline hover:text-gray-800"
                  >
                    Show all shelves
                  </button>
                )
              }
            >
              <ShelfFinder
                locations={locations}
                center={center}
                selectedId={selectedStoreId}
                onSelect={(id) => setSelectedStoreId(id === selectedStoreId ? null : id)}
                onSearch={searchZip}
                onClear={clearZip}
                searching={searching}
                searchError={searchError}
              />
            </Card>
          )}

          <div className="space-y-6 lg:col-span-3">
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
                            {storeNames[item.store_id] ? `${storeNames[item.store_id]} · ` : ""}
                            {item.category || "Uncategorized"} · sell-by{" "}
                            {formatDate(item.sell_by_date)}
                          </div>
                        </div>
                        <div className="flex gap-2">
                          {/* Several items, one trip, one QR code (FR-8.13). */}
                          <button
                            onClick={() => toggleInBasket(item)}
                            disabled={!verified}
                            aria-pressed={basket.some((b) => b.id === item.id)}
                            title={
                              verified ? undefined : "Available once your organization is verified"
                            }
                            className={`rounded-lg border px-3 py-1.5 text-sm font-medium disabled:cursor-not-allowed disabled:border-gray-200 disabled:text-gray-400 ${
                              basket.some((b) => b.id === item.id)
                                ? "border-emerald-600 bg-emerald-50 text-emerald-800"
                                : "border-emerald-600 text-emerald-700 hover:bg-emerald-50"
                            }`}
                          >
                            {basket.some((b) => b.id === item.id) ? "✓ In pickup" : "Add to pickup"}
                          </button>
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
                            {schedulingId === item.id ? "Close" : "Reserve now"}
                          </button>
                        </div>
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
            {(basket.length > 0 || basketNotice) && (
              <PickupBasket
                basket={basket}
                storeName={storeNames[basketStoreId]}
                notice={basketNotice}
                onRemove={toggleInBasket}
                onClear={() => {
                  setBasket([]);
                  setBasketNotice("");
                }}
                onConfirm={placeOrder}
              />
            )}

            <Card title={`Your pickups (${pickups.length})`}>
              {pickups.length === 0 ? (
                <Empty>No active reservations.</Empty>
              ) : (
                <div className="space-y-4">
                  {pickups.map((group) => (
                    <PickupCard
                      key={group[0].order_id || group[0].id}
                      group={group}
                      onCancel={() => cancelPickup(group)}
                    />
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
function SchedulePicker({
  item,
  onConfirm,
  onCancel,
  prompt = "When will you collect this?",
  confirmLabel = "Confirm reservation",
  subject = "This item",
}) {
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
          ? `${subject} has to be off the shelf before then. Pick an earlier time.`
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
        {prompt}
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
          {confirmLabel}
        </button>
        {onCancel && (
          <button
            type="button"
            onClick={onCancel}
            className="text-xs font-medium text-gray-500 underline hover:text-gray-800"
          >
            Cancel
          </button>
        )}
      </div>
      <p className="mt-2 text-xs text-gray-500">
        Book up to 24 hours ahead. The hold is released {GRACE_MINUTES} minutes
        after your slot, and anything uncollected goes back into the pool.
        {cappedByDiscard && (
          <>
            {" "}
            {subject} has to be off the shelf by {formatDateTime(item.discard_after)}.
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

/**
 * The pickup in progress: items added with "Add to pickup", waiting on a time.
 * Nothing is reserved until the time is confirmed, and then it is all of them
 * or none (FR-8.13).
 */
function PickupBasket({ basket, storeName, notice, onRemove, onClear, onConfirm }) {
  // The earliest-dated item decides how late the slot may be: a slot past any
  // one item's discard time would be refused, so the picker bounds itself by it.
  const earliest = useMemo(() => {
    const dated = basket
      .map((b) => b.discard_after)
      .filter(Boolean)
      .sort((a, b) => parseUtc(a) - parseUtc(b));
    return { id: "basket", discard_after: dated[0] ?? null };
  }, [basket]);

  return (
    <Card
      title={`Your pickup (${basket.length} item${basket.length === 1 ? "" : "s"})`}
      action={
        basket.length > 0 && (
          <button
            onClick={onClear}
            className="text-xs font-medium text-gray-500 underline hover:text-gray-800"
          >
            Clear
          </button>
        )
      }
    >
      {notice && (
        <p role="status" className="mb-3 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-900">
          {notice}
        </p>
      )}
      {basket.length > 0 && (
        <>
          {storeName && (
            <p className="mb-2 text-xs text-gray-500">
              Collect all of these at <span className="font-medium text-gray-700">{storeName}</span>{" "}
              with one QR code.
            </p>
          )}
          <ul className="divide-y divide-gray-100">
            {basket.map((item) => (
              <li key={item.id} className="flex items-center justify-between gap-3 py-2 text-sm">
                <div className="min-w-0">
                  <div className="truncate font-medium">{item.name}</div>
                  <div className="text-xs text-gray-500">
                    {item.category || "Uncategorized"} · sell-by {formatDate(item.sell_by_date)}
                  </div>
                </div>
                <button
                  onClick={() => onRemove(item)}
                  aria-label={`Remove ${item.name} from pickup`}
                  className="text-xs font-medium text-gray-500 underline hover:text-gray-800"
                >
                  Remove
                </button>
              </li>
            ))}
          </ul>
          <SchedulePicker
            item={earliest}
            prompt="When will you collect these?"
            confirmLabel={`Reserve ${basket.length} item${basket.length === 1 ? "" : "s"}`}
            subject="The earliest-dated item"
            onConfirm={onConfirm}
          />
        </>
      )}
    </Card>
  );
}

/**
 * Reservations that share an order are one trip with one QR code, so they are
 * shown as one card. A reservation with no order is its own group. The input
 * is already sorted soonest-first, and groups keep the order they first appear.
 */
function groupPickups(reservations) {
  const groups = new Map();
  for (const r of reservations) {
    const key = r.order_id || r.id;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(r);
  }
  return [...groups.values()];
}

function PickupCard({ group, onCancel }) {
  const reservation = group[0];
  const isOrder = Boolean(reservation.order_id);
  // An order carries one code for every item in it; a lone reservation has its own.
  const code = isOrder ? reservation.order_qr_code : reservation.qr_code;
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
          {isOrder ? (
            <>
              <div className="font-medium">
                {group.length} item{group.length === 1 ? "" : "s"}
                {reservation.store_name ? ` · ${reservation.store_name}` : ""}
              </div>
              <ul className="mt-1 space-y-0.5 text-sm text-gray-700">
                {group.map((r) => (
                  <li key={r.id}>
                    {r.item_name}
                    {r.item_category && <span className="text-xs text-gray-500"> · {r.item_category}</span>}
                  </li>
                ))}
              </ul>
            </>
          ) : (
            <>
              <div className="font-medium">{reservation.item_name}</div>
              <div className="mt-0.5 text-xs text-gray-500">
                {reservation.store_name ? `${reservation.store_name} · ` : ""}
                {reservation.shelf_name || "Shelf not set"}
                {reservation.item_category ? ` · ${reservation.item_category}` : ""}
              </div>
            </>
          )}
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
      {code && (
        <div className="mt-4 flex flex-col items-center gap-2 rounded-lg bg-white p-4">
          <QRCodeSVG value={code} size={200} level="M" marginSize={2} />
          <code className="select-all text-center text-xs tracking-wide text-gray-500">{code}</code>
          <p className="text-center text-xs text-gray-500">
            {isOrder
              ? "One code for everything above. Show it to store staff at the shelf."
              : "Show this to store staff at the shelf."}
          </p>
        </div>
      )}

      <div
        className={`mt-3 text-sm ${urgent ? "font-medium text-amber-700" : "text-gray-600"}`}
      >
        {remaining.expired
          ? `Hold window has lapsed — ${isOrder ? "these items" : "this item"} may have returned to the pool.`
          : `${remaining.label} left to collect — the hold is released at ${formatTime(
              reservation.hold_expires_at,
            )}, ${GRACE_MINUTES} minutes after your slot.`}
      </div>

      <button
        onClick={onCancel}
        className="mt-3 text-xs font-medium text-gray-500 underline hover:text-gray-800"
      >
        {isOrder ? "Cancel whole pickup" : "Cancel reservation"}
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
