import { describe, expect, it } from "vitest";

import { formatMoney, formatMoneyCompact, groupDigits, parseMoney } from "./money";

describe("money", () => {
  it("groups INR digits in lakh/crore style", () => {
    expect(groupDigits("1234567", "INR")).toBe("12,34,567");
    expect(groupDigits("123456789", "INR")).toBe("12,34,56,789");
    expect(groupDigits("1000", "INR")).toBe("1,000");
    expect(groupDigits("999", "INR")).toBe("999");
    expect(groupDigits("100000", "INR")).toBe("1,00,000");
  });

  it("groups USD digits in thousands", () => {
    expect(groupDigits("1234567", "USD")).toBe("1,234,567");
    expect(groupDigits("999", "USD")).toBe("999");
  });

  it("formats money with symbol and two decimals", () => {
    expect(formatMoney("1234567.5", "INR")).toBe("₹12,34,567.50");
    expect(formatMoney(1234567.5, "USD")).toBe("$1,234,567.50");
    expect(formatMoney("2500000", "INR", { fractionDigits: 0 })).toBe("₹25,00,000");
    expect(formatMoney(-1500, "USD")).toBe("-$1,500.00");
    expect(formatMoney("1500", "USD", { symbol: false })).toBe("1,500.00 USD");
    expect(formatMoney(null, "USD")).toBe("—");
    expect(formatMoney("abc", "USD")).toBe("—");
  });

  it("formats compact INR in lakh and crore, USD in K/M/B", () => {
    expect(formatMoneyCompact(15000000, "INR")).toBe("₹1.50 Cr");
    expect(formatMoneyCompact("1200000", "INR")).toBe("₹12.00 L");
    expect(formatMoneyCompact(75000, "INR")).toBe("₹75,000");
    expect(formatMoneyCompact(2500000, "USD")).toBe("$2.50M");
    expect(formatMoneyCompact(3400, "USD")).toBe("$3.4K");
    expect(formatMoneyCompact(1.2e9, "USD")).toBe("$1.20B");
  });

  it("parses typed amounts including Indian magnitudes", () => {
    expect(parseMoney("12,34,567.50")).toBe("1234567.50");
    expect(parseMoney("$1,000")).toBe("1000");
    expect(parseMoney("5 lakh")).toBe("500000");
    expect(parseMoney("1.5 cr")).toBe("15000000");
    expect(parseMoney("2m")).toBe("2000000");
    expect(parseMoney("")).toBeNull();
    expect(parseMoney("n/a")).toBeNull();
    expect(parseMoney(42)).toBe("42");
  });
});
