/**
 * Pull a product number (GTIN) out of whatever a scanner decoded.
 *
 * Why this exists: the intake desk now accepts QR codes as well as retail
 * barcodes, and a package QR rarely holds just the number. It is usually a
 * GS1 Digital Link ("https://id.gs1.org/01/<gtin>/10/<lot>") or a GS1 element
 * string ("(01)<gtin>(17)<date>..."). The server strips every non-digit from
 * a UPC, so handing it that text unprocessed would glue the lot and date onto
 * the number and fail the check digit — or, worse, pass it on a different
 * product. The GTIN has to be found here, by its Application Identifier.
 *
 * Returns digits, or "" when the code holds no product number (a URL to a
 * recipe, a pickup token). Empty means "keep looking", never "guess": the
 * server still validates the check digit on whatever comes out.
 */
export function extractGtin(raw) {
  const text = String(raw || "").trim();

  // A plain retail barcode: 8, 12, 13 or 14 digits, maybe with spaces or
  // hyphens from a person retyping it. Longer digit runs are element
  // strings and fall through to the Application Identifier match.
  if (/^[\d\s-]+$/.test(text)) {
    const digits = text.replace(/\D/g, "");
    if (digits.length <= 14) return digits;
  }

  // GS1 Digital Link: the GTIN follows a "/01/" path segment.
  const link = text.match(/\/01\/(\d{14})(?!\d)/);
  if (link) return link[1];

  // GS1 element string, with or without the printed parentheses.
  const element = text.match(/^\(?01\)?(\d{14})/);
  if (element) return element[1];

  return "";
}
