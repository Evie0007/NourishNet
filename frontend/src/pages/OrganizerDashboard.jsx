import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { QRCodeSVG } from "qrcode.react";
import { api, parseUtc, toLocalInputValue } from "../api";
import { useAuth } from "../auth";
import Shell, { Card, Empty, ErrorBanner, StatusBadge } from "../components/Shell";
import ShelfFinder from "../components/ShelfFinder";
import { groupItems, orderNumber, tallyItems } from "../orders";

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
  // The order in progress: [{ key, qty }], one line per group of identical
  // units (see groupItems). Held here rather than on the server: nothing is
  // claimed until the organization confirms a time, so browsing with a full
  // basket takes nothing off anyone else.
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

  // Everything on offer, as groups of identical units, and by key so a basket
  // line can find its group.
  const allGroups = useMemo(() => groupItems(available), [available]);
  const groupByKey = useMemo(() => new Map(allGroups.map((g) => [g.key, g])), [allGroups]);

  // Someone else may claim units while they sit in the basket. Trim the line
  // (or drop it) and say so, rather than letting the whole order fail later.
  useEffect(() => {
    if (basket.length === 0) return;
    let lost = 0;
    const next = [];
    for (const line of basket) {
      const group = groupByKey.get(line.key);
      const qty = group ? Math.min(line.qty, group.items.length) : 0;
      lost += line.qty - qty;
      if (qty > 0) next.push({ key: line.key, qty });
    }
    if (lost === 0) return;
    setBasket(next);
    setBasketNotice(
      `${lost} ${lost === 1 ? "item was" : "items were"} taken or no longer available, so ${
        lost === 1 ? "it was" : "they were"
      } removed from your order.`,
    );
    // Only when the donation list changes — not on every basket edit.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [groupByKey]);

  // The basket with its groups attached, for display and for placing the order.
  const lines = useMemo(
    () =>
      basket
        .map((line) => ({ ...line, group: groupByKey.get(line.key) }))
        .filter((line) => line.group),
    [basket, groupByKey],
  );

  const storeNames = useMemo(
    () => Object.fromEntries(locations.map((s) => [s.id, s.name])),
    [locations],
  );
  // An order is one trip to one shelf under one QR code (FR-8.13), so a
  // basket spanning shelves is shown, timed and placed as one order per shelf
  // rather than refused. First-added shelf first.
  const shelfOrders = useMemo(() => {
    const byStore = new Map();
    for (const line of lines) {
      const id = line.group.store_id;
      if (!byStore.has(id)) byStore.set(id, []);
      byStore.get(id).push(line);
    }
    return [...byStore].map(([storeId, storeLines]) => ({ storeId, lines: storeLines }));
  }, [lines]);

  const categories = useMemo(
    () => [...new Set(available.map((i) => i.category).filter(Boolean))].sort(),
    [available],
  );

  // FR-8.3
  const visible = useMemo(() => {
    const rows = allGroups.filter(
      (g) =>
        (!category || g.category === category) &&
        (!selectedStoreId || g.store_id === selectedStoreId),
    );
    return [...rows].sort((a, b) =>
      sortBy === "name"
        ? a.name.localeCompare(b.name)
        : (parseUtc(a.sell_by_date)?.getTime() ?? Infinity) -
          (parseUtc(b.sell_by_date)?.getTime() ?? Infinity),
    );
  }, [allGroups, category, selectedStoreId, sortBy]);

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

  // Set how many units of a group are in the order. 0 removes the line.
  function setQuantity(group, qty) {
    setError("");
    setBasketNotice("");
    const clamped = Math.max(0, Math.min(qty, group.items.length));
    if (clamped === 0) {
      setBasket(basket.filter((b) => b.key !== group.key));
      return;
    }
    setBasket(
      basket.some((b) => b.key === group.key)
        ? basket.map((b) => (b.key === group.key ? { ...b, qty: clamped } : b))
        : [...basket, { key: group.key, qty: clamped }],
    );
  }

  // Places one shelf's order. The other shelves' lines stay in the basket, so
  // a failure at one shelf never costs the pantry what it picked elsewhere.
  async function placeOrder(storeLines, scheduledLocal) {
    setError("");
    try {
      // Each line asks for N of a group; the order is the first N real units.
      const itemIds = storeLines.flatMap((line) =>
        line.group.items.slice(0, line.qty).map((i) => i.id),
      );
      await api.createOrder(itemIds, scheduledLocal);
      const placed = new Set(storeLines.map((line) => line.key));
      setBasket((current) => current.filter((b) => !placed.has(b.key)));
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
      subtitle="Browse what's available, add it to an order, and show the order's code at the shelf."
      fixed
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
        // On large screens the page fits the window and each panel scrolls on
        // its own: the map and the list stay side by side with the order, so
        // adding an item never scrolls the basket out of view.
        <div className="grid gap-6 lg:min-h-0 lg:flex-1 lg:grid-cols-5 lg:grid-rows-1">
          <div
            className={`flex flex-col gap-6 lg:col-span-3 lg:grid lg:min-h-0 ${
              locations.length > 0
                ? "lg:grid-rows-[minmax(0,2fr)_minmax(0,3fr)]"
                : "lg:grid-rows-1"
            }`}
          >
            {locations.length > 0 && (
              <Card
                title="Where the food is"
                className="lg:flex lg:min-h-0 lg:flex-col"
                bodyClassName="p-4 lg:min-h-0 lg:flex-1"
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
                  fill
                />
              </Card>
            )}

            <Card
              title={`Available donations (${visible.length})`}
              className="lg:flex lg:min-h-0 lg:flex-col"
              bodyClassName="p-4 lg:min-h-0 lg:flex-1 lg:overflow-y-auto"
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
                // FR-8.2. Name with its type, sell-by, and quantity each get a
                // column, so a large quantity reads as a number rather than a
                // long list of identical rows.
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-gray-100 text-left text-xs text-gray-500">
                        <th className="pb-2 pr-4 font-medium">Item</th>
                        <th className="pb-2 pr-4 font-medium">Sell-by</th>
                        <th className="pb-2 pr-4 text-right font-medium">Quantity</th>
                        <th className="pb-2">
                          <span className="sr-only">Add to pickup</span>
                        </th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-100">
                      {visible.map((group) => (
                        <tr key={group.key}>
                          <td className="py-3 pr-4">
                            <div className="font-medium">{group.name}</div>
                            <div className="mt-0.5 text-xs text-gray-500">
                              {group.category || "Uncategorized"}
                            </div>
                          </td>
                          <td className="whitespace-nowrap py-3 pr-4 text-gray-700">
                            {formatDate(group.sell_by_date)}
                          </td>
                          <td className="py-3 pr-4 text-right tabular-nums">{group.items.length}</td>
                          <td className="py-3 text-right">
                            <AddToOrder
                              group={group}
                              qty={basket.find((b) => b.key === group.key)?.qty ?? 0}
                              onChange={(qty) => setQuantity(group, qty)}
                              verified={verified}
                            />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>
          </div>

          <div className="space-y-6 lg:col-span-2 lg:min-h-0 lg:overflow-y-auto">
            {basketNotice && shelfOrders.length === 0 && (
              <PickupBasket lines={[]} notice={basketNotice} />
            )}
            {shelfOrders.map(({ storeId, lines: storeLines }, index) => (
              <PickupBasket
                key={storeId}
                lines={storeLines}
                storeName={storeNames[storeId]}
                notice={index === 0 ? basketNotice : ""}
                onChange={(line, qty) => setQuantity(line.group, qty)}
                onClear={() => {
                  const cleared = new Set(storeLines.map((line) => line.key));
                  setBasket((current) => current.filter((b) => !cleared.has(b.key)));
                  setBasketNotice("");
                }}
                onConfirm={(scheduledLocal) => placeOrder(storeLines, scheduledLocal)}
              />
            ))}

            <Card title={`Your orders (${pickups.length})`}>
              {pickups.length === 0 ? (
                <Empty>No active orders. Add items to an order to get a pickup code.</Empty>
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
              <Card title="Order history">
                <OrderHistory reservations={past} />
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
  confirmLabel = "Place order",
  subject = "This item",
}) {
  const [value, setValue] = useState(() => toLocalInputValue(defaultSlot()));
  const [problem, setProblem] = useState("");

  // Rounded up to a 5-minute mark, not "now". The control steps in 5 minutes
  // counting from its minimum, so a minimum of 10:24 makes 10:30 an invalid
  // value and the browser refuses the default slot with "the nearest valid
  // values are 10:29 and 10:34" — but only on minutes that are not multiples
  // of 5, which is what made it look intermittent.
  const min = useMemo(() => toLocalInputValue(nextFiveMinutes()), []);

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

/** Now, rounded up to the next 5-minute mark (a multiple of 5 minutes since the
 *  epoch, which is a 5-minute mark on the clock in every real time zone). */
function nextFiveMinutes() {
  const step = 5 * 60_000;
  return new Date(Math.ceil(Date.now() / step) * step);
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
 * "Add to pickup" until something is in the order, then a quantity stepper:
 * the same control in the list and in the order, like an online grocery order.
 * Taking the quantity to zero removes the line. The most you can ask for is
 * the number of identical units on the shelf.
 */
function AddToOrder({ group, qty, onChange, verified }) {
  const max = group.items.length;

  if (qty === 0) {
    return (
      <button
        onClick={() => onChange(1)}
        disabled={!verified}
        title={verified ? undefined : "Available once your organization is verified"}
        className="rounded-lg border border-emerald-600 px-3 py-1.5 text-sm font-medium text-emerald-700 hover:bg-emerald-50 disabled:cursor-not-allowed disabled:border-gray-200 disabled:text-gray-400"
      >
        Add to pickup
      </button>
    );
  }

  return (
    <div
      role="group"
      aria-label={`Quantity of ${group.name} in your order`}
      className="inline-flex items-center overflow-hidden rounded-lg border border-emerald-600 bg-emerald-50 text-emerald-800"
    >
      <button
        onClick={() => onChange(qty - 1)}
        aria-label={`Remove one ${group.name}`}
        className="px-3 py-1.5 text-sm font-medium hover:bg-emerald-100"
      >
        −
      </button>
      <span aria-live="polite" className="min-w-8 px-1 text-center text-sm font-medium tabular-nums">
        {qty}
      </span>
      <button
        onClick={() => onChange(qty + 1)}
        disabled={qty >= max}
        aria-label={`Add one more ${group.name}`}
        title={qty >= max ? `Only ${max} available` : undefined}
        className="px-3 py-1.5 text-sm font-medium hover:bg-emerald-100 disabled:cursor-not-allowed disabled:text-emerald-300 disabled:hover:bg-transparent"
      >
        +
      </button>
    </div>
  );
}

/**
 * The order in progress: what has been added, waiting on a pickup time.
 * Nothing is reserved until the order is placed, and then it is all of it or
 * none (FR-8.13). `lines` is [{ key, qty, group }], all from one shelf: the
 * dashboard renders one of these per shelf in the basket.
 */
function PickupBasket({ lines, storeName, notice, onChange, onClear, onConfirm }) {
  const totalUnits = lines.reduce((sum, line) => sum + line.qty, 0);

  // The earliest-dated unit decides how late the slot may be: a slot past any
  // one item's discard time would be refused, so the picker bounds itself by
  // the units actually chosen.
  const earliest = useMemo(() => {
    const dated = lines
      .flatMap((line) => line.group.items.slice(0, line.qty).map((i) => i.discard_after))
      .filter(Boolean)
      .sort((a, b) => parseUtc(a) - parseUtc(b));
    return { id: "basket", discard_after: dated[0] ?? null };
  }, [lines]);

  return (
    <Card
      title={`${storeName ? `Order at ${storeName}` : "Your order"} (${totalUnits} item${
        totalUnits === 1 ? "" : "s"
      })`}
      action={
        lines.length > 0 && (
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
      {lines.length > 0 && (
        <>
          {storeName && (
            <p className="mb-2 text-xs text-gray-500">
              Collect all of this at <span className="font-medium text-gray-700">{storeName}</span>{" "}
              with one QR code.
            </p>
          )}
          <ul className="divide-y divide-gray-100">
            {lines.map((line) => (
              <li key={line.key} className="flex items-center justify-between gap-3 py-2 text-sm">
                <div className="min-w-0">
                  <div className="truncate font-medium">{line.group.name}</div>
                  <div className="text-xs text-gray-500">
                    {line.group.category || "Uncategorized"} · sell-by {formatDate(line.group.sell_by_date)}
                  </div>
                </div>
                <AddToOrder
                  group={line.group}
                  qty={line.qty}
                  onChange={(qty) => onChange(line, qty)}
                  verified
                />
              </li>
            ))}
          </ul>
          <SchedulePicker
            item={earliest}
            prompt="When will you collect this order?"
            confirmLabel={`Place order · ${totalUnits} item${totalUnits === 1 ? "" : "s"}`}
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
 * shown as one group. A reservation with no order (made before orders
 * existed) is its own group. Groups keep the order they first appear in.
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
              <div className="font-medium">Order #{orderNumber(reservation.order_id)}</div>
              <div className="mt-0.5 text-xs text-gray-500">
                {reservation.store_name ? `${reservation.store_name} · ` : ""}
                {group.length} item{group.length === 1 ? "" : "s"}
              </div>
              <ul className="mt-2 space-y-0.5 text-sm text-gray-700">
                {tallyItems(group).map((line) => (
                  <li key={`${line.name}|${line.category}`}>
                    {line.name}
                    {line.count > 1 && <span className="font-medium"> × {line.count}</span>}
                    {line.category && <span className="text-xs text-gray-500"> · {line.category}</span>}
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
              ? "One code for the whole order. Show it to store staff at the shelf."
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
        {isOrder ? "Cancel order" : "Cancel reservation"}
      </button>
    </div>
  );
}

// Past orders are a log, not a worklist: show the latest few and keep the
// rest a click away, so a long history does not push everything else down.
const HISTORY_ORDERS_SHOWN = 6;

/**
 * Past orders, one row each, however many items they held. An order's items
 * are tucked under its row; a reservation from before orders existed is a
 * plain row of its own. The order's status is read off its items: it counts
 * as picked up if any item was collected.
 */
function OrderHistory({ reservations }) {
  const [showAll, setShowAll] = useState(false);

  const orders = useMemo(() => {
    const when = (r) => parseUtc(r.picked_up_at || r.hold_expires_at)?.getTime() ?? 0;
    return groupPickups(reservations)
      .map((group) => ({ group, at: Math.max(...group.map(when)) }))
      .sort((a, b) => b.at - a.at);
  }, [reservations]);

  const shown = showAll ? orders : orders.slice(0, HISTORY_ORDERS_SHOWN);

  return (
    <>
      <ul className="divide-y divide-gray-100">
        {shown.map(({ group, at }) => {
          const first = group[0];
          const isOrder = Boolean(first.order_id);
          const status = group.some((r) => r.status === "picked_up")
            ? "picked_up"
            : group.some((r) => r.status === "expired")
              ? "expired"
              : "cancelled";
          const summary = (
            <>
              <div className="min-w-0">
                <div className="truncate font-medium">
                  {isOrder ? `Order #${orderNumber(first.order_id)}` : first.item_name}
                </div>
                <div className="text-xs text-gray-500">
                  {isOrder ? `${group.length} item${group.length === 1 ? "" : "s"} · ` : ""}
                  {formatDateTime(new Date(at).toISOString())}
                </div>
              </div>
              <StatusBadge status={status} />
            </>
          );
          return isOrder ? (
            <li key={first.order_id}>
              <details className="group py-2 text-sm">
                <summary className="flex cursor-pointer list-none items-center justify-between gap-3">
                  {summary}
                </summary>
                <ul className="mt-2 space-y-0.5 pl-1 text-xs text-gray-600">
                  {tallyItems(group).map((line) => (
                    <li key={`${line.name}|${line.category}`}>
                      {line.name}
                      {line.count > 1 && <span className="font-medium"> × {line.count}</span>}
                      {line.category && <span className="text-gray-400"> · {line.category}</span>}
                    </li>
                  ))}
                </ul>
              </details>
            </li>
          ) : (
            <li key={first.id} className="flex items-center justify-between gap-3 py-2 text-sm">
              {summary}
            </li>
          );
        })}
      </ul>
      {orders.length > HISTORY_ORDERS_SHOWN && (
        <button
          onClick={() => setShowAll((all) => !all)}
          className="mt-2 text-xs font-medium text-gray-500 underline hover:text-gray-800"
        >
          {showAll ? "Show fewer" : `Show all ${orders.length} orders`}
        </button>
      )}
    </>
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
