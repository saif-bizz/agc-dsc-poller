# Brand Snippet — Customer-Facing Facts

This file is what Sara quotes from when a customer asks about hours,
shipping, payment, or returns. It is intentionally limited to facts that
are already publicly visible on the storefront and Google Maps listing.

---

## Store

- **Store name**: Acacia Garden Center
- **Public domain**: https://acaciagardencenter.com/
- **Physical store**: Al Warsan 3, Dubai, UAE
- **Hours**: Open 7 days a week
- **Get directions**: https://maps.app.goo.gl/AfQJtAzntB4FSaQF9

---

## Category scope (use as the vertical-list reply when a customer opens
without a specific product)

```
Welcome to Acacia Garden Center. We carry:
• Indoor plants
• Outdoor plants
• Trees
• Pots and planters
• Outdoor furniture
• In-pool and poolside furniture
• BBQ grills
• Water features
• Pebble and gravel

What are you looking for today?
```

---

## Shipping policy (UAE only)

Tiered by emirate. Single-threshold "Free over X" copy is wrong; quote the
geo-aware row.

| Emirate | Delivery (AED) | Free over (AED) | Schedule |
|---|---|---|---|
| Dubai | 25 | 99 | Everyday |
| Sharjah & Ajman | 25 | 99 | Everyday |
| Abu Dhabi — City | 199 | 499 | Saturday |
| Abu Dhabi — Outskirts (Ruwais, Al Sila, Liwa, Al Batha, Al Ghuwaifat) | 299 | 999 | Saturday |
| Northern Emirates (RAK, Fujairah, Khorfakkan, Dibba, Dhaid) | 199 | 499 | Friday |

Source of truth: the shipping accordion at acaciagardencenter.com.

---

## Payment

- Accepted online: card payment via the Shopify checkout.
- Cash on Delivery: honoured if offered. Sync with fulfilment; no
  bait-and-switch.

---

## Returns and warranty

- Plants: customer is responsible for care once delivered. Replacements
  are handled case-by-case by the floor team — do NOT promise a
  replacement, escalate via `telegram.py note` instead.
- Furniture and hard goods: refer to the product page warranty line.
  If the page does not state a warranty period, do not invent one.

---

## Unverified-claim guard

Sara must not state any of the following unless verified live from the
Shopify product page (warranty line) or a confirmed reply from the floor
team in the same turn:

- A free add-on service (planting, site visit, installation, etc.)
- A guarantee or refund window
- A multi-year or lifetime warranty
- A specific delivery slot ("by 3pm tomorrow", "same-day", etc.) — quote
  the shipping schedule above instead.

If a customer asks specifically about any of the above, do not improvise —
escalate via `telegram.py note` with the question verbatim and a brief
holding line back to the customer.

---

## Floor-team bridge format

When you cannot answer from Shopify or this snippet, bridge to the floor
team via Telegram with a one-line `Q-XXXX` reference:

```
Q-0042 [CID 67abc] Customer: <name> (<phone>)
Question: <verbatim question>
Need: <stock check / specific photo / delivery quote / etc>
```

Then send the customer a brief holding line: "Let me check that with our
floor team, I'll come back to you shortly." Do NOT chain holding messages
across multiple turns — see the irate-customer escalation rule in
sara-system.md.
