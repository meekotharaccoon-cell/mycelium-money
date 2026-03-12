# Mycelium Money

> SolarPunk's revenue layer. Every dollar earned routes automatically. 15% always to Gaza.

**Part of the [SolarPunk organism](https://meekotharaccoon-cell.github.io/meeko-nerve-center/solarpunk.html)**

---

## What this is

Mycelium Money is the economic infrastructure of SolarPunk. It handles:

- Tracking all revenue across Gumroad, Ko-fi, GitHub Sponsors, and email agent tasks
- Automatic routing: 15% to PCRF before any other payout
- Contributor splits for anyone whose engine generates revenue
- Loop fund management: reinvesting into the next cycle

---

## Revenue channels

| Channel | Status |
|---------|--------|
| Gumroad (10 products queued) | Pending first sale |
| Ko-fi shop (6 listings ready) | Pending setup |
| GitHub Sponsors | Pending enable |
| Email Agent Exchange | Live, $0 so far |
| Grants | Hunting |

---

## The routing

Every dollar that enters the system is processed by `DISPATCH_HANDLER`:

```
Incoming payment
  → 15% to PCRF (EIN: 93-1057665)
  → contributor share (per registry)
  → remainder to loop fund
```

This is not configurable. It's in the code.

---

## Engines involved

`REVENUE_FLYWHEEL` · `DISPATCH_HANDLER` · `HUMAN_PAYOUT` · `PAYPAL_PAYOUT` · `KOFI_ENGINE` · `GUMROAD_ENGINE` · `GITHUB_SPONSORS_ENGINE` · `CONTRIBUTOR_REGISTRY` · `INCOME_ARCHITECT` · `REVENUE_OPTIMIZER`

---

## Part of the organism

- 🧠 **Nerve center**: [meeko-nerve-center](https://github.com/meekotharaccoon-cell/meeko-nerve-center)
- 🌍 **What SolarPunk is**: [solarpunk.html](https://meekotharaccoon-cell.github.io/meeko-nerve-center/solarpunk.html)
- 💰 **Revenue tracker**: [quick_revenue.html](https://meekotharaccoon-cell.github.io/meeko-nerve-center/quick_revenue.html)

---

*Founded by Meeko. Runs itself. For Palestine.*
