#!/usr/bin/env python3
"""
shopify.py — Shopify Admin GraphQL client for AGC DSC poller.

Read-only by default. Sara uses this to verify stock + price + variant
availability before quoting.

Env vars:
  SHOPIFY_STORE          required (e.g. "your-store.myshopify.com")
  SHOPIFY_ADMIN_TOKEN    required
  SHOPIFY_API_VERSION    optional (default "2025-01")

CLI:
  python shopify.py search "fiddle leaf" [--limit 10]
  python shopify.py product_by_handle "fiddle-leaf-fig"
  python shopify.py product_by_id "1234567890"

Write path (the ONLY one — everything else stays read-only):
  python shopify.py draft_order_create \
      --line-item <variant_id>:<qty> [--line-item ...] \
      [--phone +9715XXXXXXXX] [--email x@y.com] [--note "..."] \
      [--source-channel whatsapp_direct] [--attr key=value ...]

  Creates a Shopify draft order (GraphQL draftOrderCreate) tagged
  sara-dsc + whatsapp-lead, with customAttributes carrying at minimum
  source_channel + created_by=sara-dsc (the agc-attribution worker's
  /shopify/draft-order-webhook enriches the rest). Prints the draft id,
  name, invoiceUrl, totalPrice and line-item prices as JSON.

  Honours DRY_RUN: when env DRY_RUN=="true" (the default), NOTHING is
  sent to Shopify — the call prints {"action": "DRY_RUN",
  "would_create": <input>} and exits 0, mirroring gallabox.py send.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request


def _env(k: str, default: str | None = None, required: bool = True) -> str:
    v = os.environ.get(k, default)
    if required and not v:
        print(json.dumps({"error": f"missing_env:{k}"}), file=sys.stderr)
        sys.exit(2)
    return v or ""


def _gql(query: str, variables: dict | None = None) -> dict:
    store = _env("SHOPIFY_STORE")
    token = _env("SHOPIFY_ADMIN_TOKEN")
    version = _env("SHOPIFY_API_VERSION", default="2025-01", required=False)
    url = f"https://{store}/admin/api/{version}/graphql.json"
    body = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "X-Shopify-Access-Token": token,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass
        raise RuntimeError(f"shopify_http_{e.code}: {body}") from e
    if data.get("errors"):
        raise RuntimeError(f"shopify_gql_errors: {json.dumps(data['errors'])[:500]}")
    return data.get("data") or {}


SEARCH_QUERY = """
query Search($q: String!, $first: Int!) {
  products(first: $first, query: $q) {
    edges {
      node {
        id
        title
        handle
        status
        totalInventory
        onlineStorePreviewUrl
        priceRangeV2 { minVariantPrice { amount currencyCode } maxVariantPrice { amount currencyCode } }
        featuredImage { url altText }
        variants(first: 5) {
          edges { node { id title sku price inventoryQuantity availableForSale } }
        }
      }
    }
  }
}
"""

PRODUCT_QUERY = """
query Product($handle: String, $id: ID) {
  product(handle: $handle, id: $id) {
    id
    title
    handle
    status
    totalInventory
    descriptionHtml
    onlineStorePreviewUrl
    priceRangeV2 { minVariantPrice { amount currencyCode } maxVariantPrice { amount currencyCode } }
    images(first: 5) { edges { node { url altText } } }
    variants(first: 25) {
      edges { node { id title sku price inventoryQuantity availableForSale selectedOptions { name value } } }
    }
    metafields(first: 25) { edges { node { namespace key value type } } }
  }
}
"""


def search(q: str, limit: int = 10) -> list[dict]:
    data = _gql(SEARCH_QUERY, {"q": q, "first": min(max(limit, 1), 25)})
    edges = ((data.get("products") or {}).get("edges")) or []
    return [e["node"] for e in edges]


def product_by_handle(handle: str) -> dict | None:
    data = _gql(PRODUCT_QUERY, {"handle": handle, "id": None})
    return data.get("product")


def product_by_id(numeric_id: str) -> dict | None:
    gid = numeric_id if numeric_id.startswith("gid://") else f"gid://shopify/Product/{numeric_id}"
    data = _gql(PRODUCT_QUERY, {"id": gid, "handle": None})
    return data.get("product")


DRAFT_ORDER_CREATE_MUTATION = """
mutation DraftCreate($input: DraftOrderInput!) {
  draftOrderCreate(input: $input) {
    draftOrder {
      id
      name
      invoiceUrl
      totalPriceSet { shopMoney { amount currencyCode } }
      lineItems(first: 25) {
        edges {
          node {
            title
            quantity
            originalUnitPriceSet { shopMoney { amount currencyCode } }
            variant { id title sku }
          }
        }
      }
    }
    userErrors { field message }
  }
}
"""


def _variant_gid(vid: str) -> str:
    return vid if vid.startswith("gid://") else f"gid://shopify/ProductVariant/{vid}"


def draft_order_create(
    line_items: list[tuple[str, int]],
    phone: str | None = None,
    email: str | None = None,
    note: str | None = None,
    source_channel: str = "whatsapp_direct",
    extra_attrs: list[tuple[str, str]] | None = None,
) -> dict:
    """Create a draft order. DRY_RUN=true (default) -> print-only, no API call."""
    attrs = [
        {"key": "source_channel", "value": source_channel},
        {"key": "created_by", "value": "sara-dsc"},
    ]
    for k, v in (extra_attrs or []):
        if k not in ("source_channel", "created_by"):
            attrs.append({"key": k, "value": v})
    draft_input: dict = {
        "lineItems": [
            {"variantId": _variant_gid(vid), "quantity": qty} for vid, qty in line_items
        ],
        "customAttributes": attrs,
        "tags": ["sara-dsc", "whatsapp-lead"],
    }
    if phone:
        draft_input["phone"] = phone
    if email:
        draft_input["email"] = email
    if note:
        draft_input["note"] = note

    dry_run = (os.environ.get("DRY_RUN", "true").lower() == "true")
    if dry_run:
        # Mirror gallabox.py send: never touch the live store in dry-run.
        return {"action": "DRY_RUN", "would_create": draft_input}

    data = _gql(DRAFT_ORDER_CREATE_MUTATION, {"input": draft_input})
    payload = (data.get("draftOrderCreate") or {})
    errors = payload.get("userErrors") or []
    if errors:
        raise RuntimeError(f"draft_order_user_errors: {json.dumps(errors)[:500]}")
    d = payload.get("draftOrder") or {}
    total = ((d.get("totalPriceSet") or {}).get("shopMoney") or {})
    items = []
    for e in ((d.get("lineItems") or {}).get("edges") or []):
        n = e.get("node") or {}
        unit = ((n.get("originalUnitPriceSet") or {}).get("shopMoney") or {})
        items.append({
            "title": n.get("title"),
            "quantity": n.get("quantity"),
            "unit_price": unit.get("amount"),
            "currency": unit.get("currencyCode"),
            "variant": n.get("variant"),
        })
    return {
        "id": d.get("id"),
        "name": d.get("name"),
        "invoiceUrl": d.get("invoiceUrl"),
        "totalPrice": total.get("amount"),
        "currency": total.get("currencyCode"),
        "lineItems": items,
    }


def _print(obj) -> None:
    json.dump(obj, sys.stdout, ensure_ascii=False, default=str)
    sys.stdout.write("\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search")
    s.add_argument("query")
    s.add_argument("--limit", type=int, default=10)

    s = sub.add_parser("product_by_handle")
    s.add_argument("handle")

    s = sub.add_parser("product_by_id")
    s.add_argument("id")

    s = sub.add_parser("draft_order_create")
    s.add_argument("--line-item", action="append", required=True,
                   metavar="VARIANT_ID:QTY",
                   help="Repeatable. Variant id (numeric or gid) and quantity, colon-separated.")
    s.add_argument("--phone", default=None)
    s.add_argument("--email", default=None)
    s.add_argument("--note", default=None)
    s.add_argument("--source-channel", default="whatsapp_direct",
                   choices=["whatsapp_direct", "whatsapp_widget_google", "meta_ctwa"])
    s.add_argument("--attr", action="append", default=[], metavar="KEY=VALUE",
                   help="Repeatable extra customAttributes.")

    args = ap.parse_args()
    try:
        if args.cmd == "search":
            _print(search(args.query, args.limit))
        elif args.cmd == "product_by_handle":
            _print(product_by_handle(args.handle))
        elif args.cmd == "product_by_id":
            _print(product_by_id(args.id))
        elif args.cmd == "draft_order_create":
            items: list[tuple[str, int]] = []
            for raw in args.line_item:
                vid, _, qty = raw.rpartition(":")
                if not vid or not qty.isdigit() or int(qty) < 1:
                    raise RuntimeError(f"bad_line_item:{raw} (expected VARIANT_ID:QTY)")
                items.append((vid, int(qty)))
            extra = []
            for raw in args.attr:
                k, _, v = raw.partition("=")
                if not k or not v:
                    raise RuntimeError(f"bad_attr:{raw} (expected KEY=VALUE)")
                extra.append((k, v))
            _print(draft_order_create(
                items,
                phone=args.phone,
                email=args.email,
                note=args.note,
                source_channel=args.source_channel,
                extra_attrs=extra,
            ))
    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
