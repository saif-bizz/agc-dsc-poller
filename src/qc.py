#!/usr/bin/env python3
"""
qc.py — Layer-1 programmatic QC checks for a drafted WhatsApp reply.

Sara MUST run this against any drafted reply before calling gallabox.py send.
If the check fails she revises and re-runs; after 3 fails she escalates to
the floor team via telegram.py escalate (no reply sent).

The 7 checks:
  1. Em-dash regex                           (em dashes are forbidden)
  2. Length cap (<=350 chars)
  3. Hedge-word block (guaranteed/definitely/100% safe/etc.)
  4. Price cross-check vs this-turn Shopify cache
  5. Fabricated-commitment block (delivery tomorrow, free shipping, etc.)
  6. Sensitive-keyword pre-check (customer side — short-circuits the turn)
  7. No-fabricated-link / phone block

CLI:
  python qc.py pre_check "<customer's last message>"
      -> {"pass": bool, "escalate": bool, "reason": str}

  python qc.py post_check --body-file <path> [--shopify-file <path>] \
      [--shipping-file <path>] [--credentials-file <path>]
      -> {"pass": bool, "reason": str?, "hint": str?}

  Body / cache / shipping / credentials are read from files to avoid
  command-line size limits and quoting issues.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


SENSITIVE_KEYWORDS = [
    "refund", "return", "complaint", "manager", "lawsuit",
    "dead plant", "stuck", "not delivered", "damaged",
    "wrong item", "cancel my order", "not happy", "disappointed",
]
SENSITIVE_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(s) for s in SENSITIVE_KEYWORDS) + r")\b",
    re.I,
)

HEDGE_WORDS = [
    "guaranteed", "definitely", "100% safe", "risk-free",
    "lifetime warranty", "forever",
]

FABRICATED_COMMITMENT_PHRASES = [
    "delivery tomorrow", "same-day", "same day delivery",
    "free shipping", "we'll call you in 1 hour", "call you back in",
    "delivery today", "next-day delivery",
]

EM_DASH_RE = re.compile(r"—| -- ")
PRICE_RE = re.compile(r"(?:AED\s*([0-9][0-9,\.]*))|(?:([0-9][0-9,\.]*)\s*AED)", re.I)
URL_RE = re.compile(r"https?://[^\s)]+", re.I)
PHONE_RE = re.compile(r"\+?\d[\d\s\-()]{6,}")


def pre_check(customer_body: str) -> dict:
    m = SENSITIVE_RE.search(customer_body or "")
    if m:
        return {
            "pass": False,
            "escalate": True,
            "reason": f"sensitive_keyword:{m.group(0).lower()}",
        }
    return {"pass": True}


def _normalise_dirhams(s: str) -> int | None:
    cleaned = s.replace(",", "").replace(" ", "")
    try:
        return round(float(cleaned))
    except ValueError:
        return None


INVOICE_URL_MARKER = "/invoices/"


def post_check(body: str, shopify_text: str = "", shipping_block: str = "", credentials_text: str = "", draft_text: str = "") -> dict:
    text = body or ""

    # 0. Checkout-link integrity: any Shopify draft-order invoice URL in the
    #    reply MUST match the invoiceUrl returned by a draft_order_create run
    #    in THIS turn (passed via --draft-file). Prevents fabricated checkout
    #    links: no draft, no link.
    for um in URL_RE.finditer(text):
        url = re.sub(r"[.,;:!)\]'\"]+$", "", um.group(0))
        if INVOICE_URL_MARKER in url and url not in (draft_text or ""):
            return {
                "pass": False,
                "reason": f"invoice_url_without_matching_draft:{url}",
                "hint": "A checkout/invoice link may only be sent if it is the exact invoiceUrl returned by shopify.py draft_order_create in this turn. Create the draft first and pass its JSON output via --draft-file.",
            }

    # 1. Em-dash
    if EM_DASH_RE.search(text):
        return {
            "pass": False,
            "reason": "em_dash_in_body",
            "hint": "Remove all em dashes (—) and the literal sequence ' -- '. Use commas, periods, colons, or parentheses instead.",
        }

    # 2. Length cap
    if len(text) > 350:
        return {
            "pass": False,
            "reason": f"length_over_350_chars:{len(text)}",
            "hint": "Trim the reply to <=350 characters. Aim for <=60 words. One forward move only.",
        }

    # 3. Hedge words
    for w in HEDGE_WORDS:
        if re.search(rf"\b{re.escape(w)}\b", text, re.I):
            return {
                "pass": False,
                "reason": f"hedge_word:{w}",
                "hint": f'Remove the absolute-claim word "{w}". Use measured language ("typically", "in most cases") or remove the line.',
            }

    # 4. Price cross-check — against this turn's Shopify lookups AND (if a
    #    draft order was created this turn) the draft's line-item/total
    #    prices, so a quoted draft total must match what Shopify returned.
    price_corpus = f"{shopify_text}\n{draft_text}"
    for m in PRICE_RE.finditer(text):
        raw = m.group(1) or m.group(2)
        if not raw:
            continue
        dirhams = _normalise_dirhams(raw)
        if dirhams is None:
            continue
        candidates = [str(dirhams), f"{dirhams}.0", f"{dirhams}.00"]
        if not any(c in price_corpus for c in candidates):
            return {
                "pass": False,
                "reason": f"price_not_in_shopify_cache:AED_{dirhams}",
                "hint": f'Price "AED {dirhams}" is not present in this turn\'s Shopify response data. Re-query the product before quoting any price.',
            }

    # 5. Fabricated commitment
    shipping_lower = (shipping_block or "").lower()
    lower_text = text.lower()
    for phrase in FABRICATED_COMMITMENT_PHRASES:
        if phrase in lower_text and phrase not in shipping_lower:
            return {
                "pass": False,
                "reason": f"fabricated_commitment:{phrase}",
                "hint": f'Removed promised commitment "{phrase}" — it is not in the AGC shipping policy. Quote only what the policy supports, or escalate via telegram.',
            }

    # 7. URL / phone fabrication
    # draft_text (this turn's draft_order_create output) is part of the
    # haystack so the Shopify-returned invoiceUrl and draft line-item
    # prices are whitelisted (price check 4 uses shopify_text only — the
    # caller passes the draft JSON in --shopify-file too when quoting
    # draft totals, or quotes prices already verified via search).
    haystack = f"{credentials_text}\n{shopify_text}\n{draft_text}"
    for um in URL_RE.finditer(text):
        url = re.sub(r"[.,;:!)\]'\"]+$", "", um.group(0))
        if url not in haystack:
            return {
                "pass": False,
                "reason": f"novel_url:{url}",
                "hint": f'URL "{url}" was not found in brand credentials or this turn\'s Shopify response. Use only verified URLs.',
            }
    for pm in PHONE_RE.finditer(text):
        tok = pm.group(0).strip()
        digits = re.sub(r"\D", "", tok)
        if len(digits) < 7:
            continue
        if "AED" in tok.upper():
            continue
        if tok in haystack or digits in haystack:
            continue
        return {
            "pass": False,
            "reason": f"novel_phone:{tok}",
            "hint": f'Phone number "{tok}" is not verified. Use only numbers present in brand credentials, or remove the number entirely.',
        }

    return {"pass": True}


def _read(path: str | None) -> str:
    if not path:
        return ""
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        print(json.dumps({"error": f"read_failed:{path}:{e}"}), file=sys.stderr)
        sys.exit(2)


def _print(obj) -> None:
    json.dump(obj, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("pre_check")
    s.add_argument("body")

    s = sub.add_parser("post_check")
    s.add_argument("--body-file", required=True)
    s.add_argument("--shopify-file")
    s.add_argument("--shipping-file")
    s.add_argument("--credentials-file")
    s.add_argument("--draft-file",
                   help="JSON output of this turn's shopify.py draft_order_create. Required whenever the reply contains a checkout/invoice link.")

    args = ap.parse_args()
    if args.cmd == "pre_check":
        _print(pre_check(args.body))
    elif args.cmd == "post_check":
        body = _read(args.body_file)
        _print(post_check(
            body,
            shopify_text=_read(args.shopify_file),
            shipping_block=_read(args.shipping_file),
            credentials_text=_read(args.credentials_file),
            draft_text=_read(args.draft_file),
        ))


if __name__ == "__main__":
    main()
