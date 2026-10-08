import { useState } from "react";
import { availabilityText } from "../availability";
import StoreMap from "./StoreMap";

/**
 * The pantry map with its side panel (FR-8.14): enter a zip code, see the
 * nearest shelves first, and tell at a glance which ones have food to collect.
 *
 * The search itself lives in the dashboard, not here, because the dashboard
 * re-fetches on a timer and has to keep using the same zip each time. This
 * component only holds what is typed into the box.
 */
export default function ShelfFinder({
  locations,
  center,
  selectedId,
  onSelect,
  onSearch,
  onClear,
  searching,
  searchError,
}) {
  const [typed, setTyped] = useState("");

  function submit(e) {
    e.preventDefault();
    const zip = typed.trim();
    if (zip) onSearch(zip);
  }

  const availableCount = locations.filter((l) => l.available).length;
  // The first available row of a zip search is the answer to "where should I
  // go", so it says so rather than leaving the pantry to read distances.
  const closestAvailableId = center ? locations.find((l) => l.available)?.id : null;

  return (
    <div className="grid gap-4 lg:grid-cols-[18rem_1fr]">
      <div className="flex min-w-0 flex-col gap-3">
        <form onSubmit={submit}>
          <label htmlFor="zip-search" className="block text-xs font-medium text-gray-700">
            Find the closest shelf
          </label>
          <div className="mt-1.5 flex gap-2">
            <input
              id="zip-search"
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              inputMode="numeric"
              autoComplete="postal-code"
              maxLength={10}
              placeholder="Your zip code"
              className="min-w-0 flex-1 rounded-lg border border-gray-300 px-3 py-2 text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-100"
            />
            <button
              disabled={searching || !typed.trim()}
              className="rounded-lg bg-emerald-600 px-3 py-2 text-sm font-medium text-white hover:bg-emerald-700 disabled:cursor-not-allowed disabled:bg-gray-300"
            >
              {searching ? "…" : "Search"}
            </button>
          </div>
          {searchError && (
            <p role="alert" className="mt-1.5 text-xs font-medium text-amber-700">
              {searchError}
            </p>
          )}
        </form>

        {center && (
          <div className="flex items-center justify-between gap-2 rounded-lg bg-blue-50 px-3 py-2 text-xs text-blue-900">
            <span>
              Nearest first, from zip <span className="font-medium">{center.zip}</span>
            </span>
            <button
              type="button"
              onClick={() => {
                setTyped("");
                onClear();
              }}
              className="font-medium underline hover:text-blue-700"
            >
              Clear
            </button>
          </div>
        )}

        <p className="text-xs text-gray-500">
          {availableCount} of {locations.length} shelves available for pickup.
        </p>

        <ul className="space-y-2" aria-label="Shelves">
          {locations.map((place) => {
            const selected = place.id === selectedId;
            return (
              <li key={place.id}>
                <button
                  type="button"
                  onClick={() => onSelect(place.id)}
                  aria-pressed={selected}
                  className={`w-full rounded-lg border px-3 py-2.5 text-left transition-colors ${
                    selected
                      ? "border-emerald-600 bg-emerald-50"
                      : "border-gray-200 hover:border-emerald-400"
                  } ${place.available ? "" : "bg-gray-50"}`}
                >
                  <div className="flex items-start justify-between gap-2">
                    <span className={`font-medium ${place.available ? "" : "text-gray-500"}`}>
                      {place.name}
                    </span>
                    {place.distance_miles != null && (
                      <span className="whitespace-nowrap text-xs tabular-nums text-gray-500">
                        {place.distance_miles} mi
                      </span>
                    )}
                  </div>
                  {place.address && (
                    <div className="mt-0.5 text-xs text-gray-500">{place.address}</div>
                  )}
                  <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                    <span
                      className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${
                        place.available
                          ? "bg-emerald-100 text-emerald-800"
                          : "bg-gray-200 text-gray-700"
                      }`}
                    >
                      {availabilityText(place)}
                    </span>
                    {place.id === closestAvailableId && (
                      <span className="inline-block rounded-full bg-blue-100 px-2 py-0.5 text-xs font-medium text-blue-800">
                        Closest available
                      </span>
                    )}
                  </div>
                </button>
              </li>
            );
          })}
        </ul>
      </div>

      <div className="min-w-0">
        <StoreMap locations={locations} center={center} selectedId={selectedId} onSelect={onSelect} />
      </div>
    </div>
  );
}
