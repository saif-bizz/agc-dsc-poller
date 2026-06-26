#!/usr/bin/env python3
"""
audit.py — KV writes/reads against the existing Cloudflare Workers KV
namespace AGC_DSC_KV via the Cloudflare REST API.

We reuse the namespace that the CF worker created so the audit log
format (and any existing dry-run/pending rows) survive the migration.

Env vars:
  CF_API_TOKEN            required — Cloudflare API token with
                          "Workers KV Storage:Edit" permission scoped to
                          the relevant account
  CF_ACCOUNT_ID           required — Cloudflare account id
  CF_KV_NAMESPACE_ID      required — KV namespace id (set as a GitHub
                          Secret; never hard-coded here)

CLI:
  python audit.py write <key> <json_value> [--ttl-seconds N]
  python audit.py read <key>
  python audit.py list <prefix>            # list keys with prefix
  python audit.py write_audit <cid> <decision> <reason> [--meta '<json>']
       # convenience: writes audit:<UTC iso minute>:<cid> with a
       # standard envelope {decision, reason, ts, meta}

Decision strings should be one of:
  SENT, SENT_DRY_RUN, SKIPPED, ESCALATED_FLOOR_TEAM,
  QC_BLOCKED, QC_SENSITIVE_ESCALATE, ERROR

This script never raises on KV write failure — it logs to stderr and
exits 1 so a bad audit write doesn't kill the whole poller run.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


def _env(k: str, required: bool = True, default: str | None = None) -> str:
    v = os.environ.get(k, default)
    if required and not v:
        # Loud + grepable: a missing CF secret means the whole audit layer
        # is dead. Non-fatal to the poller (callers ignore our exit code),
        # but it must be visible in the Actions log.
        print(f"[AUDIT_WRITE_FAILED] missing_env:{k}", file=sys.stderr)
        print(json.dumps({"error": f"missing_env:{k}"}), file=sys.stderr)
        sys.exit(2)
    return v or ""


def _base() -> str:
    acct = _env("CF_ACCOUNT_ID")
    ns = _env("CF_KV_NAMESPACE_ID")
    return f"https://api.cloudflare.com/client/v4/accounts/{acct}/storage/kv/namespaces/{ns}"


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_env('CF_API_TOKEN')}",
        "Content-Type": "application/json",
    }


def write(key: str, value: str | dict, ttl_seconds: int | None = None) -> dict:
    if isinstance(value, (dict, list)):
        body = json.dumps(value)
    else:
        body = str(value)
    qs = ""
    if ttl_seconds:
        qs = "?" + urllib.parse.urlencode({"expiration_ttl": ttl_seconds})
    url = f"{_base()}/values/{urllib.parse.quote(key, safe='')}{qs}"
    req = urllib.request.Request(url, method="PUT", headers=_headers(), data=body.encode("utf-8"))
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            pass
        raise RuntimeError(f"kv_write_http_{e.code}: {body}") from e


def read(key: str) -> str | None:
    url = f"{_base()}/values/{urllib.parse.quote(key, safe='')}"
    req = urllib.request.Request(url, method="GET", headers={"Authorization": f"Bearer {_env('CF_API_TOKEN')}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def list_keys(prefix: str, limit: int = 1000) -> list[dict]:
    url = f"{_base()}/keys?" + urllib.parse.urlencode({"prefix": prefix, "limit": min(limit, 1000)})
    req = urllib.request.Request(url, method="GET", headers={"Authorization": f"Bearer {_env('CF_API_TOKEN')}"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read())
    return (data.get("result") or []) if isinstance(data, dict) else []


def write_audit(cid: str, decision: str, reason: str, meta: dict | None = None, ttl_days: int = 30) -> dict:
    now = dt.datetime.now(dt.timezone.utc)
    iso = now.strftime("%Y-%m-%dT%H-%M-%SZ")
    key = f"audit:{iso}:{cid}"
    envelope = {
        "decision": decision,
        "reason": reason,
        "ts": now.isoformat(),
        "cid": cid,
        "meta": meta or {},
    }
    return write(key, envelope, ttl_seconds=ttl_days * 86400)


def _draft_lock_key(cid: str) -> str:
    day = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    return f"draft-lock:{cid}:{day}"


def check_draft_lock(cid: str) -> dict:
    """O(1) read — has a draft order already been created for this CID today?"""
    return {"locked": read(_draft_lock_key(cid)) is not None, "cid": cid}


def set_draft_lock(cid: str, draft_name: str = "") -> dict:
    """Set the one-draft-per-conversation-per-day lock (26h TTL)."""
    key = _draft_lock_key(cid)
    write(key, {"draft": draft_name, "ts": dt.datetime.now(dt.timezone.utc).isoformat()},
          ttl_seconds=26 * 3600)
    return {"locked": True, "key": key}


# ---------------------------------------------------------------------------
# Per-conversation processed-message watermark (KV write-amplification guard).
#
# A stale OPEN-unassigned thread whose last message is the customer's (e.g. a
# bare "Thank u") never leaves the unassigned queue and is never filtered by
# --exclude-internal-last (customer is still last sender), so every */5 tick
# re-classified it SKIPPED and wrote a fresh audit row. ~3-4 such stuck threads
# x 288 ticks/day exhausted the FREE-tier 1,000 KV writes/day cap (incident
# 26-06-2026: AGC_DSC_KV measured 1,180 writes/24h, 423 in 9.5h across only 12
# CIDs, three of them re-audited 114 times each).
#
# The watermark stores the last customer message-id we have already emitted for
# a CID. list_open_unassigned drops a thread whose latest customer message-id
# equals the stored watermark (nothing new since we last handled it), so a stuck
# thread is written exactly once instead of every tick. A genuinely new inbound
# carries a new message-id, misses the watermark, and flows through normally.
# 7-day TTL auto-cleans abandoned threads.
# ---------------------------------------------------------------------------

SEEN_TTL_SECONDS = 7 * 24 * 60 * 60


def get_seen_message_id(cid: str) -> str | None:
    """Return the last customer message-id already processed for this CID."""
    return read(f"seen:{cid}")


def set_seen_message_id(cid: str, message_id: str) -> dict:
    """Record the latest processed customer message-id for this CID (7d TTL)."""
    key = f"seen:{cid}"
    write(key, message_id, ttl_seconds=SEEN_TTL_SECONDS)
    return {"ok": True, "key": key, "message_id": message_id}


def _print(obj) -> None:
    json.dump(obj, sys.stdout, ensure_ascii=False, default=str)
    sys.stdout.write("\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("write")
    s.add_argument("key")
    s.add_argument("value")
    s.add_argument("--ttl-seconds", type=int)

    s = sub.add_parser("read")
    s.add_argument("key")

    s = sub.add_parser("list")
    s.add_argument("prefix")

    s = sub.add_parser("write_audit")
    s.add_argument("cid")
    s.add_argument("decision")
    s.add_argument("reason")
    s.add_argument("--meta", default="{}")

    s = sub.add_parser("check_draft_lock")
    s.add_argument("cid")

    s = sub.add_parser("set_draft_lock")
    s.add_argument("cid")
    s.add_argument("--draft-name", default="")

    args = ap.parse_args()
    try:
        if args.cmd == "write":
            _print(write(args.key, args.value, args.ttl_seconds))
        elif args.cmd == "read":
            v = read(args.key)
            sys.stdout.write(v if v is not None else "")
            sys.stdout.write("\n")
        elif args.cmd == "list":
            _print(list_keys(args.prefix))
        elif args.cmd == "write_audit":
            try:
                meta = json.loads(args.meta) if args.meta else {}
            except Exception:
                meta = {"raw_meta": args.meta}
            _print(write_audit(args.cid, args.decision, args.reason, meta))
        elif args.cmd == "check_draft_lock":
            _print(check_draft_lock(args.cid))
        elif args.cmd == "set_draft_lock":
            _print(set_draft_lock(args.cid, args.draft_name))
    except Exception as e:
        # Grepable failure marker — audit writes must be LOUD in the
        # Actions log but never fatal to the poller run (exit code is
        # non-zero, callers treat it as best-effort).
        print(f"[AUDIT_WRITE_FAILED] {e}", file=sys.stderr)
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
