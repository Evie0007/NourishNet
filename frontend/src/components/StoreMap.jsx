import { useEffect } from "react";
import { CircleMarker, MapContainer, Popup, TileLayer, Tooltip, useMap } from "react-leaflet";
import "leaflet/dist/leaflet.css";
import { availabilityText } from "../availability";

// Fallback view for an empty map: San Jose, where the demo shelves are.
const DEFAULT_CENTER = [37.33, -121.89];

// Closer than this and a street is readable; the map never zooms past it when
// framing a single shelf.
const MAX_FIT_ZOOM = 14;

const SPOT = {
  available: { color: "#064e3b", fill: "#10b981" },
  unavailable: { color: "#6b7280", fill: "#d1d5db" },
};

/**
 * Shelf locations as spots on a map: green where there is food to collect,
 * grey where the shelf is closed or empty. A blue spot marks the zip code
 * that was searched.
 *
 * Colour is never the only signal. Every spot carries its name as a label and
 * its availability in the popup, and the side panel beside the map lists the
 * same shelves in text, so the map works for someone who can't tell green from
 * grey and for keyboard users who can't reach a spot.
 *
 * Markers are circles rather than the default pin, because Vite does not
 * bundle Leaflet's marker image by default and a broken pin looks like a bug.
 */
export default function StoreMap({ locations, selectedId, onSelect, center }) {
  const start = locations.length
    ? [locations[0].latitude, locations[0].longitude]
    : DEFAULT_CENTER;

  return (
    <MapContainer
      center={start}
      zoom={12}
      scrollWheelZoom={false}
      className="h-80 w-full rounded-lg lg:h-[26rem]"
    >
      <TileLayer
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
      />
      <FitView locations={locations} center={center} />
      <PanToSelected locations={locations} selectedId={selectedId} />

      {center && (
        <CircleMarker
          center={[center.latitude, center.longitude]}
          radius={8}
          pathOptions={{ color: "#ffffff", fillColor: "#2563eb", fillOpacity: 1, weight: 3 }}
        >
          <Tooltip permanent direction="top" offset={[0, -8]}>
            Zip {center.zip}
          </Tooltip>
        </CircleMarker>
      )}

      {locations.map((place) => {
        const selected = place.id === selectedId;
        const look = place.available ? SPOT.available : SPOT.unavailable;
        return (
          <CircleMarker
            key={place.id}
            center={[place.latitude, place.longitude]}
            radius={selected ? 13 : 10}
            pathOptions={{
              color: selected ? "#111827" : look.color,
              fillColor: look.fill,
              fillOpacity: place.available ? 0.9 : 0.8,
              weight: selected ? 3 : 2,
              dashArray: place.available ? undefined : "3 3",
            }}
            eventHandlers={{ click: () => onSelect(place.id) }}
          >
            <Tooltip permanent direction="right" offset={[12, 0]}>
              {place.name}
              {!place.available && " · unavailable"}
            </Tooltip>
            <Popup>
              <div className="text-sm">
                <div className="font-medium">{place.name}</div>
                {place.address && <div className="text-gray-600">{place.address}</div>}
                <div className="mt-1">{availabilityText(place)}</div>
                {place.distance_miles != null && (
                  <div className="text-gray-600">{place.distance_miles} mi from you</div>
                )}
              </div>
            </Popup>
          </CircleMarker>
        );
      })}
    </MapContainer>
  );
}

// Frame the shelves on the first load and whenever the set of shelves or the
// searched zip changes. Not on every poll: a pantry who has panned away would
// be snapped back each minute. With a zip, frame the searcher and the closest
// shelf that can actually help, which is the answer they came for.
function FitView({ locations, center }) {
  const map = useMap();
  const key = `${locations.map((l) => l.id).join("|")}@${center ? center.zip : ""}`;

  useEffect(() => {
    const points = [];
    if (center) {
      points.push([center.latitude, center.longitude]);
      const nearest = locations.find((l) => l.available) || locations[0];
      if (nearest) points.push([nearest.latitude, nearest.longitude]);
    } else {
      locations.forEach((l) => points.push([l.latitude, l.longitude]));
    }
    if (points.length === 0) return;
    if (points.length === 1) {
      map.setView(points[0], MAX_FIT_ZOOM);
      return;
    }
    map.fitBounds(points, { padding: [48, 48], maxZoom: MAX_FIT_ZOOM });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, map]);

  return null;
}

// Choosing a shelf in the list should show it, not just highlight it.
function PanToSelected({ locations, selectedId }) {
  const map = useMap();
  useEffect(() => {
    const place = locations.find((l) => l.id === selectedId);
    if (place) map.panTo([place.latitude, place.longitude]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId, map]);
  return null;
}
