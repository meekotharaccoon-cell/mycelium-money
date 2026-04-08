#!/usr/bin/env python3
"""
money_agent.py -- Autonomous legal money agent
================================================
Scans Kalshi prediction markets, crypto prices, Fear & Greed index.
Calculates compound rates. Outputs JSON revenue reports.
Runs standalone or called by nerve-center.

Uses ONLY free/public APIs:
  - Kalshi public market data (no auth needed for reads)
  - CoinGecko free tier (no key needed)
  - Alternative.me Fear & Greed (no key needed)

Ethics: 99% mutual aid / 1% node fuel
"""

import json
import sys
import time
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

KALSHI_API = "https://api.elections.kalshi.com/trade-api/v2"
COINGECKO_API = "https://api.coingecko.com/api/v3"
FEAR_GREED_API = "https://api.alternative.me/fng"

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

STATE_FILE = DATA_DIR / "money_agent_state.json"
REPORT_FILE = DATA_DIR / "revenue_report.json"

# Kalshi series that resolve fast (daily/weekly) -- best for compounding
FAST_SERIES = [
    "KXINX",        # S&P 500 daily range
    "KXNASDAQ100",  # Nasdaq 100 daily
    "KXBTC",        # Bitcoin price brackets
    "KXETH",        # Ethereum price brackets
    "KXGOLD",       # Gold price daily
    "KXOIL",        # Oil/WTI price
    "KXSILVER",     # Silver price daily
    "KXHIGHNY",     # NYC temperature daily
    "KXAAAGASD",    # Gas prices daily
    "KXSOL",        # Solana brackets
    "KXJOBLESS",    # Jobless claims weekly
]

# Crypto watchlist (CoinGecko IDs -> display symbols)
CRYPTO_WATCHLIST = {
    "bitcoin": "BTC",
    "ethereum": "ETH",
    "solana": "SOL",
    "fetch-ai": "FET",
    "ocean-protocol": "OCEAN",
    "singularitynet": "AGIX",
    "render-token": "RNDR",
    "filecoin": "FIL",
}


# ---------------------------------------------------------------------------
# HTTP HELPERS
# ---------------------------------------------------------------------------

def _get_json(url, timeout=15, headers=None):
    """Fetch JSON from a URL. Returns parsed dict or None on error."""
    try:
        req = urllib.request.Request(url)
        req.add_header("Accept", "application/json")
        req.add_header("User-Agent", "mycelium-money/1.0")
        if headers:
            for k, v in headers.items():
                req.add_header(k, v)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        body = ""
        if e.fp:
            try:
                body = e.read().decode("utf-8", errors="replace")[:200]
            except Exception:
                pass
        print(f"[money_agent] HTTP {e.code} fetching {url}: {body}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"[money_agent] Error fetching {url}: {e}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# KALSHI MARKET SCANNER
# ---------------------------------------------------------------------------

def scan_kalshi_markets(series_list=None, limit_per_series=5):
    """
    Scan Kalshi public markets for fast-resolving opportunities.
    Returns list of market dicts with pricing + settlement info.

    Uses public endpoints -- no auth needed.
    GET /markets?series_ticker=XXX&status=open
    """
    series_list = series_list or FAST_SERIES
    opportunities = []

    for series_ticker in series_list:
        params = urllib.parse.urlencode({
            "series_ticker": series_ticker,
            "status": "open",
            "limit": limit_per_series,
        })
        data = _get_json(f"{KALSHI_API}/markets?{params}")
        if not data or "markets" not in data:
            continue

        for mkt in data["markets"]:
            ticker = mkt.get("ticker", "")
            title = mkt.get("title", "")
            yes_bid = mkt.get("yes_bid", 0) or 0  # cents
            yes_ask = mkt.get("yes_ask", 0) or 0
            no_bid = mkt.get("no_bid", 0) or 0
            no_ask = mkt.get("no_ask", 0) or 0
            volume = mkt.get("volume", 0) or 0
            close_time = mkt.get("close_time", "")

            # Calculate expected value for near-certain YES bets
            # If yes_bid >= 90 cents, buying at ask and collecting $1 is ~3-10% return
            if yes_bid >= 90:
                cost = yes_ask if yes_ask else yes_bid
                if 0 < cost <= 98:
                    expected_return_pct = ((100 - cost) / cost) * 100

                    # Parse close time to get hours until resolution
                    hours_to_close = None
                    if close_time:
                        try:
                            close_dt = datetime.fromisoformat(
                                close_time.replace("Z", "+00:00")
                            )
                            delta = close_dt - datetime.now(timezone.utc)
                            hours_to_close = max(0, delta.total_seconds() / 3600)
                        except Exception:
                            pass

                    opportunities.append({
                        "series": series_ticker,
                        "ticker": ticker,
                        "title": title,
                        "yes_bid": yes_bid,
                        "yes_ask": yes_ask,
                        "no_bid": no_bid,
                        "no_ask": no_ask,
                        "volume": volume,
                        "close_time": close_time,
                        "hours_to_close": (
                            round(hours_to_close, 1) if hours_to_close is not None else None
                        ),
                        "cost_cents": cost,
                        "expected_return_pct": round(expected_return_pct, 2),
                        "strategy": "near_certain_yes",
                    })

            # Also check near-certain NO bets (no_bid >= 90)
            if no_bid >= 90:
                cost = no_ask if no_ask else no_bid
                if 0 < cost <= 98:
                    expected_return_pct = ((100 - cost) / cost) * 100
                    opportunities.append({
                        "series": series_ticker,
                        "ticker": ticker,
                        "title": title + " [NO side]",
                        "yes_bid": yes_bid,
                        "yes_ask": yes_ask,
                        "no_bid": no_bid,
                        "no_ask": no_ask,
                        "volume": volume,
                        "close_time": close_time,
                        "hours_to_close": None,
                        "cost_cents": cost,
                        "expected_return_pct": round(expected_return_pct, 2),
                        "strategy": "near_certain_no",
                    })

        # Rate limit: be respectful to Kalshi public API
        time.sleep(0.3)

    # Sort by expected return (highest first), then soonest close
    opportunities.sort(
        key=lambda x: (
            -x["expected_return_pct"],
            x["hours_to_close"] if x["hours_to_close"] is not None else 9999,
        )
    )

    return opportunities


def scan_kalshi_events(limit=20):
    """
    Scan Kalshi events endpoint for active high-volume events.
    GET /events?status=open&limit=N
    """
    params = urllib.parse.urlencode({
        "status": "open",
        "limit": limit,
        "with_nested_markets": "true",
    })
    data = _get_json(f"{KALSHI_API}/events?{params}")
    if not data or "events" not in data:
        return []

    events = []
    for evt in data["events"]:
        markets = evt.get("markets", [])
        total_volume = sum(m.get("volume", 0) or 0 for m in markets)
        events.append({
            "event_ticker": evt.get("event_ticker", ""),
            "title": evt.get("title", ""),
            "category": evt.get("category", ""),
            "market_count": len(markets),
            "total_volume": total_volume,
            "close_time": evt.get("close_time", ""),
        })

    events.sort(key=lambda x: -x["total_volume"])
    return events


# ---------------------------------------------------------------------------
# CRYPTO PRICES (CoinGecko free tier)
# ---------------------------------------------------------------------------

def fetch_crypto_prices(coin_ids=None):
    """
    Fetch current prices from CoinGecko (free, no key needed).
    Returns dict: { coin_id: { symbol, usd, usd_24h_change, usd_market_cap } }
    """
    coin_ids = coin_ids or list(CRYPTO_WATCHLIST.keys())
    ids_str = ",".join(coin_ids)
    url = (
        f"{COINGECKO_API}/simple/price"
        f"?ids={ids_str}"
        f"&vs_currencies=usd"
        f"&include_24hr_change=true"
        f"&include_market_cap=true"
    )
    data = _get_json(url)
    if not data:
        return {}

    result = {}
    for coin_id, pdata in data.items():
        result[coin_id] = {
            "symbol": CRYPTO_WATCHLIST.get(coin_id, coin_id.upper()),
            "usd": pdata.get("usd", 0),
            "usd_24h_change": pdata.get("usd_24h_change", 0),
            "usd_market_cap": pdata.get("usd_market_cap", 0),
        }

    return result


def fetch_stablecoin_prices():
    """
    Fetch stablecoin prices for peg-deviation detection.
    Returns dict with USDC, USDT, DAI prices.
    """
    ids = "tether,usd-coin,dai"
    url = (
        f"{COINGECKO_API}/simple/price"
        f"?ids={ids}"
        f"&vs_currencies=usd"
        f"&include_24hr_change=true"
    )
    data = _get_json(url)
    if not data:
        return {}
    return {
        "USDT": {
            "usd": data.get("tether", {}).get("usd", 1.0),
            "usd_24h_change": data.get("tether", {}).get("usd_24h_change", 0),
        },
        "USDC": {
            "usd": data.get("usd-coin", {}).get("usd", 1.0),
            "usd_24h_change": data.get("usd-coin", {}).get("usd_24h_change", 0),
        },
        "DAI": {
            "usd": data.get("dai", {}).get("usd", 1.0),
            "usd_24h_change": data.get("dai", {}).get("usd_24h_change", 0),
        },
    }


def fetch_trending_coins():
    """Fetch trending coins from CoinGecko."""
    data = _get_json(f"{COINGECKO_API}/search/trending")
    if not data or "coins" not in data:
        return []
    return [
        {
            "id": c["item"]["id"],
            "symbol": c["item"]["symbol"],
            "name": c["item"]["name"],
            "market_cap_rank": c["item"].get("market_cap_rank"),
        }
        for c in data["coins"][:10]
    ]


# ---------------------------------------------------------------------------
# FEAR & GREED INDEX
# ---------------------------------------------------------------------------

def fetch_fear_greed():
    """
    Crypto Fear & Greed index from alternative.me.
    Returns: { value: int 0-100, label: str, timestamp: str }
    """
    data = _get_json(f"{FEAR_GREED_API}/?limit=1")
    if not data or "data" not in data:
        return {"value": 50, "label": "Neutral", "timestamp": None}

    entry = data["data"][0]
    return {
        "value": int(entry.get("value", 50)),
        "label": entry.get("value_classification", "Neutral"),
        "timestamp": entry.get("timestamp"),
    }


def fetch_fear_greed_history(days=30):
    """Fetch historical Fear & Greed for trend analysis."""
    data = _get_json(f"{FEAR_GREED_API}/?limit={days}")
    if not data or "data" not in data:
        return []
    return [
        {
            "value": int(d["value"]),
            "label": d["value_classification"],
            "date": datetime.fromtimestamp(
                int(d["timestamp"]), tz=timezone.utc
            ).isoformat(),
        }
        for d in data["data"]
    ]


# ---------------------------------------------------------------------------
# COMPOUND RATE CALCULATOR
# ---------------------------------------------------------------------------

def calculate_compound_rate(positions):
    """
    Calculate effective compound rate from position history.

    positions: list of dicts with:
      - invested: amount put in (cents)
      - returned: amount received (cents)
      - resolved_at: ISO timestamp

    Returns dict with daily/weekly/monthly compound rates.
    """
    if not positions:
        return {
            "total_invested": 0,
            "total_returned": 0,
            "net_profit": 0,
            "win_rate": 0,
            "avg_return_pct": 0,
            "daily_compound_rate": 0,
            "weekly_projection": 0,
            "monthly_projection": 0,
            "quarterly_projection": 0,
            "positions_analyzed": 0,
        }

    total_invested = sum(p.get("invested", 0) for p in positions)
    total_returned = sum(p.get("returned", 0) for p in positions)
    net_profit = total_returned - total_invested

    wins = sum(
        1 for p in positions if p.get("returned", 0) > p.get("invested", 0)
    )
    win_rate = wins / len(positions) if positions else 0

    # Average return per position
    returns = []
    for p in positions:
        inv = p.get("invested", 0)
        ret = p.get("returned", 0)
        if inv > 0:
            returns.append((ret - inv) / inv)

    avg_return = sum(returns) / len(returns) if returns else 0

    # Estimate daily compound rate from timestamps
    daily_rate = 0
    if len(positions) >= 2:
        timestamps = []
        for p in positions:
            ts = p.get("resolved_at")
            if ts:
                try:
                    timestamps.append(
                        datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    )
                except Exception:
                    pass

        if len(timestamps) >= 2:
            timestamps.sort()
            span_days = max(
                1, (timestamps[-1] - timestamps[0]).total_seconds() / 86400
            )
            if total_invested > 0:
                total_return_ratio = total_returned / total_invested
                daily_rate = (total_return_ratio ** (1 / span_days)) - 1

    # If we do not have enough data, estimate from average return
    if daily_rate == 0 and avg_return > 0:
        daily_rate = avg_return  # Assume ~1 trade per day

    # Project forward
    base = total_invested if total_invested > 0 else 2500  # Default $25 in cents
    weekly = base * ((1 + daily_rate) ** 7) if daily_rate > 0 else base
    monthly = base * ((1 + daily_rate) ** 30) if daily_rate > 0 else base
    quarterly = base * ((1 + daily_rate) ** 90) if daily_rate > 0 else base

    return {
        "total_invested": total_invested,
        "total_returned": total_returned,
        "net_profit": net_profit,
        "win_rate": round(win_rate, 4),
        "avg_return_pct": round(avg_return * 100, 2),
        "daily_compound_rate": round(daily_rate * 100, 4),
        "weekly_projection": round(weekly, 2),
        "monthly_projection": round(monthly, 2),
        "quarterly_projection": round(quarterly, 2),
        "positions_analyzed": len(positions),
    }


# ---------------------------------------------------------------------------
# STATE MANAGEMENT
# ---------------------------------------------------------------------------

def load_state():
    """Load persistent state."""
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {
            "cycles": 0,
            "positions": [],
            "opportunities_found": 0,
            "last_run": None,
        }


def save_state(state):
    """Save persistent state (trim history to last 500 positions)."""
    state["positions"] = state.get("positions", [])[-500:]
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# REVENUE REPORT GENERATOR
# ---------------------------------------------------------------------------

def generate_revenue_report():
    """
    Full revenue scan cycle:
      1. Scan Kalshi markets for opportunities
      2. Fetch crypto prices
      3. Fetch Fear & Greed index
      4. Calculate compound rate from history
      5. Output JSON report

    Returns the report dict.
    """
    state = load_state()
    state["cycles"] = state.get("cycles", 0) + 1
    now = datetime.now(timezone.utc)
    state["last_run"] = now.isoformat()

    report = {
        "generated_at": now.isoformat(),
        "cycle": state["cycles"],
        "status": "ok",
        "sections": {},
    }

    # 1. Kalshi market scan
    print("[money_agent] Scanning Kalshi markets...", file=sys.stderr)
    kalshi_opps = scan_kalshi_markets()
    kalshi_events = scan_kalshi_events(limit=10)

    report["sections"]["kalshi"] = {
        "opportunities_found": len(kalshi_opps),
        "top_opportunities": kalshi_opps[:10],
        "active_events": kalshi_events[:5],
        "series_scanned": len(FAST_SERIES),
    }
    state["opportunities_found"] = (
        state.get("opportunities_found", 0) + len(kalshi_opps)
    )

    # 2. Crypto prices
    print("[money_agent] Fetching crypto prices...", file=sys.stderr)
    crypto_prices = fetch_crypto_prices()
    stablecoin_prices = fetch_stablecoin_prices()
    trending = fetch_trending_coins()

    # Identify best AI-aligned tokens
    ai_token_ids = {"fetch-ai", "singularitynet", "ocean-protocol", "render-token"}
    ai_tokens = {k: v for k, v in crypto_prices.items() if k in ai_token_ids}
    best_ai = None
    if ai_tokens:
        best_ai_entry = max(
            ai_tokens.items(), key=lambda x: x[1].get("usd_24h_change", 0)
        )
        best_ai = {"id": best_ai_entry[0], **best_ai_entry[1]}

    report["sections"]["crypto"] = {
        "prices": crypto_prices,
        "stablecoins": stablecoin_prices,
        "trending": trending[:5],
        "best_ai_token": best_ai,
        "btc_price": crypto_prices.get("bitcoin", {}).get("usd", 0),
        "eth_price": crypto_prices.get("ethereum", {}).get("usd", 0),
    }

    # 3. Fear & Greed
    print("[money_agent] Fetching Fear & Greed...", file=sys.stderr)
    fg = fetch_fear_greed()
    fg_history = fetch_fear_greed_history(days=7)

    # Determine trend
    fg_trend = "stable"
    if len(fg_history) >= 3:
        recent_avg = sum(d["value"] for d in fg_history[:3]) / 3
        older_avg = sum(d["value"] for d in fg_history[-3:]) / 3
        if recent_avg > older_avg + 5:
            fg_trend = "improving"
        elif recent_avg < older_avg - 5:
            fg_trend = "deteriorating"

    report["sections"]["fear_greed"] = {
        "current": fg,
        "trend": fg_trend,
        "history_7d": fg_history,
    }

    # 4. Compound rate from position history
    compound_stats = calculate_compound_rate(state.get("positions", []))
    report["sections"]["compound"] = compound_stats

    # 5. Summary signal
    signal = "hold"
    reasoning = []

    if fg["value"] <= 25:
        signal = "buy_dip"
        reasoning.append(
            f"Extreme fear ({fg['value']}) -- contrarian buy signal"
        )
    elif fg["value"] >= 75:
        signal = "take_profit"
        reasoning.append(
            f"Extreme greed ({fg['value']}) -- consider taking profits"
        )

    if len(kalshi_opps) >= 5:
        reasoning.append(
            f"{len(kalshi_opps)} Kalshi opportunities found -- active market"
        )

    if best_ai and best_ai.get("usd_24h_change", 0) > 5:
        reasoning.append(
            f"AI token {best_ai['symbol']} up "
            f"{best_ai['usd_24h_change']:.1f}% -- momentum"
        )

    btc_chg = crypto_prices.get("bitcoin", {}).get("usd_24h_change", 0)
    if btc_chg and btc_chg < -5:
        reasoning.append(f"BTC down {btc_chg:.1f}% -- risk off environment")
        signal = "caution"
    elif btc_chg and btc_chg > 5:
        reasoning.append(f"BTC up {btc_chg:.1f}% -- risk on environment")

    # Check stablecoin peg deviations
    for name, sdata in stablecoin_prices.items():
        peg_dev = abs(sdata.get("usd", 1.0) - 1.0)
        if peg_dev > 0.005:
            reasoning.append(
                f"{name} peg deviation: ${sdata['usd']:.4f} "
                f"({peg_dev*100:.2f}% off)"
            )

    report["sections"]["signal"] = {
        "action": signal,
        "reasoning": reasoning,
        "fear_greed": fg["value"],
        "kalshi_opp_count": len(kalshi_opps),
    }

    # Save state and report
    save_state(state)
    REPORT_FILE.write_text(json.dumps(report, indent=2), encoding="utf-8")

    return report


# ---------------------------------------------------------------------------
# CLI ENTRY POINT
# ---------------------------------------------------------------------------

def main():
    """Run a full revenue scan and print the report."""
    print("[money_agent] Starting revenue scan...", file=sys.stderr)
    start = time.time()

    report = generate_revenue_report()

    elapsed = time.time() - start
    report["elapsed_seconds"] = round(elapsed, 1)

    # Machine-readable JSON to stdout
    print(json.dumps(report, indent=2))

    # Human-readable summary to stderr
    kalshi = report["sections"].get("kalshi", {})
    crypto = report["sections"].get("crypto", {})
    fg = report["sections"].get("fear_greed", {}).get("current", {})
    sig = report["sections"].get("signal", {})

    print(f"\n{'='*60}", file=sys.stderr)
    print(
        f"MYCELIUM MONEY AGENT -- Revenue Scan #{report['cycle']}",
        file=sys.stderr,
    )
    print(f"{'='*60}", file=sys.stderr)
    print(
        f"  Kalshi opportunities: {kalshi.get('opportunities_found', 0)}",
        file=sys.stderr,
    )
    print(f"  BTC: ${crypto.get('btc_price', 0):,.2f}", file=sys.stderr)
    print(f"  ETH: ${crypto.get('eth_price', 0):,.2f}", file=sys.stderr)
    print(
        f"  Fear & Greed: {fg.get('value', '?')}/100 ({fg.get('label', '?')})",
        file=sys.stderr,
    )
    print(f"  Signal: {sig.get('action', '?').upper()}", file=sys.stderr)
    for r in sig.get("reasoning", []):
        print(f"    - {r}", file=sys.stderr)
    print(f"  Elapsed: {elapsed:.1f}s", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)

    return report


if __name__ == "__main__":
    main()
