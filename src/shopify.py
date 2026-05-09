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

    args = ap.parse_args()
    try:
        if args.cmd == "search":
            _print(search(args.query, args.limit))
        elif args.cmd == "product_by_handle":
            _print(product_by_handle(args.handle))
        elif args.cmd == "product_by_id":
            _print(product_by_id(args.id))
    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
