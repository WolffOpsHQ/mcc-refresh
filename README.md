# mcc-refresh

Hourly data refresh for the static dashboard at https://mcc-review.pages.dev

`build.py` fetches public feeds (news wires, FX, crypto, SEC EDGAR, Bluesky public API, GDELT events export), assembles a snapshot, injects it into `template.html`, and the workflow deploys `dist/` to Cloudflare Pages by direct upload. No history is stored anywhere.

Cadence: hourly at minute 17. To switch to every 3 hours, change the cron in `.github/workflows/refresh.yml` to `17 */3 * * *`.
