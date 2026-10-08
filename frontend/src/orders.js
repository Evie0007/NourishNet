/**
 * Shared by the organizer and store dashboards: how an order is named, and how
 * many identical units are shown as one row.
 */

/**
 * The short order number people read out and look for, e.g. "A1B2C3D4".
 * It is the first eight characters of the order's ID, so it needs no column of
 * its own and always agrees with the record. It is for people to recognise an
 * order by; the QR code, not this, is what redeems it. Null for a reservation
 * that was made without an order.
 */
export function orderNumber(orderId) {
  return orderId ? orderId.replace(/-/g, "").slice(0, 8).toUpperCase() : null;
}

/**
 * The items in an order, counted by what they are: "Whole Milk × 6", not six
 * identical lines. Takes reservations (each with item_name / item_category) and
 * returns [{ name, category, count }] in first-seen order.
 */
export function tallyItems(reservations) {
  const counts = new Map();
  for (const r of reservations) {
    const key = `${r.item_name}|${r.item_category}`;
    const entry = counts.get(key) || { name: r.item_name, category: r.item_category, count: 0 };
    entry.count += 1;
    counts.set(key, entry);
  }
  return [...counts.values()];
}

/** The same tally on one line, for places with room for a sentence, not a list. */
export function describeItems(reservations) {
  return tallyItems(reservations)
    .map((t) => (t.count > 1 ? `${t.name} × ${t.count}` : t.name))
    .join(", ");
}

/** `describeItems` for a plain list of names, as a scan result carries. */
export function describeNames(names) {
  const counts = new Map();
  for (const name of names ?? []) counts.set(name, (counts.get(name) || 0) + 1);
  return [...counts.entries()].map(([name, n]) => (n > 1 ? `${name} × ${n}` : name)).join(", ");
}

/**
 * Items are stored one row per physical unit: a delivery of six identical
 * cartons is six sibling rows. A pantry choosing food wants "Whole Milk · 6",
 * not six lines, so units that are the same thing (same shelf, name, type and
 * sell-by) are shown as one row with a quantity. `items` keeps the individual
 * units, so choosing three of six still reserves three real rows.
 *
 * The key is stable across refreshes while any unit remains, so a basket can
 * refer to a group and have its quantity trimmed if someone else takes some.
 */
export function groupItems(items) {
  const groups = new Map();
  for (const item of items) {
    const key = [item.store_id, item.name, item.category, item.sell_by_date].join("|");
    if (!groups.has(key)) {
      groups.set(key, {
        key,
        name: item.name,
        category: item.category,
        sell_by_date: item.sell_by_date,
        store_id: item.store_id,
        items: [],
      });
    }
    groups.get(key).items.push(item);
  }
  return [...groups.values()];
}
