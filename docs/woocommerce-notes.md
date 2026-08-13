# WooCommerce review notes

## HPOS

High-Performance Order Storage moves orders out of the posts tables. Code that
reads order data with `get_post_meta` or queries `wp_posts` directly is unsafe:
compatibility-mode synchronisation may be disabled, incomplete, stale, or absent
entirely, so such reads can return nothing or return outdated values. Use the
WooCommerce CRUD APIs, which read whichever store is authoritative.

Declaring compatibility is not the same as being compatible. Check:

- `FeaturesUtil::declare_compatibility('custom_order_tables', ...)` is present
- every order read goes through `WC_Order` / the CRUD API
- order queries use `wc_get_orders`, not `WP_Query`
- meta access uses `$order->get_meta()`, not `get_post_meta( $order_id, ... )`
- admin list-table filters hook the HPOS-aware equivalents

## Blocks

A gateway that works on the shortcode checkout can be absent from the Blocks
checkout entirely. Confirm the block payment method is registered, and that its
client-side data matches what the server expects.

## Order lifecycle

The failure modes worth tracing:

- status transitions that assume a linear path
- guest orders, where there is no customer ID to key from
- partial refunds and refund synchronization back to the gateway
- cancellation racing against a gateway callback
- stock reduction on an order that later fails payment

## Payment gateways

- **Authorization vs capture** — code that assumes capture happened at
  authorization time
- **Duplicate transactions** — a retried request that charges twice because the
  idempotency key is derived from something that changes per attempt
- **Callback and webhook replay** — a handler that is not idempotent, or that
  trusts the payload without verifying its signature
- **3DS failure paths** — the abandoned and timed-out branches, not the success one
- **Token lifecycle** — a saved payment method outliving the customer record, or
  a token reused after the gateway invalidated it
- **AVS/CVD handling** — a decline treated as an error, or an error treated as a
  decline

## Scale

- order tables large enough that an unindexed meta query times out
- background processing that loads every matching order into memory
- API pagination that stops at the first page
- rate limits hit during a bulk sync, and whether the retry is bounded

## Cross-tool note

Plugin Check runs WPCS sniffs internally, so its findings overlap heavily with
PHPCS. The renderer merges them; two tools reporting one line is corroboration,
not two problems.
