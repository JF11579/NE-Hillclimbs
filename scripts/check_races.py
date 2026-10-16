#!/usr/bin/env python3
"""Daily check for Northeast hill climb registrations.

Queries BikeReg's public search API (and Movemint for races that moved there),
records what it finds in data/live.json, and opens a GitHub issue for each
alert. GitHub emails the issue to the repo owner, so each issue is an email:

  - a race posts next season's event (dates announced)
  - registration opens
  - registration closes within 7 days
  - a race's date or registration dates change
  - a Northeast hill climb shows up that isn't in races.json yet

Usage:  python scripts/check_races.py            # update live.json + open issues
        python scripts/check_races.py --dry-run  # print alerts, touch nothing
        python scripts/check_races.py --test-alert  # open one test issue (email)
"""
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RACES = ROOT / "data" / "races.json"
LIVE = ROOT / "data" / "live.json"

NE_STATES = {"NH", "VT", "ME", "MA", "NY", "NJ", "CT", "RI"}
QUERIES = ["climb", "uphill", "whiteface", "rock pile", "killington"]
CLIMB_RE = re.compile(r"hill ?climb|uphill|climb challenge", re.I)
CLOSING_DAYS = 7
SITE = "https://ne-hillclimbs-jf.netlify.app"
UA = "Mozilla/5.0 (personal hill-climb calendar; github.com/JF11579)"
OWNER = "@JF11579"


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status, resp.read().decode("utf-8", "replace")


def ms_date(s):
    """BikeReg dates look like /Date(1777694400000-0400)/ -> 'YYYY-MM-DD' (local)."""
    if not s:
        return None
    m = re.search(r"(-?\d+)([+-]\d{4})?", s)
    ts = int(m.group(1)) / 1000
    off = m.group(2)
    if off:
        sign = 1 if off[0] == "+" else -1
        ts += sign * (int(off[1:3]) * 3600 + int(off[3:5]) * 60)
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%d")


def search_bikereg(years):
    events, ok = {}, 0
    for year in years:
        for q in QUERIES:
            url = f"https://www.bikereg.com/api/search?year={year}&name={urllib.request.quote(q)}"
            try:
                _, body = fetch(url)
                data = json.loads(body)
                ok += 1
            except Exception as e:  # Cloudflare challenge pages aren't JSON
                print(f"  BikeReg query failed ({year} '{q}'): {e!s:.120}")
                continue
            for e in data.get("MatchingEvents", []):
                if e.get("EventState") in NE_STATES:
                    events[e["EventId"]] = e
            time.sleep(5)
    return events, ok


def summarize(e):
    fees = sorted({c["EntryFee"] for c in e.get("Categories", []) if c.get("EntryFee")})
    return {
        "eventId": e["EventId"],
        "name": e["EventName"],
        "date": ms_date(e.get("EventDate")),
        "regOpen": ms_date(e.get("RegOpenDate")),
        "regClose": ms_date(e.get("RegCloseDate")),
        "register": f"https://www.bikereg.com/{e['EventId']}",
        "website": e.get("EventWebsite") or None,
        "town": e.get("EventCity"),
        "state": e.get("EventState"),
        "fees": [round(f) for f in fees],
        "source": "bikereg",
    }


def check_movemint(race, year):
    slug = race["movemint"].format(year=year)
    url = f"https://www.movemint.cc/events/{slug}"
    try:
        status, body = fetch(url)
    except Exception:
        return None
    if status != 200 or race["name"].split()[0].lower() not in body.lower():
        return None
    return {"name": race["name"], "register": url, "source": "movemint"}


def email_subscribers(new, dry_run, status="about_to_send"):
    """Send one Buttondown email with all of today's alerts to the public sign-up list.
    Skipped unless the BUTTONDOWN_API_KEY secret is set."""
    key = os.environ.get("BUTTONDOWN_API_KEY")
    if not new or not key:
        return True
    subject = new[0][1] if len(new) == 1 else f"{len(new)} Northeast hill climb updates"
    body = "\n\n".join(f"## {title}\n\n{text}" for _, title, text in new)
    body += f"\n\n---\n\nAll races and dates: {SITE}"
    print(f"Buttondown ({status}): {subject}")
    if dry_run:
        return True
    req = urllib.request.Request(
        "https://api.buttondown.com/v1/emails", method="POST",
        data=json.dumps({"subject": subject, "body": body, "status": status}).encode(),
        headers={"Authorization": f"Token {key}", "Content-Type": "application/json",
                 "X-Buttondown-Live-Dangerously": "true"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            print(f"  Buttondown accepted: HTTP {resp.status}")
        return True
    except urllib.error.HTTPError as e:
        # Print Buttondown's reason; a bare status code hides the actionable message.
        print(f"  Buttondown error {e.code}: {e.read().decode('utf-8', 'replace')}")
    except urllib.error.URLError as e:
        print(f"  Buttondown unreachable: {e}")
    return False


def open_issue(title, body, dry_run):
    print(f"ALERT: {title}")
    if dry_run:
        print("   " + body.replace("\n", "\n   "))
        return
    subprocess.run(["gh", "issue", "create", "--title", title, "--body", body], check=True)


def fmt(d):
    return dt.date.fromisoformat(d).strftime("%a %b %-d, %Y") if d else "TBA"


def main():
    dry_run = "--dry-run" in sys.argv
    if "--test-alert" in sys.argv:
        open_issue("🧪 Test alert from the hill-climb checker",
                   "If this arrived by email, alerts are working. You can close this issue."
                   f"\n\n---\n{OWNER} · sent by the daily hill-climb checker", dry_run)
        # Subscribers get a draft only (visible in the Buttondown dashboard, never sent).
        email_subscribers([("test", "🧪 Test: Northeast hill climb alert",
                            "This is a draft created by the test run. Delete it in Buttondown.")],
                          dry_run, status="draft") or sys.exit(1)
        return
    today = dt.date.today()
    races = json.loads(RACES.read_text())["races"]
    mailed = True
    first_run = not LIVE.exists()
    live = json.loads(LIVE.read_text()) if not first_run else {"events": {}, "otherClimbs": {}, "sent": []}
    sent = set(live["sent"])
    alerts = []  # (key, title, body)

    years = [today.year, today.year + 1]
    print(f"Checking BikeReg for {years} ...")
    found, ok = search_bikereg(years)
    if ok == 0:
        print("Every BikeReg query failed; leaving live.json untouched.")
        sys.exit(1)

    matched_ids = set()
    for race in races:
        pat = re.compile(race["match"], re.I)
        for e in found.values():
            if not pat.search(e["EventName"]):
                continue
            info = summarize(e)
            year = (info["date"] or "")[:4]
            if not year:
                continue
            matched_ids.add(e["EventId"])
            prev = live["events"].get(race["id"], {}).get(year)
            # A hand-entered date (e.g. from an organizer email) without reg dates still
            # counts as "not posted", so we alert once BikeReg has the real event.
            known = prev is not None or "regOpen" in race.get("history", {}).get(year, {})
            if not known:
                alerts.append((f"{race['id']}-{year}-announced",
                               f"📅 {race['name']} {year} is posted",
                               f"**Race day:** {fmt(info['date'])}\n"
                               f"**Registration opens:** {fmt(info['regOpen'])}\n"
                               f"**Registration closes:** {fmt(info['regClose'])}\n\n"
                               f"Register: {info['register']}"))
            elif prev and prev.get("source") == "bikereg":
                changes = [f"- {k}: {fmt(prev.get(k))} → **{fmt(info[k])}**"
                           for k in ("date", "regOpen", "regClose") if prev.get(k) != info[k]]
                if changes:
                    alerts.append((f"{race['id']}-{year}-changed-{info['date']}-{info['regOpen']}-{info['regClose']}",
                                   f"✏️ {race['name']} {year}: dates changed",
                                   "\n".join(changes) + f"\n\nRegister: {info['register']}"))
            live["events"].setdefault(race["id"], {})[year] = info

        if race.get("movemint"):
            for year in map(str, years):
                if year in live["events"].get(race["id"], {}):
                    continue
                info = check_movemint(race, year)
                if info:
                    live["events"].setdefault(race["id"], {})[year] = info
                    if year not in race.get("history", {}):
                        alerts.append((f"{race['id']}-{year}-announced",
                                       f"📅 {race['name']} {year} is posted (Movemint)",
                                       f"Registration page is up: {info['register']}"))

    # Registration open / closing soon, for every event we know about.
    for race in races:
        for year, info in live["events"].get(race["id"], {}).items():
            ro, rc = info.get("regOpen"), info.get("regClose")
            if not (ro and rc):
                continue
            ro, rc = dt.date.fromisoformat(ro), dt.date.fromisoformat(rc)
            if ro <= today <= rc:
                alerts.append((f"{race['id']}-{year}-open",
                               f"🟢 Registration OPEN: {race['name']} {year}",
                               f"**Race day:** {fmt(info['date'])}\n"
                               f"**Closes:** {fmt(info['regClose'])}\n\nRegister: {info['register']}"))
            if 0 <= (rc - today).days <= CLOSING_DAYS:
                alerts.append((f"{race['id']}-{year}-closing",
                               f"⏰ Registration closes {fmt(info['regClose'])}: {race['name']} {year}",
                               f"Only {(rc - today).days} day(s) left.\n\nRegister: {info['register']}"))

    # Northeast climbs we don't track yet.
    for e in found.values():
        if e["EventId"] in matched_ids or not CLIMB_RE.search(e["EventName"]):
            continue
        info = summarize(e)
        key = str(e["EventId"])
        if key not in live["otherClimbs"]:
            alerts.append((f"other-{key}",
                           f"🆕 New Northeast hill climb on BikeReg: {info['name']}",
                           f"**Where:** {info['town']}, {info['state']}\n**Race day:** {fmt(info['date'])}\n"
                           f"**Registration:** {fmt(info['regOpen'])} – {fmt(info['regClose'])}\n\n"
                           f"{info['register']}\n\n"
                           "_Not in races.json yet — add it there if you want it on the site._"))
        live["otherClimbs"][key] = info

    new = [a for a in alerts if a[0] not in sent]
    if first_run:
        print(f"First run: seeded live.json, suppressing {len(new)} alert(s).")
    else:
        for key, title, body in new:
            open_issue(title, f"{body}\n\n---\n{OWNER} · sent by the daily hill-climb checker", dry_run)
        mailed = email_subscribers(new, dry_run)
    sent.update(k for k, _, _ in new)
    live["sent"] = sorted(sent)
    live["checked"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes")

    if not dry_run:
        LIVE.write_text(json.dumps(live, indent=2, sort_keys=True) + "\n")
    print(f"Done: {ok} queries ok, {len(found)} NE events seen, {len(new)} new alert(s).")
    if not mailed:
        sys.exit("Subscriber email failed (see Buttondown error above); GitHub issues were still opened.")


if __name__ == "__main__":
    main()
