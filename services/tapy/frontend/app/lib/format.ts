export function formatUsd(
  value: number,
  opts?: { decimals?: boolean; locale?: string },
) {
  return new Intl.NumberFormat(opts?.locale ?? "en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: opts?.decimals ? 2 : 0,
    minimumFractionDigits: opts?.decimals ? 2 : 0,
  }).format(value);
}

export function formatDate(iso: string, locale = "en-US") {
  return new Intl.DateTimeFormat(locale, {
    month: "short",
    day: "numeric",
    year: "numeric",
  }).format(new Date(iso + "T00:00:00"));
}

export function formatDateShort(iso: string, locale = "en-US") {
  return new Intl.DateTimeFormat(locale, {
    month: "short",
    day: "numeric",
  }).format(new Date(iso + "T00:00:00"));
}

export function formatDateRange(startIso: string, endIso: string, locale = "en-US") {
  const startDate = new Date(startIso + "T00:00:00");
  const endDate = new Date(endIso + "T00:00:00");
  const sameYear = startDate.getFullYear() === endDate.getFullYear();
  const short = new Intl.DateTimeFormat(locale, { month: "short", day: "numeric" });
  const medium = new Intl.DateTimeFormat(locale, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
  if (sameYear) {
    return `${short.format(startDate)} – ${medium.format(endDate)}`;
  }
  return `${medium.format(startDate)} – ${medium.format(endDate)}`;
}

export function nightsBetween(startIso: string, endIso: string) {
  const start = new Date(startIso + "T00:00:00").getTime();
  const end = new Date(endIso + "T00:00:00").getTime();
  return Math.max(1, Math.round((end - start) / (1000 * 60 * 60 * 24)));
}

export function formatPercent(value: number, digits = 0, locale = "en-US") {
  return new Intl.NumberFormat(locale, {
    style: "percent",
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(value);
}

export function formatNumber(value: number, locale = "en-US") {
  return new Intl.NumberFormat(locale).format(Math.round(value));
}
