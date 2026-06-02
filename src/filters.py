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


JOB_APPLICATION_SIGNALS = [
    r"\bvacanc(?:y|ies)\b",
    r"\b(?:are|r)\s*(?:you|u)\s*hiring\b",
    r"\b(?:now|currently)\s*hiring\b",
    r"\bjob\s*(?:vacanc|opening|application|interview|offer|seeker|post)",
    r"\bapply(?:ing)?\s*for\s*(?:a\s*)?(?:job|position|vacancy|role)\b",
    r"\bjoin\s*(?:your|the)\s*team\b",
    r"\b(?:employment|recruitment)\s*(?:opportunit|enquir|inquir|application)",
    r"\bany\s*(?:job|vacanc(?:y|ies)|opening|position)s?\s*(?:available|open|here|going)\b",
    r"\b(?:my|send\s*(?:my|you\s*my)|attach(?:ed|ing)?\s*(?:my)?|sharing\s*my)\s*(?:cv|c\.v\.|resume|r[eé]sum[eé])\b",
    r"\blooking\s*for\s*(?:a\s*)?(?:job|employment)\b",
]


def looks_like_job_application(body: str | None) -> bool:
    """Recruitment / job-seeker detector. Job applications do not contribute to
    sales and must NOT be escalated to the floor team (Director instruction
    2026-06-02). HIGH-PRECISION by design: bare "job"/"work" are excluded so a
    landscaping "job" lead is never silently dropped. Mirror of
    filters.ts looksLikeJobApplication() — keep byte-parity."""
    b = (body or "").lower()
    if not b:
        return False
    return any(re.search(p, b) for p in JOB_APPLICATION_SIGNALS)


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
            "job_application": looks_like_job_application(args.body),
            "complaint": looks_like_complaint(args.body),
        }
        json.dump(out, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")


if __name__ == "__main__":
    main()
