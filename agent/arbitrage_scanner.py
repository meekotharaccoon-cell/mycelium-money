#!/usr/bin/env python3
"""
arbitrage_scanner.py -- Cross-market price difference scanner
==============================================================
Scans for arbitrage opportunities across:
  - Stablecoin peg deviations (USDC/USDT/DAI via CoinGecko)
  - Crypto price spreads across exchanges (CoinGecko multi-exchange)
  - Kalshi market mispricings (YES + NO < $1.00 or > $1.00)

Uses ONLY free/public APIs. No keys needed.
Outputs JSON report to stdout + data/arbitrage_report.json.

Called by: GitHub Actions daily scan, nerve-center, or standalone.
"""

import json
import sys
import time
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

KALSHI_API = "https://api.elections.kalshi.com/trade-api/v2"
COINGECKO_API = "https://api.coingecko.com/api/v3"

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

REPORT_FILE = DATA_DIR / "arbitrage_report.json"

# Major coins to check cross-exchange spreads
SPREAD_COINS = ["bitcoin", "ethereum", "solana", "cardano", "polkadot"]

# Stablecoins to monitor for peg deviations
STABLECOINS = {
    "tether": "USDT",
    "usd-coin": "USDC",
    "dai": "DAI",
    "true-usd": "TUSD",
    "frax": "FRAX",
}

# Kalshi series to check for mispricing
KALSHI_SERIES = [
    "KXINX", "KXBTC", "KXETH", "KXGOLD", "KXOIL",
    "KXNASDAQ100", "KXSOL", "KXSILVER",
]

# Thresholds
STABLECOIN_PEG_THRESHOLD = 0.002   # 0.2% deviation from $1.00
CRYPTO_SPREAD_THRESHOLD = 0.5      # 0.5% spread between exchanges
KALSHI_MISPRICING_THRESHOLD = 2    # 2 cents mispricing (YES+NO != 100)


# ---------------------------------------------------------------------------
# HTTP HELPERS
# ---------------------------------------------------------------------------

def _get_json(url, timeout=15):
    """Fetch JSON from URL. Returns dict or None."""
    try:
        req = urllib.request.Request(url)
        req.add_header("Accept", "application/json")
        req.add_header("User-Agent", "mycelium-money/1.0")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        body = ""
        if e.fp:
            try:
                body = e.read().decode("utf-8", errors="replace")[:200]
            except Exception:
                pass
        print(
            f"[arb_scanner] HTTP {e.code}: {url} -- {body}",
            file=sys.stderr,
        )
        return None
    except Exception as e:
        print(f"[arb_scanner] Error: {url} -- {e}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# SCANNER 1: STABLECOIN PEG DEVIATIONS
# ---------------------------------------------------------------------------

def scan_stablecoin_pegs():
    """
    Check stablecoin prices for deviations from $1.00.
    Any deviation > threshold is a potential arbitrage signal.
    Returns list of deviation dicts.
    """
    ids = ",".join(STABLECOINS.keys())
    url = (
        f"{COINGECKO_API}/simple/price"
        f"?ids={ids}"
        f"&vs_currencies=usd"
        f"&include_24hr_change=true"
        f"&include_24hr_vol=true"
    )
    data = _get_json(url)
    if not data:
        return []

    deviations = []
    for coin_id, symbol in STABLECOINS.items():
        pdata = data.get(coin_id, {})
        price = pdata.get("usd", 1.0)
        change_24h = pdata.get("usd_24h_change", 0)
        volume_24h = pdata.get("usd_24h_vol", 0)

        deviation = abs(price - 1.0)
        deviation_pct = deviation * 100

        entry = {
            "symbol": symbol,
            "coin_id": coin_id,
            "price": price,
            "deviation_from_peg": round(deviation, 6),
            "deviation_pct": round(deviation_pct, 4),
            "direction": "above" if price > 1.0 else "below",
            "change_24h": change_24h,
            "volume_24h": volume_24h,
            "is_opportunity": deviation >= STABLECOIN_PEG_THRESHOLD,
        }

        if deviation >= STABLECOIN_PEG_THRESHOLD:
            if price < 1.0:
                entry["action"] = f"Buy {symbol} at ${price:.4f}, redeem at $1.00"
                entry["profit_per_unit"] = round(1.0 - price, 6)
            else:
                entry["action"] = f"Sell {symbol} at ${price:.4f}, rebuy at $1.00"
                entry["profit_per_unit"] = round(price - 1.0, 6)

        deviations.append(entry)

    # Also check cross-stablecoin spread (USDC vs USDT)
    usdc_price = data.get("usd-coin", {}).get("usd", 1.0)
    usdt_price = data.get("tether", {}).get("usd", 1.0)
    spread = abs(usdc_price - usdt_price)
    if spread > 0.001:
        cheaper = "USDC" if usdc_price < usdt_price else "USDT"
        pricier = "USDT" if usdc_price < usdt_price else "USDC"
        deviations.append({
            "symbol": f"{cheaper}/{pricier}",
            "coin_id": "cross-stable",
            "price": None,
            "deviation_from_peg": round(spread, 6),
            "deviation_pct": round(spread * 100, 4),
            "direction": "spread",
            "change_24h": 0,
            "volume_24h": 0,
            "is_opportunity": spread >= STABLECOIN_PEG_THRESHOLD,
            "action": (
                f"Buy {cheaper} -> swap to {pricier} "
                f"(spread: ${spread:.4f})"
            ),
            "profit_per_unit": round(spread, 6),
        })

    return deviations


# ---------------------------------------------------------------------------
# SCANNER 2: CROSS-EXCHANGE CRYPTO SPREADS
# ---------------------------------------------------------------------------

def scan_crypto_exchange_spreads(coin_ids=None):
    """
    Use CoinGecko tickers endpoint to find price differences
    for the same coin across different exchanges.
    Returns list of spread opportunity dicts.
    """
    coin_ids = coin_ids or SPREAD_COINS
    opportunities = []

    for coin_id in coin_ids:
        url = (
            f"{COINGECKO_API}/coins/{coin_id}/tickers"
            f"?include_exchange_logo=false"
            f"&depth=true"
        )
        data = _get_json(url)
        if not data or "tickers" not in data:
            time.sleep(0.5)
            continue

        # Filter to USD/USDT pairs only, with valid last price
        usd_tickers = []
        for t in data["tickers"]:
            target = t.get("target", "").upper()
            if target not in ("USD", "USDT", "USDC", "BUSD"):
                continue
            last = t.get("last", 0)
            if not last or last <= 0:
                continue
            volume_usd = t.get("converted_volume", {}).get("usd", 0) or 0
            # Skip very low volume exchanges (unreliable prices)
            if volume_usd < 10000:
                continue
            usd_tickers.append({
                "exchange": t.get("market", {}).get("name", "Unknown"),
                "exchange_id": t.get("market", {}).get("identifier", ""),
                "pair": f"{t.get('base', '?')}/{target}",
                "last": last,
                "bid_ask_spread_pct": t.get("bid_ask_spread_percentage", 0),
                "volume_usd": volume_usd,
                "trust_score": t.get("trust_score", ""),
            })

        if len(usd_tickers) < 2:
            time.sleep(0.5)
            continue

        # Find min ask (cheapest) and max bid (highest)
        usd_tickers.sort(key=lambda x: x["last"])
        cheapest = usd_tickers[0]
        most_expensive = usd_tickers[-1]

        if cheapest["exchange_id"] == most_expensive["exchange_id"]:
            time.sleep(0.5)
            continue

        spread_pct = (
            (most_expensive["last"] - cheapest["last"])
            / cheapest["last"]
            * 100
        )

        if spread_pct >= CRYPTO_SPREAD_THRESHOLD:
            opportunities.append({
                "coin": coin_id,
                "buy_exchange": cheapest["exchange"],
                "buy_price": cheapest["last"],
                "buy_pair": cheapest["pair"],
                "buy_volume_usd": cheapest["volume_usd"],
                "sell_exchange": most_expensive["exchange"],
                "sell_price": most_expensive["last"],
                "sell_pair": most_expensive["pair"],
                "sell_volume_usd": most_expensive["volume_usd"],
                "spread_pct": round(spread_pct, 3),
                "spread_usd": round(
                    most_expensive["last"] - cheapest["last"], 4
                ),
                "exchanges_checked": len(usd_tickers),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

        time.sleep(0.5)  # CoinGecko rate limit

    opportunities.sort(key=lambda x: -x["spread_pct"])
    return opportunities


# ---------------------------------------------------------------------------
# SCANNER 3: KALSHI MARKET MISPRICINGS
# ---------------------------------------------------------------------------

def scan_kalshi_mispricings(series_list=None):
    """
    Check Kalshi markets where YES_ASK + NO_ASK != 100 cents.
    If the sum < 100, there is a guaranteed profit buying both sides.
    If the sum > 100, there may be selling opportunities.
    Returns list of mispricing dicts.
    """
    series_list = series_list or KALSHI_SERIES
    mispricings = []

    for series_ticker in series_list:
        params = urllib.parse.urlencode({
            "series_ticker": series_ticker,
            "status": "open",
            "limit": 10,
        })
        data = _get_json(f"{KALSHI_API}/markets?{params}")
        if not data or "markets" not in data:
            time.sleep(0.3)
            continue

        for mkt in data["markets"]:
            yes_ask = mkt.get("yes_ask", 0) or 0
            no_ask = mkt.get("no_ask", 0) or 0
            yes_bid = mkt.get("yes_bid", 0) or 0
            no_bid = mkt.get("no_bid", 0) or 0

            # Check if buying both YES and NO is profitable
            # If yes_ask + no_ask < 100, guaranteed profit
            if yes_ask > 0 and no_ask > 0:
                total_cost = yes_ask + no_ask
                if total_cost < (100 - KALSHI_MISPRICING_THRESHOLD):
                    profit_cents = 100 - total_cost
                    mispricings.append({
                        "series": series_ticker,
                        "ticker": mkt.get("ticker", ""),
                        "title": mkt.get("title", ""),
                        "yes_ask": yes_ask,
                        "no_ask": no_ask,
                        "total_cost": total_cost,
                        "guaranteed_profit_cents": profit_cents,
                        "return_pct": round(
                            (profit_cents / total_cost) * 100, 2
                        ),
                        "type": "guaranteed_arb",
                        "action": (
                            f"Buy YES@{yes_ask}c + NO@{no_ask}c = "
                            f"{total_cost}c, collect 100c = "
                            f"{profit_cents}c profit"
                        ),
                        "volume": mkt.get("volume", 0),
                    })

            # Check bid-side: if yes_bid + no_bid > 100, sell both
            if yes_bid > 0 and no_bid > 0:
                total_bid = yes_bid + no_bid
                if total_bid > (100 + KALSHI_MISPRICING_THRESHOLD):
                    profit_cents = total_bid - 100
                    mispricings.append({
                        "series": series_ticker,
                        "ticker": mkt.get("ticker", ""),
                        "title": mkt.get("title", ""),
                        "yes_bid": yes_bid,
                        "no_bid": no_bid,
                        "total_bid": total_bid,
                        "guaranteed_profit_cents": profit_cents,
                        "return_pct": round(
                            (profit_cents / 100) * 100, 2
                        ),
                        "type": "guaranteed_arb_sell",
                        "action": (
                            f"Sell YES@{yes_bid}c + Sell NO@{no_bid}c = "
                            f"collect {total_bid}c, "
                            f"pay 100c = {profit_cents}c profit"
                        ),
                        "volume": mkt.get("volume", 0),
                    })

        time.sleep(0.3)

    mispricings.sort(key=lambda x: -x.get("guaranteed_profit_cents", 0))
    return mispricings


# ---------------------------------------------------------------------------
# FULL SCAN
# ---------------------------------------------------------------------------

def full_scan():
    """
    Run all three scanners and produce a unified report.
    Returns dict with all opportunities.
    """
    now = datetime.now(timezone.utc)

    report = {
        "generated_at": now.isoformat(),
        "status": "ok",
        "scanners": {},
        "summary": {},
    }

    # 1. Stablecoin pegs
    print("[arb_scanner] Scanning stablecoin pegs...", file=sys.stderr)
    pegs = scan_stablecoin_pegs()
    peg_opps = [p for p in pegs if p.get("is_opportunity")]
    report["scanners"]["stablecoin_pegs"] = {
        "all_stablecoins": pegs,
        "opportunities": peg_opps,
        "count": len(peg_opps),
    }

    # 2. Cross-exchange crypto spreads
    print("[arb_scanner] Scanning cross-exchange spreads...", file=sys.stderr)
    spreads = scan_crypto_exchange_spreads()
    report["scanners"]["crypto_spreads"] = {
        "opportunities": spreads,
        "count": len(spreads),
        "coins_checked": len(SPREAD_COINS),
    }

    # 3. Kalshi mispricings
    print("[arb_scanner] Scanning Kalshi mispricings...", file=sys.stderr)
    mispricings = scan_kalshi_mispricings()
    report["scanners"]["kalshi_mispricings"] = {
        "opportunities": mispricings,
        "count": len(mispricings),
        "series_checked": len(KALSHI_SERIES),
    }

    # Summary
    total_opps = len(peg_opps) + len(spreads) + len(mispricings)
    best_opp = None

    if mispricings:
        best_opp = {
            "type": "kalshi_mispricing",
            "detail": mispricings[0],
        }
    elif spreads:
        best_opp = {
            "type": "crypto_spread",
            "detail": spreads[0],
        }
    elif peg_opps:
        best_opp = {
            "type": "stablecoin_peg",
            "detail": peg_opps[0],
        }

    report["summary"] = {
        "total_opportunities": total_opps,
        "stablecoin_peg_opps": len(peg_opps),
        "crypto_spread_opps": len(spreads),
        "kalshi_mispricing_opps": len(mispricings),
        "best_opportunity": best_opp,
        "scan_time": datetime.now(timezone.utc).isoformat(),
    }

    # Save report
    REPORT_FILE.write_text(json.dumps(report, indent=2), encoding="utf-8")

    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    """Run full arbitrage scan and output JSON."""
    print("[arb_scanner] Starting full scan...", file=sys.stderr)
    start = time.time()

    report = full_scan()

    elapsed = time.time() - start
    report["elapsed_seconds"] = round(elapsed, 1)

    # JSON to stdout
    print(json.dumps(report, indent=2))

    # Human summary to stderr
    s = report["summary"]
    print(f"\n{'='*60}", file=sys.stderr)
    print("ARBITRAGE SCANNER -- Results", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)
    print(f"  Total opportunities: {s['total_opportunities']}", file=sys.stderr)
    print(f"    Stablecoin peg: {s['stablecoin_peg_opps']}", file=sys.stderr)
    print(f"    Crypto spread:  {s['crypto_spread_opps']}", file=sys.stderr)
    print(f"    Kalshi mispricing: {s['kalshi_mispricing_opps']}", file=sys.stderr)

    best = s.get("best_opportunity")
    if best:
        print(f"  Best opportunity: {best['type']}", file=sys.stderr)
        detail = best.get("detail", {})
        if "action" in detail:
            print(f"    {detail['action']}", file=sys.stderr)
        elif "spread_pct" in detail:
            print(
                f"    {detail['coin']}: buy on {detail['buy_exchange']} "
                f"@ ${detail['buy_price']:,.2f}, sell on "
                f"{detail['sell_exchange']} @ ${detail['sell_price']:,.2f} "
                f"= {detail['spread_pct']:.2f}%",
                file=sys.stderr,
            )
    else:
        print("  No opportunities above threshold.", file=sys.stderr)

    print(f"  Elapsed: {elapsed:.1f}s", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)

    return report


if __name__ == "__main__":
    main()
