import { useCallback, useEffect, useRef, useState } from "react";
import { api, parseUtc } from "../api";
import BarcodeScanner from "../components/BarcodeScanner";
import { Card, Empty } from "../components/Shell";

/**
 * The intake station: scan a barcode, read the date, confirm it.
 *
 * Three deliberate choices about how this behaves under a person's hands,
 * because this screen is used standing at a loading dock with a box in one
 * arm, not sitting at a desk:
 *
 *  1. The UPC field holds focus. A hardware scanner types its digits like
 *     a keyboard and ends with Enter, so an always-focused field turns a
 *     scan into a submit with nothing to click.
 *  2. Nothing auto-advances past the date. The barcode step resolves on
 *     its own because a check digit either matches or it doesn't; the date
 *     step never does, because that is the judgment this whole screen
 *     exists to capture.
 *  3. Every failure has a way through. An unknown barcode, an unreadable
 *     label, no camera, Vision being down — each falls back to typing,
 *     and none of them stops the unit being received.
 */

const input =
  "rounded-lg border border-gray-300 px-3 py-1.5 text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-100";
const primary =
  "rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-60";
const secondary =
  "rounded-lg border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-60";

const LABEL_TYPES = [
  ["sell_by", "Sell by"],
  ["use_by", "Use by"],
  ["best_by", "Best by"],
  ["packed_on", "Packed on"],
];

export default function Intake({ shelves, onError, onChanged }) {
  const [scan, setScan] = useState(null);
  const [pending, setPending] = useState([]);

  const refreshPending = useCallback(async () => {
    try {
      setPending(await api.listScans("pending"));
    } catch (err) {
      onError(err.message);
    }
  }, [onError]);

  useEffect(() => {
    refreshPending();
  }, [refreshPending]);

  function handleResolved() {
    setScan(null);
    refreshPending();
    onChanged();
  }

  return (
    <div className="space-y-6">
      <p className="text-sm text-gray-600">
        Scan the barcode, capture the date, then confirm. Nothing reaches
        inventory until someone confirms it — the scanners propose, a person
        decides.
      </p>

      {scan ? (
        <ConfirmPanel
          // Remount on a different scan: picking another row out of the
          // pending list below must not leave the previous unit's name and
          // date sitting in the form.
          key={scan.id}
          scan={scan}
          shelves={shelves}
          onError={onError}
          onScanUpdated={setScan}
          onResolved={handleResolved}
          onCancel={() => setScan(null)}
        />
      ) : (
        <ScanPanel shelves={shelves} onError={onError} onScanned={setScan} />
      )}

      <Card title={`Waiting on confirmation (${pending.length})`}>
        {pending.length === 0 ? (
          <Empty>Nothing waiting. Every scan has been confirmed or turned away.</Empty>
        ) : (
          <ul className="divide-y divide-gray-100">
            {pending.map((item) => (
              <li key={item.id} className="flex items-center justify-between gap-3 py-2 text-sm">
                <div className="min-w-0">
                  <div className="font-medium">{item.product_name || "Unidentified item"}</div>
                  <div className="text-xs text-gray-500">
                    {item.display_upc ? `UPC ${item.display_upc}` : "No barcode"}
                    {" · "}
                    {item.detected_date
                      ? `read ${formatDate(item.detected_date)}`
                      : "no date read yet"}
                    {" · "}
                    scanned {relativeTo(item.created_at)}
                  </div>
                </div>
                <button onClick={() => setScan(item)} className={secondary}>
                  Confirm
                </button>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}

/* ---------------- Step 1: the UPC scanner ---------------- */

function ScanPanel({ shelves, onError, onScanned }) {
  const [upc, setUpc] = useState("");
  const [lookup, setLookup] = useState(null);
  const [shelfId, setShelfId] = useState("");
  const [quantity, setQuantity] = useState(1);
  const [busy, setBusy] = useState(false);
  const [cameraOpen, setCameraOpen] = useState(false);
  const upcRef = useRef(null);

  // Keep the cursor in the barcode field so a wedge scanner's keystrokes
  // land somewhere useful without anyone having to click first. Skipped
  // while the camera is open, where stealing focus would scroll the
  // preview off screen on a phone.
  useEffect(() => {
    if (!cameraOpen) upcRef.current?.focus();
  }, [lookup, cameraOpen]);

  const runLookup = useCallback(
    async (code) => {
      onError("");
      setBusy(true);
      try {
        setLookup(await api.lookupUpc(code));
      } catch (err) {
        // A bad check digit lands here: the read itself failed, so clear
        // the field rather than leaving digits the person has to delete.
        setLookup(null);
        setUpc("");
        onError(err.message);
      } finally {
        setBusy(false);
      }
    },
    [onError],
  );

  // The camera hands over digits and gets out of the way. It closes on a
  // hit so the preview isn't still running while someone reads the result,
  // and the code lands in the same field a typed one would, so there is
  // only ever one path from "a number" to "a product".
  const handleCameraHit = useCallback(
    (code) => {
      setCameraOpen(false);
      setUpc(code);
      runLookup(code);
    },
    [runLookup],
  );

  function handleLookup(event) {
    event.preventDefault();
    runLookup(upc.trim());
  }

  async function open({ withUpc }) {
    onError("");
    setBusy(true);
    try {
      const scan = await api.openScan({
        upc: withUpc ? lookup?.upc || upc.trim() : null,
        shelf_id: shelfId || null,
        quantity: Number(quantity) || 1,
      });
      setUpc("");
      setLookup(null);
      onScanned(scan);
    } catch (err) {
      onError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="1 · Scan the barcode">
      <form onSubmit={handleLookup} className="flex flex-wrap items-end gap-2">
        <div>
          <label htmlFor="intake-upc" className="block text-xs font-medium text-gray-600">
            UPC / EAN
          </label>
          <input
            id="intake-upc"
            ref={upcRef}
            value={upc}
            onChange={(e) => setUpc(e.target.value)}
            inputMode="numeric"
            autoComplete="off"
            placeholder="Scan or type"
            className={`${input} mt-1 w-56 font-mono`}
          />
        </div>
        <div>
          <label htmlFor="intake-shelf" className="block text-xs font-medium text-gray-600">
            Shelf
          </label>
          <select
            id="intake-shelf"
            value={shelfId}
            onChange={(e) => setShelfId(e.target.value)}
            className={`${input} mt-1`}
          >
            <option value="">Unassigned</option>
            {shelves.map((shelf) => (
              <option key={shelf.id} value={shelf.id}>
                {shelf.name}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor="intake-qty" className="block text-xs font-medium text-gray-600">
            Units
          </label>
          <input
            id="intake-qty"
            type="number"
            min="1"
            max="999"
            value={quantity}
            onChange={(e) => setQuantity(e.target.value)}
            className={`${input} mt-1 w-20`}
          />
        </div>
        <button disabled={busy || !upc.trim()} className={primary}>
          Look up
        </button>
        <button
          type="button"
          onClick={() => setCameraOpen((open) => !open)}
          disabled={busy}
          aria-expanded={cameraOpen}
          className={secondary}
        >
          {cameraOpen ? "Close camera" : "Scan with camera"}
        </button>
        {/* A crushed or missing barcode is an ordinary event, and refusing
            the unit over it would mean throwing away food over a printing
            defect. */}
        <button
          type="button"
          onClick={() => open({ withUpc: false })}
          disabled={busy}
          className={secondary}
        >
          No barcode
        </button>
      </form>

      {/* Unmounting rather than hiding: a hidden <video> keeps the camera
          light on, which is alarming in a way no amount of copy fixes. */}
      {cameraOpen && (
        <BarcodeScanner onDetected={handleCameraHit} onClose={() => setCameraOpen(false)} />
      )}

      {lookup && (
        <div className="mt-4 rounded-lg border border-gray-200 bg-gray-50 p-3">
          {lookup.product ? (
            <>
              <div className="font-medium">{lookup.product.name}</div>
              <div className="mt-0.5 text-xs text-gray-500">
                {lookup.product.brand ? `${lookup.product.brand} · ` : ""}
                {lookup.product.category || "Uncategorized"} · UPC {lookup.display_upc}
                {lookup.suggested_shelf_life_days
                  ? ` · typical shelf life ${lookup.suggested_shelf_life_days}d`
                  : ""}
              </div>
              {lookup.donation_restricted && (
                <p className="mt-2 text-xs font-medium text-amber-800">
                  ⚠ This category is never published automatically — a person
                  has to approve it before any pantry sees it.
                </p>
              )}
            </>
          ) : (
            <p className="text-sm text-gray-600">
              <span className="font-medium">UPC {lookup.display_upc}</span> scanned
              cleanly, but nothing in the catalog matches it yet. Carry on — you'll
              name it on the next screen, and it'll be recognized next time.
            </p>
          )}
          <button
            onClick={() => open({ withUpc: true })}
            disabled={busy}
            className={`${primary} mt-3`}
          >
            Start intake
          </button>
        </div>
      )}
    </Card>
  );
}

/* ---------------- Steps 2 and 3: the date, then the person ---------------- */

function ConfirmPanel({ scan, shelves, onError, onScanUpdated, onCancel, onResolved }) {
  const [name, setName] = useState(scan.product_name || "");
  const [category, setCategory] = useState(scan.product_category || "");
  const [shelfId, setShelfId] = useState(scan.shelf_id || "");
  const [quantity, setQuantity] = useState(scan.quantity || 1);
  const [date, setDate] = useState(toDateInput(scan.detected_date));
  const [labelType, setLabelType] = useState(
    scan.detected_label_type && scan.detected_label_type !== "unknown"
      ? scan.detected_label_type
      : "sell_by",
  );
  const [busy, setBusy] = useState(false);
  const [reading, setReading] = useState(false);

  const proposed = toDateInput(scan.detected_date);
  // Recorded rather than guessed at confirm time: the gap between what OCR
  // proposed and what the person submitted is this pipeline's real error
  // rate, and it is only measurable if it's captured here (FR-11.5).
  const acceptedOcrDate = Boolean(proposed) && date === proposed;

  async function readLabel(file) {
    if (!file) return;
    onError("");
    setReading(true);
    try {
      const updated = await api.submitScanImage(scan.id, file);
      onScanUpdated(updated);
      if (updated.detected_date) {
        setDate(toDateInput(updated.detected_date));
        if (updated.detected_label_type && updated.detected_label_type !== "unknown") {
          setLabelType(updated.detected_label_type);
        }
      } else {
        onError(
          "No date could be read from that photo. Try another angle, or type the date in below.",
        );
      }
    } catch (err) {
      onError(err.message);
    } finally {
      setReading(false);
    }
  }

  async function confirm(event) {
    event.preventDefault();
    onError("");
    setBusy(true);
    try {
      await api.confirmScan(scan.id, {
        confirmed_date: new Date(`${date}T00:00:00Z`).toISOString(),
        date_label_type: labelType,
        name: name.trim() || null,
        category: category.trim() || null,
        shelf_id: shelfId || null,
        quantity: Number(quantity) || 1,
        accepted_ocr_date: acceptedOcrDate,
      });
      onResolved();
    } catch (err) {
      onError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function reject() {
    onError("");
    setBusy(true);
    try {
      await api.rejectScan(scan.id);
      onResolved();
    } catch (err) {
      onError(err.message);
    } finally {
      setBusy(false);
    }
  }

  const lowConfidence = scan.date_confidence != null && scan.date_confidence < 0.95;
  const candidates = scan.date_candidates || [];

  return (
    <div className="space-y-6">
      <Card title="2 · Read the date">
        <div className="flex flex-wrap items-center gap-3">
          <label className={`${secondary} cursor-pointer`}>
            {reading ? "Reading…" : "Photograph the label"}
            <input
              type="file"
              accept="image/*"
              // On a phone this opens the camera directly; on a desktop it
              // falls back to the file picker, which is what a store laptop
              // with a USB document camera needs.
              capture="environment"
              className="sr-only"
              disabled={reading}
              onChange={(e) => readLabel(e.target.files?.[0])}
            />
          </label>
          <span className="text-xs text-gray-500">
            Or skip this and type the date — OCR being unavailable never stops intake.
          </span>
        </div>

        {scan.ocr_raw_text && (
          <dl className="mt-4 space-y-1 border-t border-gray-100 pt-3 text-sm">
            <div className="flex gap-2">
              <dt className="text-gray-500">Date read</dt>
              <dd className={lowConfidence ? "font-medium text-amber-700" : ""}>
                {scan.detected_date ? formatDate(scan.detected_date) : "none found"}
                {scan.date_confidence != null &&
                  ` — ${(scan.date_confidence * 100).toFixed(0)}% confident`}
                {lowConfidence && " (below the 95% threshold — check it against the package)"}
              </dd>
            </div>
            <div className="flex gap-2">
              <dt className="shrink-0 text-gray-500">Label text</dt>
              <dd className="min-w-0 break-words font-mono text-xs text-gray-600">
                {scan.ocr_raw_text}
              </dd>
            </div>
          </dl>
        )}

        {candidates.length > 1 && (
          <div className="mt-3 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-900">
            <p className="font-medium">This label has more than one date on it.</p>
            <p className="mt-0.5 text-xs">
              Pick the one that belongs to this product — a packed-on date or a
              promotion end looks identical to a machine.
            </p>
            <div className="mt-2 flex flex-wrap gap-2">
              {candidates.map((candidate, index) => (
                <button
                  key={`${candidate.date}-${index}`}
                  type="button"
                  onClick={() => {
                    setDate(toDateInput(candidate.date));
                    if (candidate.label_type && candidate.label_type !== "unknown") {
                      setLabelType(candidate.label_type);
                    }
                  }}
                  className="rounded border border-amber-300 bg-white px-2 py-1 font-mono text-xs hover:bg-amber-100"
                >
                  {candidate.text} → {formatDate(candidate.date)}
                </button>
              ))}
            </div>
          </div>
        )}
      </Card>

      <Card title="3 · Confirm">
        <form onSubmit={confirm} className="space-y-4">
          <div className="flex flex-wrap items-end gap-3">
            <div className="min-w-48 flex-1">
              <label htmlFor="c-name" className="block text-xs font-medium text-gray-600">
                Item name
              </label>
              <input
                id="c-name"
                required
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder={scan.display_upc ? `UPC ${scan.display_upc}` : "What is it?"}
                className={`${input} mt-1 w-full`}
              />
            </div>
            <div>
              <label htmlFor="c-category" className="block text-xs font-medium text-gray-600">
                Category
              </label>
              <input
                id="c-category"
                value={category}
                onChange={(e) => setCategory(e.target.value)}
                placeholder="Dairy, Bakery…"
                className={`${input} mt-1`}
              />
            </div>
            <div>
              <label htmlFor="c-shelf" className="block text-xs font-medium text-gray-600">
                Shelf
              </label>
              <select
                id="c-shelf"
                value={shelfId}
                onChange={(e) => setShelfId(e.target.value)}
                className={`${input} mt-1`}
              >
                <option value="">Unassigned</option>
                {shelves.map((shelf) => (
                  <option key={shelf.id} value={shelf.id}>
                    {shelf.name}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label htmlFor="c-qty" className="block text-xs font-medium text-gray-600">
                Units
              </label>
              <input
                id="c-qty"
                type="number"
                min="1"
                max="999"
                value={quantity}
                onChange={(e) => setQuantity(e.target.value)}
                className={`${input} mt-1 w-20`}
              />
            </div>
          </div>

          <div className="flex flex-wrap items-end gap-3 border-t border-gray-100 pt-4">
            <div>
              <label htmlFor="c-date" className="block text-xs font-medium text-gray-600">
                Date on the package
              </label>
              <input
                id="c-date"
                type="date"
                required
                value={date}
                onChange={(e) => setDate(e.target.value)}
                className={`${input} mt-1`}
              />
            </div>
            <div>
              <label htmlFor="c-labeltype" className="block text-xs font-medium text-gray-600">
                What kind of date
              </label>
              <select
                id="c-labeltype"
                value={labelType}
                onChange={(e) => setLabelType(e.target.value)}
                className={`${input} mt-1`}
              >
                {LABEL_TYPES.map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </div>
            {proposed && (
              <p className="pb-1.5 text-xs text-gray-500">
                {acceptedOcrDate
                  ? "Matches what the scanner read."
                  : `Scanner read ${formatDate(scan.detected_date)} — your correction will be recorded.`}
              </p>
            )}
          </div>

          {labelType === "use_by" && (
            <p className="rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-900">
              A use-by date is a safety limit, not a sell-by date. Nothing will be
              offered to a pantry past it, whatever the category rule says.
            </p>
          )}

          <Projection
            scan={scan}
            // The projection is computed server-side from the scanned date
            // and category. Once either is edited here it no longer
            // describes what will happen, so it is withdrawn rather than
            // left on screen quietly wrong.
            stale={date !== toDateInput(scan.detected_date) || category !== (scan.product_category || "")}
          />

          <div className="flex flex-wrap gap-3 border-t border-gray-100 pt-4">
            <button disabled={busy || !date || !name.trim()} className={primary}>
              Confirm and add to inventory
            </button>
            {/* No confirmation gate and no required reason: a food-safety
                judgment must never wait on a form (NFR-4.8.4). */}
            <button type="button" onClick={reject} disabled={busy} className={secondary}>
              Turn away
            </button>
            <button type="button" onClick={onCancel} disabled={busy} className={secondary}>
              Back
            </button>
          </div>
        </form>
      </Card>
    </div>
  );
}

/**
 * What the expiration rules will do to this item, shown before the person
 * commits rather than after. Deadlines discovered afterwards are deadlines
 * nobody checked.
 */
function Projection({ scan, stale }) {
  if (!scan.projected_discard_after && !scan.projected_donate_after) return null;

  if (stale) {
    return (
      <p className="rounded-lg bg-gray-50 px-3 py-2 text-xs text-gray-600">
        You've changed the date or category, so the deadlines below no longer
        apply. They'll be recalculated from this category's expiration rule when
        you confirm, and shown on the item.
      </p>
    );
  }

  return (
    <div className="rounded-lg bg-gray-50 px-3 py-2 text-xs text-gray-700">
      <p className="font-medium text-gray-800">Once confirmed, this item will:</p>
      <ul className="mt-1 space-y-0.5">
        {scan.projected_donate_after && scan.projected_auto_publish && (
          <li>
            · go to the donation network on{" "}
            <span className="font-medium">{formatDateTime(scan.projected_donate_after)}</span>
          </li>
        )}
        {!scan.projected_auto_publish && (
          <li>
            · <span className="font-medium">wait for a person to publish it</span> — this
            category is never offered automatically
          </li>
        )}
        {scan.projected_discard_after && (
          <li>
            · come off the shelf on{" "}
            <span className="font-medium">{formatDateTime(scan.projected_discard_after)}</span>
          </li>
        )}
      </ul>
      <p className="mt-1 text-gray-500">
        From this category's expiration rule. Editing the date or category above
        changes these.
      </p>
    </div>
  );
}

/* ---------------- date helpers ---------------- */

function formatDate(value) {
  const d = parseUtc(value);
  return d ? d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }) : "—";
}

function formatDateTime(value) {
  const d = parseUtc(value);
  return d
    ? d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })
    : "—";
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
