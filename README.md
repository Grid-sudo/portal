# Telegram Gift Monitor

Read-only Telegram monitoring bot. It defaults to `DEMO` mode so the bot, comparisons, database, notifications, and error journal can be exercised before any marketplace grants live access. Demo values are synthetic and are never represented as market quotes.

## Marketplace API and access audit

Checked against the official marketplace pages and documentation available on 30 October 2026. “Not found” means no public official listing API specification was located; it does not rule out a private partner API.

| Marketplace | Official API and documentation | Access requirements | Permitted data access | Use restrictions / current adapter |
| --- | --- | --- | --- | --- |
| MRKT | No public official API specification for Telegram gift listings was found. [Official MRKT site](https://www.mrkt.land/) and [official product news](https://www.mrkt.land/en/news). | Public API authentication requirements are not published in the material found. Use the official product account for the user interface. | For this project, no programmatic access is enabled. Add a read-only connector only after MRKT publishes a contract or authorizes one. | No third-party data terms for an API were located. No scraping or private endpoints. Adapter state: `UNAVAILABLE`. |
| Portals | **Yes: official Partners REST API.** [API guide](https://docs.portals.tg/ru/developers/api/) and [OpenAPI contract](https://docs.portals.tg/api/partners.en.json). Listing search: `GET /partners/nfts/search`; base URL `https://portal-market.com`. | Create a key in the Portals mini-app profile → Partner API. The key is shown once; reissuing immediately invalidates the old one. Select a plan in Partner API if required. The documented free tier is 1 request/second; paid tiers advertise 5 rps (50 GRAM/30 days) and 20 rps (100 GRAM/30 days). | This adapter only calls the documented GET listing search with `status=listed`, newest listings first, and paginates up to `PORTALS_MAX_PAGES` (default 5) at 1.05-second intervals. | The API key authorizes both read and write methods on Portals; the app stores it server-side and its adapter issues GET requests only. No purchase, listing, transfer, balance, or wallet calls are implemented. State is `NOT_CONFIGURED` without `PORTALS_API_KEY`, `READY` when configured, and `UNAVAILABLE` on request errors. |
| Tonnel | No official public API specification for marketplace gift listings was found. [Official marketplace](https://marketplace.tonnel.network/) and [official Tonnel docs](https://docs.tonnel.network/) were checked; the docs located describe other Tonnel products, not a listing feed. | No public programmatic access requirements were found; official marketplace access is through Tonnel's product. | No programmatic marketplace data source is enabled. Use only a published API or an operator-approved read feed if one becomes available. | API usage terms for a read-only integration were not located. No reverse-engineered endpoints, scraping, or auth workarounds. Adapter state: `UNAVAILABLE`. |

The three separate adapters all implement the same interface. In `LIVE`, MRKT and Tonnel report `UNAVAILABLE`; Portals reports `NOT_CONFIGURED` until a key is set, then uses the official GET search. In `DEMO`, the synthetic source remains clearly marked. Switch to `APP_MODE=LIVE` to analyze Portals data without DEMO samples.

## What works now

- `APP_MODE=DEMO` is the safe default. `DemoAdapter` returns five clearly marked sample rows with `DEMO` IDs, collection, and traits. The dataset is local and makes no HTTP calls.
- The sample has one synthetic gift listed at 35, 40, and 42 TON across venue labels, plus separate comparison gifts at 50 and 51 TON. The analyzer identifies cross-market gross spreads and collection comparisons; a 35 TON ask against a 50 TON reference is a 30% observed discount. For a 35 TON and 42 TON same-gift pair, it reports 7 TON gross spread and 16.7% gross margin on the 42 TON sale ask, before fees. All these values are invented test data.
- `DEMO` and `LIVE` records are isolated in PostgreSQL by mode. Floors, history, statistics, and opportunity keys are mode-scoped, so sample rows cannot pollute live analysis.
- Same-venue duplicate rows are collapsed by marketplace listing ID. The same gift ID is used only to compare listings across venues. Currencies are never implicitly converted.
- Portals monitoring uses only `GET /partners/nfts/search`, the documented `Authorization: partners <token>` header, listed-gift filtering, GRAM decimal prices, pagination, and a conservative 1.05-second request interval. The key is loaded from `PORTALS_API_KEY`; never put it in source code or Telegram messages.
- Telegram alerts are visibly prefixed `🧪 DEMO` and omit external listing links. A persistent notification outbox retries delivery failures up to five times and avoids sending the same alert twice to the same chat. `/start` subscribes and queues recent opportunities for that chat.
- Database startup applies ordered migrations to existing PostgreSQL tables before creating any missing tables. Existing listing and floor rows are marked `LIVE`; old opportunities inherit their collection from the matching listing, or `Unknown` when no listing remains.
- If Redis is temporarily unavailable, scans use a process-local lock. PostgreSQL upserts and unique notification keys prevent repeated listing/opportunity inserts; the Redis lock remains necessary to coordinate separate app processes.
- Errors go to stdout and a rotating file journal at `data/nft-monitor.log` (5 MB per file, three backups). The Compose file persists `data/` on the host.
- No buy/sell methods, wallet connection, transaction signing, private-key storage, or seed phrase fields exist.

## Bot commands

`/start`, `/stop`, `/status`, `/scan`, `/floor`, `/opportunities`, `/collections`, `/settings`, `/history`, and `/stats`.

In DEMO, run `/scan` to refresh the deterministic sample. Alerts are sent to chats that have used `/start`. The first `/start` also queues recent demo opportunities so an early scheduled scan does not consume the alerts before a chat subscribes.

## Run locally

Requirements: Python 3.12+, Docker Compose, and a bot token from Telegram's BotFather.

```sh
cp .env.example .env
# Set BOT_TOKEN in .env
docker compose up --build -d
docker compose logs -f bot
```

### macOS

Install and start Docker Desktop, then from the project directory run:

```sh
open -a Docker
docker info
cp .env.example .env
```

Set `BOT_TOKEN` and a unique URL-safe `POSTGRES_PASSWORD` in `.env`, then start the stack:

```sh
docker compose up --build -d
docker compose logs -f bot
```

Before the first PostgreSQL volume is created, generate a password without printing it to the terminal and update only that setting with:

```sh
python3 - <<'PY'
from pathlib import Path
import secrets

path = Path('.env')
lines = path.read_text().splitlines()
for index, line in enumerate(lines):
    if line.startswith('POSTGRES_PASSWORD='):
        current = line.split('=', 1)[1]
        if len(current) != 64 or any(char not in '0123456789abcdef' for char in current):
            lines[index] = 'POSTGRES_PASSWORD=' + secrets.token_hex(32)
        break
else:
    raise SystemExit('POSTGRES_PASSWORD setting not found')
path.write_text('\n'.join(lines) + '\n')
path.chmod(0o600)
PY
```

Once PostgreSQL has initialized its volume, changing `.env` alone does not rotate the database user's password. Keep the existing value or rotate both the PostgreSQL role and app configuration together.

To select `LIVE`, set `APP_MODE=LIVE` and restart. With current access, live scans store no marketplace listings and clearly report adapter states. Switching modes does not mix stored rows.

## Ubuntu VPS

1. Install Docker Engine and its Compose plugin using [Docker's official Ubuntu instructions](https://docs.docker.com/engine/install/ubuntu/).
2. Copy this project to the VPS and enter its directory.
3. Run `cp .env.example .env`, set `BOT_TOKEN`, and replace `POSTGRES_PASSWORD` with a unique URL-safe password.
4. Protect `.env` with `chmod 600 .env`.
5. Start the services using `docker compose up --build -d`.
6. Check startup with `docker compose ps` and `docker compose logs -f bot`.

PostgreSQL and Redis stay on the private Compose network; no service port is exposed publicly. Back up the `postgres_data` volume and the `data/` log directory as part of VPS operations.

## Tests and verification

Run unit tests with `python3 -m unittest discover -s tests -v`.

The unit suite verifies adapter states and provenance, Portals GET auth/pagination/GRAM/traits/rate-limit and Cloudflare-block handling, price and gross-margin arithmetic, currency separation, same-NFT comparison, LIVE adapter isolation, duplicate handling, fee-aware estimates, scan-lock recovery, migration ordering/idempotence, error-journal writes, and secret redaction. The latest local run passed all 26 tests. The migration SQL was also applied twice to a temporary PostgreSQL 14 legacy-schema fixture; existing rows were preserved, assigned `LIVE`, and floor keys were rewritten to the LIVE key format.

Docker verification completed on 30 October 2026 using PostgreSQL 16 and Redis 7. The scanner fetched five DEMO listings and persisted six opportunities. It recorded one simulated Telegram send error, recovered that pending outbox item after recreating the database connection, and sent no duplicate on a repeated scan. A LIVE scan fetched zero listings; there were zero LIVE listings and opportunities. With Redis stopped, a scan completed using the process-local lock; Redis returned `PONG` after restart. The current Docker image build and all 26 unit tests inside that image passed.

The scanner/outbox integration uses a local `RecordingBot` for the simulated send-failure and retry assertions. Separately, a real Telegram bot session reached long polling; after the account owner sent `/start`, Telegram accepted six DEMO alerts and the outbox recorded `sent: 6`. Restarting that bot resumed polling and left the six records sent, with no duplicates. A live read-only Portals GET was attempted from the configured container, but Cloudflare returned HTTP 403 `1010 browser_signature_banned` (`retryable=false`). This edge response does not confirm whether the API key or plan is valid. The adapter records that state and stops retrying until restart; obtain an approved server-client path from Portals support. No browser fingerprint spoofing or other block bypass is used.

The ordinary `docker compose up --build -d` stack was also launched and the bot was restarted. PostgreSQL and Redis became healthy, the bot resumed polling, and the persistent database retained five DEMO listings and six DEMO opportunities with no LIVE records or duplicate deliveries. This Compose database is clean and has no subscribers. The bot is currently stopped after the non-retryable Portals edge block; PostgreSQL and Redis remain healthy. Docker Desktop initially returned HTTP 500 for `docker info`; restarting Docker Desktop restored the daemon.

## What requires official access

Portals credentials are configured, but its edge currently blocks this server client with Cloudflare error 1010. Ask Portals support (official docs link to [@Portals_Help_Bot](https://t.me/Portals_Help_Bot) for partner API questions) for the approved server-side API access path or allowlisting. Do not spoof a browser fingerprint. Once Portals confirms access, restart the bot; the adapter uses the official GET API and free-tier 1 request/second limit with up to five pages per scan. MRKT and Tonnel still require published or operator-authorized APIs. Live fee schedules and sales history must be verified before showing net profit or liquidity-based valuations. Until then, arbitrage output is a gross ask-price spread, never a profit guarantee.
# portal
