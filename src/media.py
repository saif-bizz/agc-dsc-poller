#!/usr/bin/env python3
"""
media.py — generate HMAC-signed URLs for the agc-dsc-media-proxy
Cloudflare Worker so Sara can hand a Telegram file to Gallabox/WhatsApp
without exposing the bot token.

The worker validates `${fid}:${exp}` with HMAC-SHA256 and `MEDIA_HMAC_SECRET`,
checks the expiry is in the future, then fetches the file from Telegram and
streams it back. URLs are deliberately short-lived (default 10 min) — just
long enough for Gallabox/WhatsApp to fetch the media once.

Env vars:
  MEDIA_PROXY_BASE_URL   required, e.g. https://agc-dsc-media-proxy.agc-ops.workers.dev
  MEDIA_HMAC_SECRET      required, must match the secret set on the worker

CLI:
  python media.py sign <telegram_file_id> [--ttl-seconds 600]
       prints the signed proxy URL to stdout
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.parse


def _env(k: str) -> str:
    v = os.environ.get(k, "").strip()
    if not v:
        print(json.dumps({"error": f"missing_env:{k}"}), file=sys.stderr)
        sys.exit(2)
    return v


def sign_url(file_id: str, ttl_seconds: int = 600) -> str:
    base = _env("MEDIA_PROXY_BASE_URL").rstrip("/")
    secret = _env("MEDIA_HMAC_SECRET").encode("utf-8")
    exp = int(time.time()) + max(60, int(ttl_seconds))
    msg = f"{file_id}:{exp}".encode("utf-8")
    sig = hmac.new(secret, msg, hashlib.sha256).digest()
    sig_b64url = base64.urlsafe_b64encode(sig).rstrip(b"=").decode("ascii")
    qs = urllib.parse.urlencode({"fid": file_id, "exp": exp, "sig": sig_b64url})
    return f"{base}/media?{qs}"


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sign")
    s.add_argument("file_id")
    s.add_argument("--ttl-seconds", type=int, default=600)
    args = ap.parse_args()
    if args.cmd == "sign":
        sys.stdout.write(sign_url(args.file_id, args.ttl_seconds))
        sys.stdout.write("\n")


if __name__ == "__main__":
    main()
