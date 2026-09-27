/**
 * Money helpers for the two supported currencies.
 *
 * INR uses the Indian grouping (last three digits, then pairs): 12,34,567.89
 * and reads as lakh / crore; USD uses western thousands grouping. Amounts
 * travel as decimal strings to keep the backend's Decimal precision.
 */
export type Currency = "USD" | "INR";

export const CURRENCIES: readonly Currency[] = ["USD", "INR"];

export const CURRENCY_SYMBOL: Record<Currency, string> = { USD: "$", INR: "₹" };

/** Groups an integer digit string per currency ("1234567" -> "12,34,567" for INR). */
export function groupDigits(digits: string, currency: Currency): string {
  const clean = digits.replace(/^0+(?=\d)/, "");
  if (currency !== "INR") return clean.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  if (clean.length <= 3) return clean;
  const last3 = clean.slice(-3);
  const rest = clean.slice(0, -3).replace(/\B(?=(\d{2})+(?!\d))/g, ",");
  return `${rest},${last3}`;
}

/**
 * Formats an amount (number or decimal string) with the currency's grouping.
 * `fractionDigits` defaults to 2; pass 0 to drop the decimals.
 */
export function formatMoney(
  amount: number | string | null | undefined,
  currency: Currency,
  options: { fractionDigits?: number; symbol?: boolean } = {},
): string {
  if (amount === null || amount === undefined || amount === "") return "—";
  const { fractionDigits = 2, symbol = true } = options;
  const numeric = typeof amount === "number" ? amount : Number(amount);
  if (!Number.isFinite(numeric)) return "—";
  const negative = numeric < 0;
  const fixed = Math.abs(numeric).toFixed(fractionDigits);
  const [intPart, fracPart] = fixed.split(".");
  const grouped = groupDigits(intPart, currency);
  const body = fracPart ? `${grouped}.${fracPart}` : grouped;
  const prefix = symbol ? CURRENCY_SYMBOL[currency] : "";
  return `${negative ? "-" : ""}${prefix}${body}${symbol ? "" : ` ${currency}`}`;
}

/**
 * Short, human-readable magnitude: INR in lakh / crore ("₹1.50 Cr", "₹12.00 L"),
 * USD in K / M / B ("$2.50M").
 */
export function formatMoneyCompact(amount: number | string | null | undefined, currency: Currency): string {
  if (amount === null || amount === undefined || amount === "") return "—";
  const numeric = typeof amount === "number" ? amount : Number(amount);
  if (!Number.isFinite(numeric)) return "—";
  const abs = Math.abs(numeric);
  const sign = numeric < 0 ? "-" : "";
  const sym = CURRENCY_SYMBOL[currency];
  if (currency === "INR") {
    if (abs >= 1e7) return `${sign}${sym}${(abs / 1e7).toFixed(2)} Cr`;
    if (abs >= 1e5) return `${sign}${sym}${(abs / 1e5).toFixed(2)} L`;
    return `${sign}${sym}${groupDigits(String(Math.round(abs)), "INR")}`;
  }
  if (abs >= 1e9) return `${sign}${sym}${(abs / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `${sign}${sym}${(abs / 1e6).toFixed(2)}M`;
  if (abs >= 1e3) return `${sign}${sym}${(abs / 1e3).toFixed(1)}K`;
  return `${sign}${sym}${groupDigits(String(Math.round(abs)), "USD")}`;
}

/** Parses user input ("12,34,567.50", "$1,000", "₹ 5 lakh") into a decimal string or null. */
export function parseMoney(input: string | number | null | undefined): string | null {
  if (input === null || input === undefined) return null;
  if (typeof input === "number") return Number.isFinite(input) ? String(input) : null;
  const text = input.trim().toLowerCase();
  if (!text) return null;
  let multiplier = 1;
  const unit = (pattern: string) => new RegExp(`(?<![a-z])(${pattern})(?![a-z])`).test(text);
  if (unit("cr|crore|crores")) multiplier = 1e7;
  else if (unit("l|lakh|lakhs|lac|lacs")) multiplier = 1e5;
  else if (unit("k")) multiplier = 1e3;
  else if (unit("m|mn|million")) multiplier = 1e6;
  const digits = text.replace(/[^0-9.-]/g, "");
  if (!/\d/.test(digits)) return null;
  const numeric = Number(digits);
  if (!Number.isFinite(numeric)) return null;
  const value = numeric * multiplier;
  return Number.isInteger(value) ? String(value) : value.toFixed(2);
}
