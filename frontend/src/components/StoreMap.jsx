import { useEffect } from "react";
import { CircleMarker, MapContainer, Popup, TileLayer, useMap } from "react-leaflet";
import "leaflet/dist/leaflet.css";

// Fallback view for an empty map: San Jose, where the demo stores are.
const DEFAULT_CENTER = [37.33, -121.89];

/**
 * Stores on a map, each marked with how much food it has available. The map
 * is a picture, not the only way in: the list of store buttons in the card
 * above it does the same job for keyboard users and small screens.
 *
 * Markers are circles rather than the default pin, because Vite does not
 * bundle Leaflet's marker image by default and a broken pin looks like a bug.
 */
export default function StoreMap({ stores, countByStore, selectedId, onSelect }) {
  const center = stores.length ? [stores[0].latitude, stores[0].longitude] : DEFAULT_CENTER;

  return (
    <MapContainer
      center={center}
      zoom={12}
      scrollWheelZoom={false}
      className="h-64 w-full rounded-lg"
    >
      <TileLayer
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
      />
      <FitToStores stores={stores} />
      {stores.map((store) => {
        const selected = store.id === selectedId;
        const available = countByStore[store.id] || 0;
        return (
          <CircleMarker
            key={store.id}
            center={[store.latitude, store.longitude]}
            radius={selected ? 12 : 9}
            pathOptions={{
              color: selected ? "#064e3b" : "#059669",
              fillColor: "#10b981",
              fillOpacity: 0.85,
              weight: 2,
            }}
            eventHandlers={{ click: () => onSelect(store.id) }}
          >
            <Popup>
              <div className="text-sm">
                <div className="font-medium">{store.name}</div>
                {store.address && <div className="text-gray-600">{store.address}</div>}
                <div className="mt-1">
                  {available} item{available === 1 ? "" : "s"} available
                </div>
              </div>
            </Popup>
          </CircleMarker>
        );
      })}
    </MapContainer>
  );
}

// Zoom so every store is visible. Runs when the store list changes, not on
// every poll, so a pantry who has panned away is not snapped back each minute.
function FitToStores({ stores }) {
  const map = useMap();
  const key = stores.map((s) => `${s.id}:${s.latitude},${s.longitude}`).join("|");

  useEffect(() => {
    if (stores.length < 2) return;
    map.fitBounds(stores.map((s) => [s.latitude, s.longitude]), { padding: [24, 24] });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, map]);

  return null;
}
