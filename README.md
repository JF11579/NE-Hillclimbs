# Northeast Hill Climbs

Personal tracker for Northeast bike hill climbs (the BUMPS series plus a few others):
race days, registration windows, course stats, and links.

- **Site:** static `index.html` that reads `data/races.json` and `data/live.json`. Hosted on Netlify, which redeploys on every push.
- **`data/races.json`:** hand-curated race facts plus past seasons' dates. Edit this to add or remove a race.
- **`data/live.json`:** written by the daily checker; don't hand-edit it.
- **`scripts/check_races.py`:** runs daily via `.github/workflows/check.yml`. Queries BikeReg's public search API (and Movemint for Prospect), then opens a GitHub issue for each alert. GitHub emails each issue to the repo owner. Alerts fire when next year's race is posted, when registration opens, 7 days before it closes, when dates change, and when an untracked Northeast hill climb appears.

Run it by hand with `python3 scripts/check_races.py --dry-run` (prints alerts, changes nothing), or trigger it from GitHub with `gh workflow run check.yml`.

Dates shown as "≈" are estimates projected from last year. They're replaced automatically once a race posts real dates.
