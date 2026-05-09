#!/usr/bin/env python3
"""
telegram.py — minimal Telegram Bot API client for AGC DSC poller.

Two channels:
  TELEGRAM_CHAT_ID         floor-team escalation channel (genuine bridges +
                           irate-customer escalations)
  TELEGRAM_REVIEW_CHAT_ID  reviewer-only review channel (Sara posts dry-run
                           drafts here; not used in steady state)

Env vars:
  TELEGRAM_BOT_TOKEN          required
  TELEGRAM_CHAT_ID            required for `escalate` and `note`
  TELEGRAM_REVIEW_CHAT_ID     required for `review`

CLI:
  python telegram.py note "free-form text"
       # post to floor-team channel (genuine bridge)
  python telegram.py escalate <cid> <customer_name> <phone> <reason>
       # post a structured irate-customer escalation alert
  python telegram.py review <cid> <customer_label> <customer_msg> <drafted_reply>
       # post a Layer-2 dry-run review card to the reviewer channel
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


def _env(k: str, required: bool = True, default: str | None = None) -> str:
    v = os.environ.get(k, default)
    if required and not v:
        print(json.dumps({"error": f"missing_env:{k}"}), file=sys.stderr)
        sys.exit(2)
    return v or ""


def _send(chat_id: str, text: str, parse_mode: str | None = None) -> dict:
    token = _env("TELEGRAM_BOT_TOKEN")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    body = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            pass
        raise RuntimeError(f"telegram_http_{e.code}: {body}") from e


def note(text: str) -> dict:
    return _send(_env("TELEGRAM_CHAT_ID"), text)


def escalate(cid: str, name: str, phone: str, reason: str) -> dict:
    text = (
        f"[ESCALATE] {name} ({phone})\n"
        f"CID: {cid}\n"
        f"Reason: {reason}\n"
        f"Action: assigned to floor-team owner."
    )
    return _send(_env("TELEGRAM_CHAT_ID"), text)


def review(cid: str, label: str, customer_msg: str, drafted_reply: str) -> dict:
    text = (
        f"[CID {cid} | DSC review]\n"
        f"Customer ({label}): {customer_msg}\n\n"
        f"Drafted reply:\n{drafted_reply}\n\n"
        f"(dry-run — not sent to customer)"
    )
    return _send(_env("TELEGRAM_REVIEW_CHAT_ID"), text)


def _print(obj) -> None:
    json.dump(obj, sys.stdout, ensure_ascii=False, default=str)
    sys.stdout.write("\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("note")
    s.add_argument("text")

    s = sub.add_parser("escalate")
    s.add_argument("cid")
    s.add_argument("name")
    s.add_argument("phone")
    s.add_argument("reason")

    s = sub.add_parser("review")
    s.add_argument("cid")
    s.add_argument("label")
    s.add_argument("customer_msg")
    s.add_argument("drafted_reply")

    args = ap.parse_args()
    try:
        if args.cmd == "note":
            _print(note(args.text))
        elif args.cmd == "escalate":
            _print(escalate(args.cid, args.name, args.phone, args.reason))
        elif args.cmd == "review":
            _print(review(args.cid, args.label, args.customer_msg, args.drafted_reply))
    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
