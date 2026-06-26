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
  python gallabox.py thread <conversation_id> [--limit 10]
       # full recent WhatsApp thread oldest->newest with media URLs surfaced
  python gallabox.py download_media <media_url> <out_path>
       # downloads attachment so Sara can Read it (vision-enabled)
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

# audit.py owns KV access; used by the processed-message watermark guard in
# list_open_unassigned. Imported lazily-safe so gallabox.py still runs for
# commands that don't touch KV even if the audit module's env is unset.
try:
    import audit as _audit_seen  # type: ignore
except Exception:  # noqa: BLE001
    _audit_seen = None  # type: ignore


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

def list_open_unassigned(limit: int = 100, exclude_internal_last: bool = False) -> list[dict]:
    """AXIS B: list OPEN conversations whose assigneeId is null.

    When `exclude_internal_last=True`, also drop any conversation whose
    most recent WhatsApp message was sent from our side (Sara's own prior
    reply, a teammate's reply, a template send). This prevents Sara from
    re-walking already-answered threads every tick — she should only
    spend turns on threads where the customer is actually waiting on her.

    Fetches 2x the limit when filtering to leave headroom after drops."""
    fetch_limit = limit * 2 if exclude_internal_last else limit
    data = _request("/conversations", params={"limit": fetch_limit, "status": "OPEN", "sort": "-updatedAt"})
    items = _unwrap_list(data)
    unassigned = [c for c in items if not c.get("assigneeId")]
    if not exclude_internal_last:
        return unassigned[:limit]
    kept: list[dict] = []
    for c in unassigned:
        cid = c.get("_id") or c.get("id") or ""
        if not cid:
            continue
        try:
            la = last_actionable(cid)
        except Exception:
            # If we can't classify, keep — Sara's per-thread step 2.g is
            # the defence-in-depth backstop.
            kept.append(c)
            continue
        if la.get("is_internal", False):
            continue
        # Processed-message watermark (KV write-amplification guard, see
        # audit.set_seen_message_id). A customer-last thread that never leaves
        # the unassigned queue (e.g. a bare "Thank u") would otherwise be
        # re-classified and re-audited every */5 tick. Drop it once we have
        # already emitted its latest customer message-id; a new inbound carries
        # a new id and flows through. Best-effort: any KV hiccup keeps the
        # thread (fail-open, never lose a real customer reply).
        msg_id = la.get("message_id")
        if msg_id and _audit_seen is not None:
            try:
                if _audit_seen.get_seen_message_id(cid) == msg_id:
                    continue
            except Exception:  # noqa: BLE001 — fail-open, never lose a reply
                pass
        kept.append(c)
        if msg_id and _audit_seen is not None:
            try:
                _audit_seen.set_seen_message_id(cid, msg_id)
            except Exception:  # noqa: BLE001
                pass
        if len(kept) >= limit:
            break
    return kept


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


def _classify_role(msg: dict) -> str:
    """Deterministic role classification for a Gallabox WhatsApp message.

    Returns 'customer' if the message is inbound from the contact, 'agent'
    if it's outbound from us (channel/bot send, human teammate reply,
    template send, etc.). Single source of truth used by both
    last_actionable() and thread().

    Heuristics, in order:
      1. sender field == contact._id  -> customer (strong inbound signal)
      2. whatsapp.from present AND no user/userId field -> customer
         (covers payloads where contact._id isn't echoed on the message)
      3. otherwise -> agent (anything dispatched from our side)
    """
    contact_id = (msg.get("contact") or {}).get("_id", "")
    sender = msg.get("sender", "")
    if sender and contact_id and sender == contact_id:
        return "customer"
    wa_from = (msg.get("whatsapp") or {}).get("from")
    if wa_from and not (msg.get("user") or msg.get("userId")):
        return "customer"
    return "agent"


def last_actionable(cid: str) -> dict:
    """Convenience: fetch the most recent whatsapp message in the thread and
    return a deterministic envelope Sara can branch on without LLM judgment.

    Returns:
      {
        message_id, sender, body, media_type, created_at, raw_type,
        role: 'customer'|'agent',
        is_internal: bool,   # True when role == 'agent' — Sara's previous
                             # reply, a teammate's reply, or a template send.
                             # Step 2.g of run-prompt.md MUST skip drafting
                             # whenever is_internal is true.
      }
    """
    msgs = messages(cid, limit=10)
    wa = [m for m in msgs if m.get("channelType") == "whatsapp"]
    if not wa:
        return {
            "is_inbound": False,
            "is_internal": False,
            "role": None,
            "body": "",
            "reason": "no_whatsapp_messages",
        }
    last = wa[0]
    body, media_type = _extract_body(last)
    role = _classify_role(last)
    return {
        "message_id": last.get("_id") or last.get("id"),
        "sender": last.get("sender"),
        "body": body,
        "media_type": media_type,
        "created_at": last.get("createdAt") or last.get("created_at"),
        "raw_type": last.get("type"),
        "role": role,
        "is_internal": role == "agent",
    }


def _send_whatsapp(payload: dict) -> dict:
    """POST to the GLOBAL send endpoint /devapi/messages/whatsapp.

    NOT account-scoped — per brand-credentials.md §WhatsApp/Gallabox and the
    worker's gallabox.ts. (Bug fixed 2026-06-10: the account-scoped path +
    {conversationId,type,text} envelope never delivered; first live run
    logged SENT while customers received nothing.)
    """
    base = _env("GALLABOX_API_BASE", required=False, default="https://server.gallabox.com/devapi")
    url = f"{base.rstrip('/')}/messages/whatsapp"
    headers = {
        "apiKey": _env("GALLABOX_API_KEY"),
        "apiSecret": _env("GALLABOX_API_SECRET"),
        "Content-Type": "application/json",
    }
    data = json.dumps(payload).encode("utf-8")
    last_err: Exception | None = None
    for _ in range(RETRY_ATTEMPTS):
        try:
            req = urllib.request.Request(url, headers=headers, method="POST", data=data)
            with urllib.request.urlopen(req, timeout=30) as resp:
                out = json.loads(resp.read())
                # A real send returns an id/status from Gallabox. Surface it so
                # the caller can put it in the audit row; an empty response is
                # a FAILURE, not a success.
                if not out:
                    return {"error": "empty_send_response", "sent": False}
                out["sent"] = True
                return out
        except urllib.error.HTTPError as e:
            if e.code >= 500 or e.code == 429:
                last_err = e
                time.sleep(RETRY_SLEEP_S)
                continue
            err_body = ""
            try:
                err_body = e.read().decode("utf-8", "replace")[:300]
            except Exception:
                pass
            return {"error": f"http_{e.code}", "detail": err_body, "sent": False}
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(RETRY_SLEEP_S)
    return {"error": f"send_failed_after_retries:{last_err}", "sent": False}


def send(cid: str, phone: str, body: str, contact_name: str = "") -> dict:
    """Send a free-text WhatsApp reply. Honours DRY_RUN env var."""
    dry_run = (os.environ.get("DRY_RUN", "true").lower() == "true")
    payload = {
        "channelId": _env("GALLABOX_CHANNEL_ID"),
        "recipient": {"name": contact_name or phone, "phone": phone},
        "whatsapp": {"type": "text", "text": {"body": body}},
    }
    if dry_run:
        return {"action": "DRY_RUN", "would_send": payload}
    return _send_whatsapp(payload)


def send_image(cid: str, phone: str, image_url: str, caption: str | None = None,
               contact_name: str = "") -> dict:
    """Send an image WhatsApp reply by URL. The URL must be publicly
    fetchable by Meta's media servers (use media.py sign_url to wrap a
    Telegram file_id via the agc-dsc-media-proxy worker). Honours DRY_RUN.

    WhatsApp constraints:
      - Caption max 1024 chars (Gallabox returns 4xx if violated).
      - 24h messaging window applies — calling outside the window returns
        an error from Gallabox; caller should fall back to text-only.
    """
    dry_run = (os.environ.get("DRY_RUN", "true").lower() == "true")
    image_block: dict = {"link": image_url}
    if caption:
        image_block["caption"] = caption
    payload = {
        "channelId": _env("GALLABOX_CHANNEL_ID"),
        "recipient": {"name": contact_name or phone, "phone": phone},
        "whatsapp": {"type": "image", "image": image_block},
    }
    if dry_run:
        return {"action": "DRY_RUN", "would_send": payload}
    return _send_whatsapp(payload)


def _extract_media_path(msg: dict) -> str | None:
    """Return the public Gallabox file URL for an image/document/video, if any."""
    wa = msg.get("whatsapp") or {}
    for key in ("image", "document", "video", "audio", "voice"):
        sub = wa.get(key)
        if isinstance(sub, dict):
            path = sub.get("path") or sub.get("url")
            if path:
                return path
    return None


def thread(cid: str, limit: int = 10) -> list[dict]:
    """Return the recent WhatsApp thread oldest->newest with media URLs and
    role labels, suitable for Sara to reason over before drafting.

    Each row: {message_id, created_at, role ('customer'|'agent'), body,
               media_type, media_path}. role is best-effort: any whatsapp
               message with sender == contact._id is 'customer', else 'agent'.
    """
    raw = messages(cid, limit=limit)
    wa = [m for m in raw if m.get("channelType") == "whatsapp"]
    out: list[dict] = []
    for m in reversed(wa):  # oldest -> newest
        body, media_type = _extract_body(m)
        role = _classify_role(m)
        out.append({
            "message_id": m.get("_id") or m.get("id"),
            "created_at": m.get("createdAt") or m.get("created_at"),
            "role": role,
            "body": body,
            "media_type": media_type,
            "media_path": _extract_media_path(m),
        })
    return out


def download_media(url: str, out_path: str) -> dict:
    """Download an attachment from Gallabox files.gallabox.com to disk so
    Sara can use Read (vision) on the file."""
    last_err: Exception | None = None
    for attempt in range(RETRY_ATTEMPTS):
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
            with open(out_path, "wb") as f:
                f.write(data)
            return {"ok": True, "out_path": out_path, "bytes": len(data)}
        except Exception as e:
            last_err = e
            time.sleep(RETRY_SLEEP_S)
    return {"ok": False, "error": f"download_failed: {last_err}", "out_path": out_path}


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
    s.add_argument("--exclude-internal-last", action="store_true",
                   help="Drop conversations whose most recent WhatsApp message was outbound from us.")

    s = sub.add_parser("messages")
    s.add_argument("cid")
    s.add_argument("--limit", type=int, default=10)

    s = sub.add_parser("last_actionable")
    s.add_argument("cid")

    s = sub.add_parser("thread")
    s.add_argument("cid")
    s.add_argument("--limit", type=int, default=10)

    s = sub.add_parser("download_media")
    s.add_argument("url")
    s.add_argument("out_path")

    s = sub.add_parser("send")
    s.add_argument("cid")
    s.add_argument("phone")
    s.add_argument("body")
    s.add_argument("--contact-name", default="")

    s = sub.add_parser("send_image")
    s.add_argument("cid")
    s.add_argument("phone")
    s.add_argument("image_url")
    s.add_argument("--caption", default=None)
    s.add_argument("--contact-name", default="")

    s = sub.add_parser("assign")
    s.add_argument("cid")
    s.add_argument("user_id")

    args = ap.parse_args()

    try:
        if args.cmd == "list_open_unassigned":
            _print(list_open_unassigned(args.limit, getattr(args, "exclude_internal_last", False)))
        elif args.cmd == "messages":
            _print(messages(args.cid, args.limit))
        elif args.cmd == "last_actionable":
            _print(last_actionable(args.cid))
        elif args.cmd == "thread":
            _print(thread(args.cid, args.limit))
        elif args.cmd == "download_media":
            _print(download_media(args.url, args.out_path))
        elif args.cmd == "send":
            _print(send(args.cid, args.phone, args.body, getattr(args, "contact_name", "")))
        elif args.cmd == "send_image":
            _print(send_image(args.cid, args.phone, args.image_url,
                              getattr(args, "caption", None),
                              getattr(args, "contact_name", "")))
        elif args.cmd == "assign":
            _print(assign(args.cid, args.user_id))
    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
