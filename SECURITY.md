# Security

FLIP RADAR is a **self-hosted, single-tenant** tool. It holds your purchase and sales history, the
credentials for optional third-party APIs, and the market data it has collected. This document
describes the threat model, the controls in place and how to deploy safely.

## Reporting a vulnerability

Open a private security advisory on the GitHub repository, or contact the maintainer directly. Do not
file public issues for vulnerabilities.

## Threat model

| Asset | Threat | Control |
|---|---|---|
| API (inventory, settings, keys in use) | Unauthorized access from the LAN or internet | Every `/api/v1` route requires a bearer token by default (`FLIPRADAR_REQUIRE_AUTH=true`). Tokens are 256-bit random (`fr_` + `secrets.token_urlsafe(32)`). Only the SHA-256 hash is stored, and the plain token is shown once. Tokens can be revoked. |
| API | Brute force / abuse | An in-memory sliding-window rate limiter returns 429 with `Retry-After`. Requests carrying a valid token are limited per token; anonymous or invalid requests are limited per client IP. Behind a reverse proxy, set `FLIPRADAR_TRUSTED_PROXIES` (IPs/CIDRs). `X-Forwarded-For` is honoured only from those peers, using the right-most untrusted hop. |
| API | Memory or disk exhaustion through large bodies | `BodySizeLimitMiddleware` rejects a `Content-Length` above the limit with 413 before reading, and caps streamed bodies. The limit is 256 KB by default and 5 MB + 64 KB for CSV import. Uploads over 256 KB need a valid token before any byte is buffered. |
| Local no-auth mode | CSRF / DNS rebinding from a malicious website | Only when `FLIPRADAR_REQUIRE_AUTH=false`: the Host header must be localhost, 127.0.0.1, [::1] or a host in `FLIPRADAR_ALLOWED_HOSTS`. State-changing requests with a cross-site `Origin` (or `Sec-Fetch-Site: cross-site`) get 403. |
| Web dashboard | XSS, clickjacking | Strict CSP (`default-src 'self'`, no inline scripts or styles, no CDNs), `X-Frame-Options: DENY`, `frame-ancestors 'none'`, `nosniff`, `Referrer-Policy: no-referrer`. The UI renders all server or marketplace strings with `textContent`, never `innerHTML`. |
| Server | SSRF through the "check URL" feature | Only http/https, no credentials in URLs, standard ports only. The hostname is resolved and private, loopback, link-local, multicast and reserved IPs are rejected, including after every redirect (max 3). The connect-time address is re-checked to prevent DNS rebinding. The body is capped at 2 MB **after decompression**; only one gzip/deflate layer is accepted, which defeats decompression bombs. Only HTML is accepted. `robots.txt` is honored. Domains whose terms forbid automation are refused. Refusals return HTTP 400 with a generic message, so internal DNS answers are never revealed. |
| Server | Malicious uploads | CSV import is limited to 5 MB and 20,000 rows, is parsed with the stdlib `csv` module, and is never executed. A bad cell (ambiguous decimal separator, out-of-range number, a field over 2,000 characters) rejects only its row. Every money field accepted from clients or files is capped at 10^12 centavos to rule out integer overflow. RSS and XML are parsed with `defusedxml`, which blocks XXE and billion-laughs attacks. Hostile JSON-LD (deep nesting, absurd prices) is ignored instead of crashing. |
| Third-party API keys | Leakage | Keys are read from environment variables or `.env` and never appear in API responses. `.env` is git-ignored. A log filter redacts the Telegram bot token, the webhook URL and the ntfy topic URL from HTTP client logs. Mercado Libre refresh tokens are single-use. The rotated token is stored in `app_settings` (`secret.ml_refresh_token`), never returned by the API, written in its own short transaction off the event loop, and saved again at shutdown. Treat the SQLite file as a secret. |
| Android device | Token theft from storage, or token sent to the wrong server | The token is encrypted with an AES-GCM key from the Android Keystore and never logged. It is bound to the server URL: changing the URL deletes it, so it can never be sent to another host. Redirects are not followed. |
| Network | Eavesdropping | The Android app allows cleartext HTTP because many users run the server on a LAN. The setup screen warns when a non-private address is used over `http://`. **Use HTTPS (reverse proxy) or a private overlay network such as Tailscale/WireGuard for anything beyond your home LAN.** |
| Data integrity | AI hallucination or prompt injection in recommendations | AI is optional and off by default. An AI summary is stored only if every number in it appears among the **trusted numeric facts** with the same sign. Free text such as titles, descriptions and seller names never whitelists a number, and claims like "sin riesgos" are rejected when risk flags exist. Otherwise the summary is discarded. |
| Legal | ToS violations | Only official APIs, user-chosen feeds and user-initiated page checks are used. There is no scraping behind anti-bot protection. See `docs/DATA_SOURCES.md`. |

## Deployment checklist

1. Run `python -m flipradar init`, then `python -m flipradar create-token --name phone`. Store the
   token in a password manager.
2. Bind to `127.0.0.1` (the default) unless you need LAN access. For LAN access use
   `--host 0.0.0.0` behind your router's firewall. **Never expose the port directly to the internet.**
   Use a reverse proxy with TLS (Caddy or nginx) or Tailscale.
3. Keep `FLIPRADAR_REQUIRE_AUTH=true`. Only disable it for local development.
4. Set `FLIPRADAR_CORS_ORIGINS` only if you host the dashboard on another origin.
5. Back up `backend/data/flipradar.db` (SQLite). It contains your inventory and sales history.
6. Rotate tokens with `python -m flipradar revoke-token --prefix fr_xxxx`, then create a new one.
7. If you enable an `openai_compatible` LLM pointing to a hosted provider, collected listing data is
   sent to that provider. Prefer the local Ollama option.

## Known limitations

* The rate limiter is in-memory and per process. It resets on restart and is not shared across workers.
  Add a request-body size limit at your reverse proxy too (e.g. Caddy `request_body { max_size 6MB }`).
* There are no user accounts or roles: any valid token has full access, which suits a single-tenant tool.
* SQLite is not encrypted at rest. Use full-disk encryption on the host.
* The Android debug APK built by `build-offline.sh` is signed with a locally generated debug key. For
  distribution, sign a release build with your own key (see `android/README.md`).
