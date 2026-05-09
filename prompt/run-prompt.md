# Per-Tick Workflow

Execute these steps in order, then stop. The runner is destroyed when the
job finishes — there is no continuation.

---

## STEP 1 — list unassigned conversations

```
python src/gallabox.py list_open_unassigned --limit 100
```

## STEP 2 — for each conversation (cap at MAX_THREADS_PER_TICK = 10)

### a. Fetch the last WhatsApp inbound

```
python src/gallabox.py last_actionable <cid>
```

### b. Run the deterministic filter

```
python src/filters.py classify "<body>"
```

If `actionable=false`, `supplier_pitch=true`, or `complaint=true`: skip
and write an audit row with `decision=SKIPPED reason=<...>`.

### c. Sensitive-keyword pre-check

```
python src/qc.py pre_check "<body>"
```

If `escalate=true`: assign the conversation to `$ESCALATION_USER_ID`,
post a Telegram escalate note, write audit
`decision=QC_SENSITIVE_ESCALATE`. Do NOT draft a reply.

### d. Shopify lookup (if customer asked about a specific product)

```
python src/shopify.py search "<query>" > /tmp/shopify_<cid>.json
```

Use this file when you draft so QC can cross-check prices.

### e. Draft a reply

Follow the rules in `prompt/sara-system.md` "Hard quality rules" and
quote shipping / hours / payment from `prompt/brand-snippet.md`.

Write the draft to `/tmp/draft_<cid>.txt`.

### f. Run Layer-1 QC on the draft

```
python src/qc.py post_check \
  --body-file /tmp/draft_<cid>.txt \
  --shopify-file /tmp/shopify_<cid>.json \
  --shipping-file prompt/brand-snippet.md \
  --credentials-file prompt/brand-snippet.md
```

If `pass=false`: redraft once with the hint, re-run QC. Max 3 attempts.
After 3 fails: post `telegram.py escalate` and write audit
`decision=QC_BLOCKED`. Do not send.

### g. Re-check the conversation is still unassigned

```
python src/gallabox.py last_actionable <cid>
```

If the sender is now an internal user, skip — a human picked up.

### h. Send the reply

```
python src/gallabox.py send <cid> <e164_phone> "<reply>"
```

The send helper honours `$DRY_RUN`:
- `DRY_RUN=true`  — logs `would_send` to stdout, does NOT contact the
  customer. You must also post the draft to the review channel via
  `telegram.py review`.
- `DRY_RUN=false` — dispatches to Gallabox; the customer receives the
  message.

### i. Write an audit row

```
python src/audit.py write_audit <cid> SENT|SENT_DRY_RUN \
  "<reason>" --meta '{"body":"<reply>","attempts":<n>,"run_id":"<github.run_id>"}'
```

---

## STEP 3 — output the tick summary

After processing all threads, output exactly one line:

```
[TICK_SUMMARY] processed=<N> sent=<S> dry_run_drafts=<D> skipped=<K> escalated=<E> qc_blocked=<Q>
```

Then stop.
