#!/usr/bin/env python3
"""
filters.py — deterministic message classifier.

Decides whether an inbound WhatsApp message is worth a reply, looks like a
B2B supplier pitch (skip), or reads as a complaint (escalate).

CLI:
  python filters.py classify "<message body>"
       prints {"actionable": bool, "supplier_pitch": bool, "complaint": bool}
"""
from __future__ import annotations

import argparse
import json
import re
import sys

ACK_EXACT = {
    "", "thank you", "thanks", "thank u", "thanku", "tnx",
    "ok", "okay", "noted", "got it", "sure", "alright",
    "👍", "🙏", "🤝",
}

STARTS_RULES = [
    ("okay", 25),
    ("thank", 25),
    ("amazing", 30),
    ("great", 25),
    ("perfect", 25),
    ("cool", 20),
]

ENDS_RULES = [
    ("thank you", 35),
    ("thanks", 35),
    ("thank u", 35),
]

CLOSER_PHRASES = [
    "let me consider", "let me think",
    "will get back", "look forward to hearing back",
    "will reach out myself", "have a nice night",
]


def is_actionable(body: str | None) -> bool:
    b = (body or "").strip().lower()
    if b in ACK_EXACT:
        return False
    if b.startswith("[reaction]") or b.startswith("[contacts]"):
        return False
    for prefix, max_len in STARTS_RULES:
        if b.startswith(prefix) and len(b) < max_len:
            return False
    for suffix, max_len in ENDS_RULES:
        if b.endswith(suffix) and len(b) < max_len:
            return False
    for phrase in CLOSER_PHRASES:
        if phrase in b:
            return False
    return True


def looks_like_supplier_pitch(body: str | None) -> bool:
    b = (body or "").lower()
    if not b:
        return False
    score = 0
    if re.search(r"\b(b2b|b 2 b|wholesale|wholesaler|reseller|supplier|manufacturer|distributor|exporter)\b", b):
        score += 1
    if re.search(r"\b(catalog|catalogue|price\s*list|MOQ|min(imum)?\s*order|FOB|CIF|EXW)\b", b, re.I):
        score += 1
    if re.search(r"\b(factory|company|firm|trading\s*co\.?|llc|pvt\.?\s*ltd|gmbh|ltd)\b", b):
        score += 1
    if re.search(r"\bwe\s*(supply|manufacture|export|produce)\b", b):
        score += 1
    if re.search(r"\bworking\s*with\b.*\b(brands?|retailers?|chains?)\b", b):
        score += 1
    return score >= 2


def looks_like_complaint(body: str | None) -> bool:
    b = (body or "").lower()
    if not b:
        return False
    return bool(re.search(r"\b(complaint|complain|terrible|awful|worst|disgusting|fraud|scam|cheat|liar)\b", b))


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("classify")
    s.add_argument("body")
    args = ap.parse_args()
    if args.cmd == "classify":
        out = {
            "actionable": is_actionable(args.body),
            "supplier_pitch": looks_like_supplier_pitch(args.body),
            "complaint": looks_like_complaint(args.body),
        }
        json.dump(out, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")


if __name__ == "__main__":
    main()
