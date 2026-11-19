// Small API client and formatting helpers shared by every page.

export async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "content-type": "application/json", ...(options.headers || {}) },
    credentials: "same-origin",
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

let platformPromise = null;
export function getPlatform() {
  if (!platformPromise) platformPromise = api("/api/platform");
  return platformPromise;
}

let businessPromise = null;
export function getBusiness() {
  if (!businessPromise) businessPromise = api("/api/business");
  return businessPromise;
}

export function money(value, currency = "USD") {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency,
    maximumFractionDigits: 0,
  }).format(Number(value) || 0);
}

export function number(value) {
  return new Intl.NumberFormat("en-US").format(Number(value) || 0);
}

export function percent(value, digits = 0) {
  return `${(Number(value) * 100).toFixed(digits)}%`;
}

export function formatDate(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

export function formatWhen(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString("en-GB", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function ratingText(value) {
  return value == null ? "—" : Math.round(value).toLocaleString("en-GB");
}

export function bb(value) {
  if (value == null || !Number.isFinite(Number(value))) return "—";
  const rounded = Number(value).toFixed(1);
  return Number(rounded) > 0 ? `+${rounded}` : rounded;
}

export function divisionVariant(division) {
  return division === "elite" ? "purple" : division === "raised" ? "blue" : "neutral";
}
