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
team via Telegram. **You must pick the right category** so the right
people get tagged:

| Category | Use for | Tagged |
|---|---|---|
| `inventory` | "Do we have X in stock?", quantity-on-hand, "is this size available?", anything that needs the warehouse/stock list | Shameer, Bala, Shibin |
| `product` | Variety/cultivar questions, plant care, "what's the difference between A and B", product fit recommendations | Rise, Abbas |
| `logistics` | Delivery windows, slot timing, route questions, "can you deliver to X area by Y date" | Murad |

Call the helper with `--category`:

```
python src/telegram.py note --category inventory "Q-0042 [CID 67abc1234567]


Do we have an 8m Olive tree in stock?"
```

This produces, in Telegram:

```
📦 INVENTORY — Shameer, Bala, Shibin

Q-0042 [CID 67abc1234567]


Do we have an 8m Olive tree in stock?
```

Rules for the bridge note body:

- First line: the `Q-XXXX` reference + `[CID <full conversation id>]`
  in square brackets so the floor team can find the chat in Gallabox.
- Then **two blank lines** separating the reference from the question
  (these render as visual breathing room in Telegram).
- Then the **direct factual question** the floor team needs to answer,
  written in the floor team's voice (what would YOU ask a colleague
  who has the stock list in hand?).
- Always pass `--category`. If you genuinely cannot tell which category
  fits (rare — most questions fit one cleanly), default to `inventory`.

Do NOT include in the bridge note:
- Customer name
- Customer phone number
- Verbatim quote of the customer's message
- Conversation history or context
- "Question:" / "Need:" / "Customer:" prefixes
- Any pleasantries ("Hi team", "Could you please", "Thank you")

The floor team will not be reading the customer chat — they will only
glance at this Telegram note. Every word that isn't the actual question
slows them down. Strip everything that doesn't help them answer.

After posting the bridge, send the customer a brief holding line:
"Let me check that and come back to you shortly." Do NOT chain holding
messages across multiple turns — see the irate-customer escalation rule
in sara-system.md.
