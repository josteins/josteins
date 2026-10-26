#!/usr/bin/env python3
"""Weekly contribution ranking for the josteins profile.

For each person in PEOPLE (one or more GitHub accounts each) this collects GitHub's
contribution counts (public + private, as on the profile calendar) for the last
365 days, places each total on the committers.top "All contributions"
leaderboards, and, when NORVANA_READ_TOKEN is set, counts merged pull requests in
norvanaltd/Norvana per month. It writes:

  rankings/data/latest.json    this run's numbers
  rankings/data/history.json   one snapshot per ISO week, for the trend chart
  rankings/index.html          the chart page (GitHub Pages)
  rankings/card-light.svg      README card, light theme
  rankings/card-dark.svg       README card, dark theme
  README.md                    the block between the rankings markers

Standard library only. GITHUB_TOKEN is any token: private contribution counts are
read from the public profile calendar, so an account only counts its private work
if "Private contributions" is switched on in its profile settings.
"""
import datetime as dt
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

PEOPLE = [
    {"key": "jostein", "name": "Jostein", "accounts": ["josteins", "jostein-sven"]},
    # opt_in: shown only once the profile shows private contributions (GitHub's own consent switch).
    {"key": "sean", "name": "Sean", "accounts": ["seansvendsen"], "opt_in": True},
]
HERO = "jostein"
PR_REPO = "norvanaltd/Norvana"
BOARDS = [
    {"key": "norway", "label": "Norway", "url": "https://committers.top/norway_private"},
    {"key": "uk", "label": "UK", "url": "https://committers.top/uk_private"},
]
PAGE_URL = "https://josteins.github.io/josteins/rankings/"

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "rankings"
DATA = OUT / "data"
ALL_ACCOUNTS = [a for p in PEOPLE for a in p["accounts"]]


def http(url, body=None, headers=None, tries=4):
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, data=body, headers=headers or {})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read().decode("utf-8")
        except Exception as e:  # noqa: BLE001 - retried, then raised
            if attempt == tries - 1:
                raise
            print(f"retry {url}: {e}", file=sys.stderr)
            time.sleep(5 * (attempt + 1))


QUERY = """query($u:String!,$f:DateTime!,$t:DateTime!){user(login:$u){
contributionsCollection(from:$f,to:$t){contributionCalendar{weeks{contributionDays{date contributionCount}}}}}}"""


def daily_counts(login, start, end):
    """Day -> count for one account, fetched a month at a time (a full year times out)."""
    days = {}
    cur = start
    while cur < end:
        nxt = min(cur + dt.timedelta(days=31), end)
        body = json.dumps({"query": QUERY, "variables": {
            "u": login,
            "f": f"{cur.isoformat()}T00:00:00Z",
            "t": f"{(nxt - dt.timedelta(days=1)).isoformat()}T23:59:59Z",
        }}).encode()
        res = json.loads(http("https://api.github.com/graphql", body, {
            "Authorization": f"bearer {os.environ['GITHUB_TOKEN']}", "Content-Type": "application/json"}))
        if res.get("errors") or not res.get("data", {}).get("user"):
            raise RuntimeError(f"GraphQL error for {login}: {res.get('errors')}")
        for w in res["data"]["user"]["contributionsCollection"]["contributionCalendar"]["weeks"]:
            for d in w["contributionDays"]:
                day = dt.date.fromisoformat(d["date"])
                if start <= day < end:
                    days[day] = d["contributionCount"]
        cur = nxt
    return days


def merged_prs(login, first, last, token):
    q = f"repo:{PR_REPO} is:pr is:merged author:{login} merged:{first.isoformat()}..{last.isoformat()}"
    res = json.loads(http("https://api.github.com/search/issues?per_page=1&q=" + urllib.parse.quote(q), headers={
        "Authorization": f"bearer {token}", "Accept": "application/vnd.github+json"}))
    time.sleep(2.2)  # search API: 30 requests a minute
    return res["total_count"]


def board(url):
    page = http(url, headers={"User-Agent": "josteins-profile-rankings"})
    gen = re.search(r"(\d{4}-\d{2}-\d{2}) \d{2}:\d{2}:\d{2} \+0000", page)
    # The page also lists organisations in a second table; rank people only.
    page = page.split('class="users-list"', 1)[-1].split('class="organizations-list"', 1)[0]
    rows = re.findall(
        r'<tr id="([^"]+)">\s*<td>(\d+)\.</td>\s*<td><a[^>]*>[^<]*</a>(?:<br>\(([^)]*)\))?</td>\s*<td>(\d+)</td>',
        page, re.S)
    if len(rows) < 50:
        raise RuntimeError(f"{url}: parsed only {len(rows)} rows; the page layout may have changed")
    users = [{"login": r[0], "name": html.unescape(r[2] or ""), "contribs": int(r[3])}
             for r in rows if r[0] not in ALL_ACCOUNTS]
    return users, gen.group(1) if gen else None


def place(users, total):
    """Rank a total would take among the listed users (ties go to us)."""
    return sum(u["contribs"] > total for u in users) + 1


def month_starts(today, n=12):
    out, y, m = [], today.year, today.month
    for _ in range(n):
        out.append(dt.date(y, m, 1))
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    return out[::-1]


def compact(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 10_000:
        return f"{n / 1000:.1f}K".replace(".0K", "K")
    return f"{n:,}"


def card(snapshot, dark):
    c = {
        "bg": "#1a1a19" if dark else "#fcfcfb",
        "ring": "rgba(255,255,255,0.10)" if dark else "rgba(11,11,11,0.10)",
        "ink": "#f0efec" if dark else "#0b0b0b",
        "ink2": "#c3c2b7" if dark else "#52514e",
        "muted": "#898781",
        "accent": "#3987e5" if dark else "#2a78d6",
        "grid": "#2c2c2a" if dark else "#e1e0d9",
    }
    hero = next(p for p in snapshot["people"] if p["key"] == HERO)
    months = hero["months"]
    m_max = max(months) or 1
    x0, x1, y0, y1 = 316, 536, 46, 112
    slot = (x1 - x0) / len(months)
    bw = min(12.0, slot - 4)
    bars = ""
    for i, v in enumerate(months):
        h = max(2.0, v / m_max * (y1 - y0))
        x = x0 + i * slot + (slot - bw) / 2
        # 4px rounded data-end, square at the baseline
        r = min(3.0, bw / 2, h / 2)
        bars += (f'<path d="M{x:.1f},{y1}V{y1 - h + r:.1f}Q{x:.1f},{y1 - h:.1f} {x + r:.1f},{y1 - h:.1f}'
                 f'H{x + bw - r:.1f}Q{x + bw:.1f},{y1 - h:.1f} {x + bw:.1f},{y1 - h + r:.1f}V{y1}Z" fill="{c["accent"]}"/>')
    ranks = " · ".join(f'#{b["rank"]} {b["label"]}' for b in hero["boards"])
    font = "system-ui,-apple-system,'Segoe UI',Helvetica,Arial,sans-serif"
    first = dt.date.fromisoformat(snapshot["months"][0]).strftime("%b %Y")
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="560" height="150" viewBox="0 0 560 150" role="img" aria-label="{hero['total']:,} GitHub contributions in the last 12 months; {ranks}">
<rect x="0.5" y="0.5" width="559" height="149" rx="12" fill="{c['bg']}" stroke="{c['ring']}"/>
<g font-family="{font}">
<text x="24" y="34" font-size="13" fill="{c['ink2']}">GitHub contributions, last 12 months</text>
<text x="24" y="80" font-size="42" font-weight="600" fill="{c['ink']}">{compact(hero['total'])}</text>
<text x="24" y="108" font-size="15" font-weight="600" fill="{c['ink']}">{html.escape(ranks)}</text>
<text x="24" y="132" font-size="11" fill="{c['muted']}">Ranked against committers.top · updated {snapshot['date']}</text>
<text x="{x0}" y="34" font-size="11" fill="{c['muted']}">Per month</text>
<line x1="{x0}" x2="{x1}" y1="{y1 + 0.5}" y2="{y1 + 0.5}" stroke="{c['grid']}"/>
{bars}
<text x="{x0}" y="132" font-size="11" fill="{c['muted']}">{first}</text>
<text x="{x1}" y="132" font-size="11" fill="{c['muted']}" text-anchor="end">this month</text>
</g></svg>
"""


README_BLOCK = """<!-- rankings:start -->
<div align="center">

### 🏁 Contribution ranking

<a href="{url}"><picture><source media="(prefers-color-scheme: dark)" srcset="rankings/card-dark.svg"><img alt="{alt}" src="rankings/card-light.svg" width="560"></picture></a>

<sub>Updated weekly · <a href="{url}">full charts</a></sub>

</div>
<!-- rankings:end -->"""


def update_readme(snapshot):
    path = ROOT / "README.md"
    text = path.read_text(encoding="utf-8")
    hero = next(p for p in snapshot["people"] if p["key"] == HERO)
    ranks = ", ".join(f"#{b['rank']} in {b['label']}" for b in hero["boards"])
    block = README_BLOCK.format(url=PAGE_URL, alt=f"{hero['total']:,} contributions in the last 12 months; {ranks}")
    if "<!-- rankings:start -->" in text:
        text = re.sub(r"<!-- rankings:start -->.*?<!-- rankings:end -->", lambda _: block, text, flags=re.S)
    else:
        anchor = "## 📊 GitHub Activity & Contributions\n"
        if anchor not in text:
            raise RuntimeError("README anchor heading not found; add the rankings markers by hand")
        text = text.replace(anchor, anchor + "\n" + block + "\n", 1)
    path.write_text(text, encoding="utf-8")


def main():
    today = dt.datetime.now(dt.timezone.utc).date()
    end = today + dt.timedelta(days=1)
    start = end - dt.timedelta(days=365)
    months = month_starts(today)
    fetch_start = min(start, months[0])
    pr_token = os.environ.get("NORVANA_READ_TOKEN")
    latest_path = DATA / "latest.json"
    previous = json.loads(latest_path.read_text()) if latest_path.exists() else {}

    boards = {b["key"]: board(b["url"]) for b in BOARDS}

    people = []
    for p in PEOPLE:
        combined, per_account = {}, {}
        for login in p["accounts"]:
            days = daily_counts(login, fetch_start, end)
            per_account[login] = sum(n for d, n in days.items() if d >= start)
            for d, n in days.items():
                combined[d] = combined.get(d, 0) + n
        total = sum(n for d, n in combined.items() if d >= start)
        if p.get("opt_in") and total == 0:
            # Not shown until the person switches on "Private contributions" on their profile.
            print(f"{p['name']}: not opted in yet, left off the page", file=sys.stderr)
            continue
        month_counts = []
        for i, m0 in enumerate(months):
            m1 = months[i + 1] if i + 1 < len(months) else end
            month_counts.append(sum(n for d, n in combined.items() if m0 <= d < m1))
        prs = None
        if pr_token:
            prs = []
            for i, m0 in enumerate(months):
                last = (months[i + 1] if i + 1 < len(months) else end) - dt.timedelta(days=1)
                prs.append(sum(merged_prs(a, m0, last, pr_token) for a in p["accounts"]))
        ranked = []
        if total > 0:
            for b in BOARDS:
                users, generated = boards[b["key"]]
                rank = place(users, total)
                ranked.append({"key": b["key"], "label": b["label"], "rank": rank, "listed": len(users)})
        people.append({"key": p["key"], "name": p["name"], "accounts": per_account, "total": total,
                       "months": month_counts, "prs": prs, "boards": ranked})

    pr_as_of = today.isoformat() if pr_token else None
    if not pr_token and previous.get("pr_repo"):
        # No token this run: keep the last PR counts, aligned by month, and say how old they are.
        old = {p["key"]: dict(zip(previous["months"], p.get("prs") or [])) for p in previous["people"]}
        for p in people:
            p["prs"] = [old.get(p["key"], {}).get(m.isoformat(), 0) for m in months]
        pr_as_of = previous.get("pr_as_of") or previous["date"]

    snapshot = {
        "date": today.isoformat(), "window": [start.isoformat(), today.isoformat()],
        "months": [m.isoformat() for m in months], "pr_repo": PR_REPO if pr_as_of else None,
        "pr_as_of": pr_as_of, "people": people,
        "boards": [{"key": b["key"], "label": b["label"], "source": b["url"],
                    "generated": boards[b["key"]][1], "listed": len(boards[b["key"]][0]),
                    "users": boards[b["key"]][0][:25]} for b in BOARDS],
    }

    DATA.mkdir(parents=True, exist_ok=True)
    hist_path = DATA / "history.json"
    history = json.loads(hist_path.read_text()) if hist_path.exists() else []
    iso_week = lambda s: dt.date.fromisoformat(s).isocalendar()[:2]  # noqa: E731
    history = [h for h in history if iso_week(h["date"]) != iso_week(snapshot["date"])]
    history.append({"date": snapshot["date"],
                    "people": {p["key"]: {"total": p["total"], "ranks": {b["key"]: b["rank"] for b in p["boards"]}}
                               for p in people}})
    history.sort(key=lambda h: h["date"])

    latest_path.write_text(json.dumps(snapshot, indent=2) + "\n")
    hist_path.write_text(json.dumps(history, indent=2) + "\n")

    # Names come from a third-party page: escape <, > and & so nothing can leave the script element.
    payload = (json.dumps({"snapshot": snapshot, "history": history, "hero": HERO})
               .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))
    template = (OUT / "template.html").read_text(encoding="utf-8")
    (OUT / "index.html").write_text(template.replace("/*__DATA__*/null", payload), encoding="utf-8")
    (OUT / "card-light.svg").write_text(card(snapshot, dark=False), encoding="utf-8")
    (OUT / "card-dark.svg").write_text(card(snapshot, dark=True), encoding="utf-8")
    update_readme(snapshot)
    print(json.dumps({p["name"]: {"total": p["total"], "ranks": {b["label"]: b["rank"] for b in p["boards"]},
                                  "prs_12mo": sum(p["prs"]) if p["prs"] else None} for p in people}))


if __name__ == "__main__":
    main()
