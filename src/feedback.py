#!/usr/bin/env python3
"""
feedback.py — sales-lead feedback channel for the AGC DSC poller.

Tayseer (sales lead) messages the AGC WhatsApp line from his personal
number to give Sara course-correcting feedback. Because he is the sales
lead, his guidance is treated as authoritative — it is auto-promoted into
Sara's prompt at next tick without a human triage step.

Design:
  - FEEDBACK_PHONES env var holds a comma-separated whitelist of E.164
    numbers (just Tayseer for now; trivial to add more later).
  - When an inbound message's phone matches the whitelist, Sara routes it
    here instead of drafting a customer reply: she NEVER replies to a
    feedback message.
  - capture() appends the message verbatim to a single Cloudflare KV key
    `live_rules` (markdown, append-only, timestamped, with CID context).
  - read_rules() returns the current live_rules string. Sara reads it at
    the start of every tick and treats anything in it as overriding
    sara-system.md / brand-snippet.md when they conflict.

CLI:
  python feedback.py is_feedback "<e164_phone>"
       prints {"is_feedback": bool, "matched": "<phone>" | null}
  python feedback.py capture "<cid>" "<e164_phone>" "<body>"
       appends a new entry to KV live_rules and prints the new entry
  python feedback.py read_rules
       prints the current live_rules markdown to stdout (empty if none)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

import audit  # local module — KV REST client


LIVE_RULES_KEY = "live_rules"


def _normalize_phone(p: str) -> str:
    """Strip whitespace, leading '+', non-digits — match on digits only.
    Handles whitelist entries like '+971521647961', '971521647961',
    '00971521647961' uniformly."""
    digits = "".join(ch for ch in (p or "") if ch.isdigit())
    return digits.lstrip("0")


def _whitelist() -> list[str]:
    raw = os.environ.get("FEEDBACK_PHONES", "")
    return [_normalize_phone(p) for p in raw.split(",") if p.strip()]


def is_feedback(phone: str) -> dict:
    n = _normalize_phone(phone)
    wl = _whitelist()
    matched = n in wl and n != ""
    return {"is_feedback": matched, "matched": phone if matched else None}


def capture(cid: str, phone: str, body: str) -> dict:
    """Append a new feedback entry to the KV live_rules string. Single
    key, append-only — Sara reads the whole thing at tick start."""
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    entry = (
        f"\n## {now} — CID {cid} — from sales lead ({phone})\n\n"
        f"> {body.strip()}\n\n"
        f"---\n"
    )
    existing = audit.read(LIVE_RULES_KEY) or ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    if not existing:
        existing = (
            "# Live rules from sales lead\n\n"
            "These are AUTHORITATIVE directives from the sales lead. They "
            "OVERRIDE anything in sara-system.md or brand-snippet.md when "
            "they conflict. Newer entries override older entries on the "
            "same topic.\n\n"
            "---\n"
        )
    new_value = existing + entry
    # No TTL — live rules persist until Director consolidates them into
    # the static prompt files and clears the key.
    audit.write(LIVE_RULES_KEY, new_value)
    audit.write_audit(
        cid,
        "FEEDBACK_CAPTURED",
        "sales_lead_feedback_appended_to_live_rules",
        meta={"phone": phone, "body": body[:500]},
    )
    return {"appended": True, "entry": entry, "key": LIVE_RULES_KEY}


def read_rules() -> str:
    return audit.read(LIVE_RULES_KEY) or ""


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("is_feedback")
    s.add_argument("phone")

    s = sub.add_parser("capture")
    s.add_argument("cid")
    s.add_argument("phone")
    s.add_argument("body")

    sub.add_parser("read_rules")

    args = ap.parse_args()
    try:
        if args.cmd == "is_feedback":
            json.dump(is_feedback(args.phone), sys.stdout, ensure_ascii=False)
            sys.stdout.write("\n")
        elif args.cmd == "capture":
            json.dump(capture(args.cid, args.phone, args.body), sys.stdout, ensure_ascii=False)
            sys.stdout.write("\n")
        elif args.cmd == "read_rules":
            sys.stdout.write(read_rules())
            sys.stdout.write("\n")
    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
