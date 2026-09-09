# Official reference record

Retrieval/check date: **2026-09-09 UTC**. URLs must be reverified immediately before authenticated market-data or broker implementation. The app currently makes no Alpaca request.

## OpenAI-compatible profiles

OpenAI official documentation returned HTTP 403 to this environment on the retrieval date; no claim of inspecting its content is made.

- API reference: <https://platform.openai.com/docs/api-reference>
- Chat Completions: <https://platform.openai.com/docs/api-reference/chat>
- Responses: <https://platform.openai.com/docs/api-reference/responses>
- Models: <https://platform.openai.com/docs/api-reference/models>

Implementation supports only explicitly selected Chat Completions or Responses request shapes. It does not promise every “OpenAI-compatible” service supports those shapes or optional features.

## Alpaca official documentation

The following official pages resolved successfully, including redirects to their current US documentation paths:

- Trading API: <https://docs.alpaca.markets/us/docs/trading-api>
- Authentication reference: <https://docs.alpaca.markets/us/reference/authentication-2>
- Market Data API reference: <https://docs.alpaca.markets/docs/api-references/market-data-api/>
- Streaming market data: <https://docs.alpaca.markets/us/docs/streaming-market-data>
- Working with orders: <https://docs.alpaca.markets/us/docs/working-with-orders>
- Calendar reference: <https://docs.alpaca.markets/reference/calendar-1>
- Corporate actions: <https://docs.alpaca.markets/docs/corporate-actions>
- Historical bars: <https://docs.alpaca.markets/reference/stockbars>
- SDK docs: <https://docs.alpaca.markets/docs/sdks-and-tools>

Before enabling market data, verify authentication headers, current REST/WebSocket hosts, subscription message shape, feed choice (`iex`, `sip`, delayed/SIP where offered), entitlement behavior, rate/subscription limits, timestamp/timezone semantics, corrections/cancels, reconnect rules, symbol status, calendars, corporate actions, and data licensing.

Before enabling broker paper/live, verify separate paper/live base URLs and credentials, account environment identity, asset tradability/fractionability, order lifecycle, client order IDs, timeout/unknown outcomes, replacements, cancels, partial fills, streaming reconciliation, buying power, market hours, extended-hours restrictions, fractional-order restrictions, PDT/regulatory behavior, and current rate limits.

No entitlement, endpoint, or rate-limit value is hard-coded because no authenticated adapter exists. The product never labels unavailable data “real-time” and never falls back between delayed, paper, or live modes.

## Results video

- Requested reference: <https://www.youtube.com/watch?v=f9GO0ZEaCmI&t=308s>

The HTML page was reachable. Browser automation/video playback was unavailable. The segment was not watched. UI decisions derive from the user-provided results requirements, not a claimed visual match.
