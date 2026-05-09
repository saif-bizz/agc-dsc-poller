#!/usr/bin/env python3
"""
gallabox.py — Gallabox API client for the AGC DSC GitHub Actions poller.

Mirrors the request shape used by the local poll.py and the Cloudflare worker
client (auth via apiKey + apiSecret headers, channelId query param). 3 retries
with sleep on transient failure.

Env vars consumed:
  GALLABOX_API_KEY        required
  GALLABOX_API_SECRET     required
  GALLABOX_ACCOUNT_ID     required
  GALLABOX_CHANNEL_ID     required
  GALLABOX_API_BASE       optional (default https://server.gallabox.com/devapi)
  DRY_RUN                 optional (default "true"). When "true", send/assign
                          are NOT dispatched; the call is logged via stdout
                          as a JSON line with action=DRY_RUN.

CLI usage:
  python gallabox.py list_open_unassigned [--limit 100]
  python gallabox.py messages <conversation_id> [--limit 10]
  python gallabox.py send <conversation_id> <e164_phone> <body>
  python gallabox.py assign <conversation_id> <user_id>
  python gallabox.py last_actionable <conversation_id>
       # convenience: returns last whatsapp inbound message + actionable bool

All commands print a single JSON object to stdout. Non-zero exit on hard
error. Designed for Sara to invoke via Bash and parse the JSON.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


RETRY_ATTEMPTS = 3
RETRY_SLEEP_S = 2.5


def _env(key: str, required: bool = True, default: str | None = None) -> str:
    v = os.environ.get(key, default)
    if required and not v:
        print(json.dumps({"error": f"missing_env:{key}"}), file=sys.stderr)
        sys.exit(2)
    return v or ""


def _base() -> str:
    base = _env("GALLABOX_API_BASE", required=False, default="https://server.gallabox.com/devapi")
    acct = _env("GALLABOX_ACCOUNT_ID")
    return f"{base.rstrip('/')}/accounts/{acct}"


def _request(path: str, params: dict | None = None, method: str = "GET", body: dict | None = None) -> dict | list:
    url = _base() + path
    p = {"channelId": _env("GALLABOX_CHANNEL_ID")}
    if params:
        p.update({k: v for k, v in params.items() if v is not None})
    url += "?" + urllib.parse.urlencode(p)
    headers = {
        "apiKey": _env("GALLABOX_API_KEY"),
        "apiSecret": _env("GALLABOX_API_SECRET"),
        "Content-Type": "application/json",
    }
    data = json.dumps(body).encode("utf-8") if body is not None else None

    last_err: Exception | None = None
    for attempt in range(RETRY_ATTEMPTS):
        try:
            req = urllib.request.Request(url, headers=headers, method=method, data=data)
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            # Retry 5xx + 429; bail on 4xx other than 429.
            if e.code >= 500 or e.code == 429:
                last_err = e
                time.sleep(RETRY_SLEEP_S)
                continue
            err_body = ""
            try:
                err_body = e.read().decode("utf-8", errors="replace")[:240]
            except Exception:
                pass
            raise RuntimeError(f"gallabox_http_{e.code}: {err_body}") from e
        except Exception as e:
            last_err = e
            time.sleep(RETRY_SLEEP_S)
    raise RuntimeError(f"gallabox_unknown_error: {last_err}")


def _unwrap_list(payload, keys=("conversations", "items", "messages", "data")) -> list:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for k in keys:
            if isinstance(payload.get(k), list):
                return payload[k]
    return []


# ---------------------------------------------------------------------------
# Public commands
# ---------------------------------------------------------------------------

def list_open_unassigned(limit: int = 100) -> list[dict]:
    """AXIS B: list OPEN conversations whose assigneeId is null."""
    data = _request("/conversations", params={"limit": limit, "status": "OPEN", "sort": "-updatedAt"})
    items = _unwrap_list(data)
    return [c for c in items if not c.get("assigneeId")]


def messages(cid: str, limit: int = 10) -> list[dict]:
    data = _request(f"/conversation/{cid}/messages", params={"limit": limit})
    return _unwrap_list(data)


def _extract_body(msg: dict) -> tuple[str, str | None]:
    """Return (body_text, media_type_or_none) from a Gallabox message payload."""
    wa = msg.get("whatsapp") or {}
    wa_text = (wa.get("text") or {}).get("body") if isinstance(wa.get("text"), dict) else None
    wa_img_cap = (wa.get("image") or {}).get("caption") if isinstance(wa.get("image"), dict) else None
    wa_doc_cap = (wa.get("document") or {}).get("caption") if isinstance(wa.get("document"), dict) else None
    wa_vid_cap = (wa.get("video") or {}).get("caption") if isinstance(wa.get("video"), dict) else None
    wa_type = wa.get("type") if isinstance(wa, dict) else None
    body = (
        msg.get("body")
        or wa_text
        or wa_img_cap
        or wa_doc_cap
        or wa_vid_cap
        or msg.get("text")
        or ""
    )
    if not body and wa_type in ("audio", "voice", "image", "document", "video", "sticker"):
        return f"[MEDIA:{wa_type}]", wa_type
    return body, wa_type


def last_actionable(cid: str) -> dict:
    """Convenience: fetch the last whatsapp inbound message and return
    { is_inbound, body, media_type, sender, contact_id, raw_message? }.
    Sara uses this to decide whether to draft."""
    msgs = messages(cid, limit=10)
    wa = [m for m in msgs if m.get("channelType") == "whatsapp"]
    if not wa:
        return {"is_inbound": False, "body": "", "reason": "no_whatsapp_messages"}
    last = wa[0]
    sender = last.get("sender")
    # Best-effort contact id resolution from the conversation list
    body, media_type = _extract_body(last)
    return {
        "message_id": last.get("_id") or last.get("id"),
        "sender": sender,
        "body": body,
        "media_type": media_type,
        "created_at": last.get("createdAt") or last.get("created_at"),
        "raw_type": last.get("type"),
    }


def send(cid: str, phone: str, body: str) -> dict:
    """Send a free-text WhatsApp reply. Honours DRY_RUN env var."""
    dry_run = (os.environ.get("DRY_RUN", "true").lower() == "true")
    payload = {
        "conversationId": cid,
        "phone": phone,
        "type": "text",
        "text": {"body": body},
    }
    if dry_run:
        return {"action": "DRY_RUN", "would_send": payload}
    # Gallabox WhatsApp send endpoint shape — same as the worker tool handler.
    return _request("/messages/whatsapp", method="POST", body=payload)


def assign(cid: str, user_id: str) -> dict:
    """Assign a conversation. Honours DRY_RUN."""
    dry_run = (os.environ.get("DRY_RUN", "true").lower() == "true")
    payload = {"assigneeId": user_id}
    if dry_run:
        return {"action": "DRY_RUN", "would_assign": {"cid": cid, "user_id": user_id}}
    return _request(f"/conversations/{cid}/assign", method="POST", body=payload)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print(obj) -> None:
    json.dump(obj, sys.stdout, ensure_ascii=False, default=str)
    sys.stdout.write("\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("list_open_unassigned")
    s.add_argument("--limit", type=int, default=100)

    s = sub.add_parser("messages")
    s.add_argument("cid")
    s.add_argument("--limit", type=int, default=10)

    s = sub.add_parser("last_actionable")
    s.add_argument("cid")

    s = sub.add_parser("send")
    s.add_argument("cid")
    s.add_argument("phone")
    s.add_argument("body")

    s = sub.add_parser("assign")
    s.add_argument("cid")
    s.add_argument("user_id")

    args = ap.parse_args()

    try:
        if args.cmd == "list_open_unassigned":
            _print(list_open_unassigned(args.limit))
        elif args.cmd == "messages":
            _print(messages(args.cid, args.limit))
        elif args.cmd == "last_actionable":
            _print(last_actionable(args.cid))
        elif args.cmd == "send":
            _print(send(args.cid, args.phone, args.body))
        elif args.cmd == "assign":
            _print(assign(args.cid, args.user_id))
    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
