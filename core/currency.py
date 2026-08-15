from __future__ import annotations

LEGACY_QUOTE_CURRENCIES = {
    "10AM": "EUR",
    "EXSA": "EUR",
    "LCUJ": "EUR",
    "PPFB": "EUR",
    "PPFD": "EUR",
    "SQ": "USD",
    "SXR8": "EUR",
}


def infer_quote_currency(symbol: str, configured: str | None = None) -> str | None:
    if configured:
        return configured.strip()
    normalized = symbol.upper()
    if normalized in LEGACY_QUOTE_CURRENCIES:
        return LEGACY_QUOTE_CURRENCIES[normalized]
    if normalized.endswith((".DE", ".AMS", ".MI")):
        return "EUR"
    if normalized.endswith("USDT"):
        return "USD"
    if ".LON" in normalized or normalized.endswith(".L"):
        return None
    return "USD"
