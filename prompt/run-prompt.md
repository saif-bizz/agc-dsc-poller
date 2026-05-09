# Per-Tick Workflow

Execute these steps in order, then stop. The runner is destroyed when the
job finishes — there is no continuation.

---

## STEP 1 — list unassigned conversations

```
python src/gallabox.py list_open_unassigned --limit 100
```

## STEP 2 — for each conversation (cap at MAX_THREADS_PER_TICK = 10)

### a. Fetch the recent WhatsApp thread (oldest -> newest, up to 10 msgs)

```
python src/gallabox.py thread <cid> --limit 10 > /tmp/thread_<cid>.json
```

This gives you full conversational context — earlier messages, agent
replies, and any attachments. **Do not draft from the last message
alone — read the whole thread to understand intent.** A customer who
says "9 plants" in their last message has likely set the actual intent
in earlier messages (e.g. "this is for a hotel lobby, here are the
pots my client picked").

### b. For every attachment in the thread, download and look at it

For each row in the thread JSON where `media_path` is non-null AND
`media_type` is in (`image`, `document`, `video`):

```
python src/gallabox.py download_media "<media_path>" "/tmp/<cid>-att<N>.<ext>"
```

Then use the `Read` tool on the file. You have vision — actually look
at the image / read the PDF / scan the booth design. Identify what's
in it. **Never write "image received" or "document received" to the
customer** (see sara-system.md hard rule 1).

If an image is clearly a supplier-pitch flyer (text-on-background ad,
"we provide X services", phone numbers + price list, no actual
product the customer wants from us) → skip with `[SKIP:supplier_pitch]`.

If an image is a product the customer is asking us to identify or
match (a pot, a plant, a render of a setting) → identify it, then
proceed to step (d) Shopify lookup with the identified term.

### c. Determine the latest customer message + run the deterministic filter

The newest `role=customer` row in the thread is the message you must
respond to. Run the text filter on its body:

```
python src/filters.py classify "<body>"
```

If `actionable=false`, `supplier_pitch=true`, or `complaint=true`: skip
and write an audit row with `decision=SKIPPED reason=<...>`.

### c2. Sensitive-keyword pre-check (refund/complaint/etc.)

```
python src/qc.py pre_check "<body>"
```

If `escalate=true`: assign the conversation to `$ESCALATION_USER_ID`,
post a Telegram escalate note, write audit
`decision=QC_SENSITIVE_ESCALATE`. Do NOT draft a reply.

### d. Shopify lookup (if a specific product was mentioned or seen in an attachment)

```
python src/shopify.py search "<query>" > /tmp/shopify_<cid>.json
```

`<query>` should be the SPECIFIC product Sara identified — either from
the customer's text OR from looking at an attached image (e.g. "gold
metallic pot 30cm" or "olive tree 1.0-1.5m"). Use this file when you
draft so QC can cross-check prices.

### e. Draft a reply

Follow the rules in `prompt/sara-system.md` "Hard quality rules" and
quote shipping / hours / payment from `prompt/brand-snippet.md`.

For open-ended B2B/project inquiries (Madushan-style: design PDF +
"we need plants for an event"), apply hard rule 12 from
sara-system.md — lead with positioning + ask for specifics, do not
bridge first.

For project recommendations where the customer has given context but
no specific plant (Romany-style: "9 plants for a lobby" + lobby
render images), use Shopify search to recommend a fitting in-stock
plant (e.g. Areca Palm for indoor lobby, Olive for villa entrance) —
do NOT just say "let me check with the team".

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
