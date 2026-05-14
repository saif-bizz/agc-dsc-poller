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
  python telegram.py note "free-form text" --cid <cid> --q-ref <Q-XXXX> [--category inventory|product|logistics]
       # post a bridge to floor-team channel. cid + q-ref are persisted
       # as bridge:<message_id> in KV so floor-team replies can be
       # matched back to the originating customer thread.
  python telegram.py escalate <cid> <customer_name> <phone> <reason>
       # post a structured irate-customer escalation alert
  python telegram.py review <cid> <customer_label> <customer_msg> <drafted_reply>
       # post a Layer-2 dry-run review card to the reviewer channel
  python telegram.py fetch_replies [--limit 100]
       # fetch new floor-team replies since the last processed update_id.
       # Returns a list of replies, each annotated with the matched cid +
       # q_ref (looked up via reply_to_message.message_id, or fallback by
       # parsing [CID xxx] from the body). Updates tg:last_update_id.
  python telegram.py warn_unmatched <update_id> <preview>
       # post an "unmatched reply — please use Reply feature" warning
       # back to the floor-team channel.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

import audit  # local module — KV REST client


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


def _api_get(method: str, params: dict | None = None) -> dict:
    """GET against Telegram Bot API. Used for getUpdates."""
    token = _env("TELEGRAM_BOT_TOKEN")
    url = f"https://api.telegram.org/bot{token}/{method}"
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            pass
        raise RuntimeError(f"telegram_http_{e.code}: {body}") from e


# ---------------------------------------------------------------------------
# Bridge persistence — KV-backed map from bot message_id -> {cid, q_ref, ...}
# so floor-team replies (which use Telegram's reply feature) can be matched
# back to the originating customer thread.
# ---------------------------------------------------------------------------

BRIDGE_TTL_DAYS = 7
LAST_UPDATE_KEY = "tg:last_update_id"
CID_RE = re.compile(r"\[CID\s+([a-f0-9]{8,})\]", re.IGNORECASE)
Q_REF_RE = re.compile(r"\b(Q-[A-Z0-9]{3,8})\b")


def _persist_bridge(message_id: int, cid: str, q_ref: str, category: str | None) -> None:
    envelope = {
        "cid": cid,
        "q_ref": q_ref,
        "category": category,
        "sent_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    audit.write(f"bridge:msg_{message_id}", envelope, ttl_seconds=BRIDGE_TTL_DAYS * 86400)


def _lookup_bridge(message_id: int) -> dict | None:
    val = audit.read(f"bridge:msg_{message_id}")
    if not val:
        return None
    try:
        return json.loads(val)
    except Exception:
        return None


# Floor-team routing by category. Names show up as a header on the bridge
# note so the right people know to act on it. Push notifications require
# numeric Telegram user IDs (not phone numbers) — once collected per person
# (have each member DM @userinfobot and report the ID back), populate the
# `tg_user_id` field and the renderer will switch from name labels to true
# @-mentions using HTML parse mode.
CATEGORY_ROUTES: dict[str, dict] = {
    "inventory": {
        "label": "📦 INVENTORY",
        "members": [
            {"name": "Shameer", "phone": "+971529295381", "tg_user_id": None},
            {"name": "Bala", "phone": "+971554042829", "tg_user_id": None},
            {"name": "Shibin", "phone": "+971586547689", "tg_user_id": None},
        ],
    },
    "product": {
        "label": "🌿 PRODUCT",
        "members": [
            {"name": "Rise", "phone": "+971529905819", "tg_user_id": None},
            {"name": "Abbas", "phone": "+971544664556", "tg_user_id": None},
        ],
    },
    "logistics": {
        "label": "🚚 LOGISTICS / DELIVERY",
        "members": [
            {"name": "Murad", "phone": "+971581885899", "tg_user_id": None},
        ],
    },
}


def _render_route_header(category: str | None) -> tuple[str, str | None]:
    """Returns (header_text, parse_mode). parse_mode is 'HTML' when at
    least one member has a tg_user_id (true @-mention) — falls back to
    None (plain text + name labels) when IDs aren't populated yet."""
    if not category:
        return "", None
    route = CATEGORY_ROUTES.get(category.lower())
    if not route:
        return "", None
    members = route["members"]
    has_ids = any(m.get("tg_user_id") for m in members)
    if has_ids:
        tags = " ".join(
            (f'<a href="tg://user?id={m["tg_user_id"]}">{m["name"]}</a>'
             if m.get("tg_user_id") else m["name"])
            for m in members
        )
        return f"<b>{route['label']}</b> — {tags}\n\n", "HTML"
    names = ", ".join(m["name"] for m in members)
    return f"{route['label']} — {names}\n\n", None


def note(text: str, category: str | None = None, cid: str | None = None,
         q_ref: str | None = None) -> dict:
    """Post a bridge to the floor-team channel.

    When cid + q_ref are provided, the bot's returned message_id is
    persisted to KV so floor-team replies (using Telegram's reply
    feature) can be matched back to this customer thread by
    fetch_replies().
    """
    header, parse_mode = _render_route_header(category)
    resp = _send(_env("TELEGRAM_CHAT_ID"), header + text, parse_mode=parse_mode)
    if cid and q_ref:
        result = (resp or {}).get("result") or {}
        msg_id = result.get("message_id")
        if isinstance(msg_id, int):
            try:
                _persist_bridge(msg_id, cid, q_ref, category)
                resp["bridge_persisted"] = {"message_id": msg_id, "cid": cid, "q_ref": q_ref}
            except Exception as e:
                resp["bridge_persist_error"] = str(e)
    return resp


def warn_unmatched(update_id: int, preview: str) -> dict:
    """Post a polite "couldn't match" note back to the floor-team channel
    when a reply has no reply_to_message and no [CID xxx] in the body."""
    text = (
        "⚠️ Couldn't match this reply to a customer thread:\n\n"
        f"  \"{(preview or '')[:200]}\"\n\n"
        "Please long-press the bot's original question and tap Reply, "
        "or include the [CID …] reference in your message."
    )
    try:
        return _send(_env("TELEGRAM_CHAT_ID"), text)
    except Exception as e:
        return {"ok": False, "error": str(e), "update_id": update_id}


def _extract_text(msg: dict) -> str:
    """Return the human-readable body of a Telegram message — plain text
    or photo/document caption."""
    return msg.get("text") or msg.get("caption") or ""


def _detect_media(msg: dict) -> dict | None:
    """Return a small descriptor when the message has media we can't
    relay in Phase 1 (photo/document/video). None for text-only."""
    if "photo" in msg and isinstance(msg["photo"], list) and msg["photo"]:
        # photo is a list of size variants — take the largest (last)
        return {"kind": "photo", "file_id": msg["photo"][-1].get("file_id")}
    if "document" in msg and isinstance(msg["document"], dict):
        return {"kind": "document", "file_id": msg["document"].get("file_id"),
                "mime_type": msg["document"].get("mime_type")}
    if "video" in msg and isinstance(msg["video"], dict):
        return {"kind": "video", "file_id": msg["video"].get("file_id")}
    return None


def _resolve_match(msg: dict) -> dict:
    """Return the best (cid, q_ref, source) match for an incoming reply.

    Resolution order:
      1. reply_to_message.message_id  -> KV lookup (deterministic)
      2. CID parsed from reply_to_message.text (if reply gesture used but
         the original wasn't persisted, e.g. legacy pre-Phase-1 bridges)
      3. CID parsed from the reply body itself
    Returns {matched: bool, cid, q_ref, source}.
    """
    # 1. KV lookup via reply-to message_id
    rtm = msg.get("reply_to_message") or {}
    rtm_id = rtm.get("message_id")
    if isinstance(rtm_id, int):
        bridge = _lookup_bridge(rtm_id)
        if bridge:
            return {"matched": True, "cid": bridge.get("cid"),
                    "q_ref": bridge.get("q_ref"), "source": "kv_message_id"}

    # 2. Parse CID from quoted original
    rtm_text = _extract_text(rtm)
    m = CID_RE.search(rtm_text)
    if m:
        q = Q_REF_RE.search(rtm_text)
        return {"matched": True, "cid": m.group(1),
                "q_ref": q.group(1) if q else None, "source": "parse_quoted"}

    # 3. Parse CID from reply body
    body = _extract_text(msg)
    m = CID_RE.search(body)
    if m:
        q = Q_REF_RE.search(body)
        return {"matched": True, "cid": m.group(1),
                "q_ref": q.group(1) if q else None, "source": "parse_body"}

    return {"matched": False, "cid": None, "q_ref": None, "source": None}


def _is_internal_user_id() -> int | None:
    """The bot's own user id, if discoverable. Used to filter the bot's
    own messages (and other bots) out of the reply stream."""
    try:
        me = _api_get("getMe")
        return ((me or {}).get("result") or {}).get("id")
    except Exception:
        return None


def fetch_replies(limit: int = 100) -> dict:
    """Pull new messages from the floor-team chat since last_update_id.

    Returns:
      {
        replies: [{ update_id, message_id, from_name, text, media,
                    matched, cid, q_ref, source, posted_at }, ...],
        unmatched_count, matched_count, advanced_to: <update_id|None>
      }

    Caller (Sara, in run-prompt STEP 1.5) iterates `replies` and for each
    `matched=true` row drafts + sends a customer follow-up; for each
    `matched=false` row, calls warn_unmatched() and skips.
    """
    floor_chat = _env("TELEGRAM_CHAT_ID")
    last_raw = audit.read(LAST_UPDATE_KEY) or ""
    try:
        offset = int(last_raw.strip()) + 1 if last_raw else 0
    except Exception:
        offset = 0

    params: dict = {"timeout": 0, "allowed_updates": json.dumps(["message"]),
                    "limit": min(limit, 100)}
    if offset:
        params["offset"] = offset

    resp = _api_get("getUpdates", params)
    updates = (resp or {}).get("result") or []
    bot_id = _is_internal_user_id()

    out: list[dict] = []
    last_seen: int | None = None
    for u in updates:
        uid = u.get("update_id")
        if isinstance(uid, int):
            last_seen = uid if last_seen is None else max(last_seen, uid)
        msg = u.get("message") or {}
        if not msg:
            continue
        chat = (msg.get("chat") or {}).get("id")
        # Telegram chat IDs can be int or str depending on platform; coerce
        if str(chat) != str(floor_chat):
            continue
        sender = msg.get("from") or {}
        sender_id = sender.get("id")
        # Skip our own bot's posts (we don't relay those back)
        if bot_id and sender_id == bot_id:
            continue
        if sender.get("is_bot"):
            continue

        match = _resolve_match(msg)
        out.append({
            "update_id": uid,
            "message_id": msg.get("message_id"),
            "from_name": (sender.get("first_name") or "") + (
                " " + sender.get("last_name") if sender.get("last_name") else ""),
            "from_username": sender.get("username"),
            "text": _extract_text(msg),
            "media": _detect_media(msg),
            "matched": match["matched"],
            "cid": match["cid"],
            "q_ref": match["q_ref"],
            "match_source": match["source"],
            "posted_at": msg.get("date"),
        })

    # Advance the offset only on success. If none returned, keep prior offset.
    if last_seen is not None:
        try:
            audit.write(LAST_UPDATE_KEY, str(last_seen))
        except Exception:
            pass

    return {
        "replies": out,
        "matched_count": sum(1 for r in out if r["matched"]),
        "unmatched_count": sum(1 for r in out if not r["matched"]),
        "advanced_to": last_seen,
    }


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
    s.add_argument("--category", choices=sorted(CATEGORY_ROUTES.keys()), default=None,
                   help="Floor-team route. Determines who is tagged in the bridge note.")
    s.add_argument("--cid", default=None,
                   help="Customer conversation id. Required for floor-team reply matching.")
    s.add_argument("--q-ref", dest="q_ref", default=None,
                   help="Bridge reference (e.g. Q-45A1). Stored alongside cid for trace.")

    s = sub.add_parser("fetch_replies")
    s.add_argument("--limit", type=int, default=100)

    s = sub.add_parser("warn_unmatched")
    s.add_argument("update_id", type=int)
    s.add_argument("preview")

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
            _print(note(args.text, getattr(args, "category", None),
                        getattr(args, "cid", None), getattr(args, "q_ref", None)))
        elif args.cmd == "escalate":
            _print(escalate(args.cid, args.name, args.phone, args.reason))
        elif args.cmd == "review":
            _print(review(args.cid, args.label, args.customer_msg, args.drafted_reply))
        elif args.cmd == "fetch_replies":
            _print(fetch_replies(args.limit))
        elif args.cmd == "warn_unmatched":
            _print(warn_unmatched(args.update_id, args.preview))
    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
