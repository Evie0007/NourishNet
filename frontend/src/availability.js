/**
 * How a shelf location's availability reads, in words. Shared by the map's
 * popup and the side panel's list so the two never word it differently, and
 * kept out of the component files so they only export components.
 *
 * `place` is a row from GET /stores/pickup-locations.
 */
export function availabilityText(place) {
  if (place.unavailable_reason === "closed") return "Unavailable — closed for pickups right now";
  if (place.unavailable_reason === "no_items") return "Unavailable — no items to collect right now";
  return `${place.available_count} item${place.available_count === 1 ? "" : "s"} available`;
}
