# LinkedIn Non-Profit Outreach

Selenium-driven LinkedIn automation: searches non-profit companies, scrapes people from each, sends connection requests with a personalized note, and tracks everything in Excel.

> **Warning** — LinkedIn's ToS prohibits automation; accounts can be restricted or banned. Keep volume low (default 15/day), keep delays randomized, and always test with `--dry-run` first. Use at your own risk.

## Setup

```powershell
pip install -r requirements.txt
```

Chrome must be installed. Selenium 4.6+ auto-resolves the matching ChromeDriver — no separate install.

## First run

1. Edit `config/config.yaml` — set keywords, daily limit, note template.
2. Dry run first to verify search + scrape work without sending anything:
   ```powershell
   python main.py --dry-run
   ```
   A Chrome window opens. Log in to LinkedIn manually. Press Enter in the terminal when you've reached your feed.
3. Your session cookie is saved in `./chrome_profile/`. Future runs reuse it — no manual login.
4. Inspect `data/outreach.xlsx`. Each row shows the contact + status (`skipped` with reason `dry-run`).

## Real run

```powershell
python main.py
```

The script:
1. Loads `data/outreach.xlsx` and counts how many connections were already sent today.
2. For each keyword: searches LinkedIn companies, opens each company's `/people` tab, picks people whose title matches `target_titles`.
3. Skips anyone already in the tracker.
4. Opens each profile, finds the Connect button (handles the More-menu case), optionally types the note, clicks Send.
5. Logs status to Excel after every action — safe to interrupt with Ctrl+C.

Stops automatically when `daily_limit` connections have been sent today.

## Excel columns

| Column | Notes |
|---|---|
| Timestamp | ISO datetime when row was written |
| Company / Company URL | Source non-profit |
| Name / Title | Scraped from company People tab |
| Profile URL | Used as dedup key |
| Status | `sent`, `skipped`, `failed`, `already_connected` |
| Note Sent | Actual rendered note text (empty if non-Premium dialog) |
| Keyword | Which search keyword surfaced this person |
| Error | Failure detail if `status=failed` |

## Tuning

All knobs live in `config/config.yaml`:

- `search.keywords` — search phrases.
- `search.target_titles` — only contact people whose title contains one of these. Leave empty for everyone.
- `search.max_companies_per_keyword` / `max_people_per_company` — how wide to fan out.
- `connection.daily_limit` — hard stop per calendar day. Default 15 — keep it under 20 to stay safe.
- `connection.send_note` — try to attach a note. LinkedIn often disables custom notes for non-Premium accounts; the script falls back to "Send without a note" automatically.
- `connection.note_template` — `{first_name}` and `{company}` are substituted in.
- `delays.*` — randomized waits. Don't lower these aggressively.
- `debug.focus_highlight` — when `true`, outlines the current card/button the bot is acting on.

## Troubleshooting

- **"chrome failed to start: user data directory is already in use"** — close every Chrome window before running.
- **"Connect" button not found** — LinkedIn rolls out UI variants frequently. Open the profile manually, see where Connect lives (sometimes under "More"), and add a matching XPath in `linkedin.py:_find_connect_button` or `_find_connect_in_more_menu`.
- **No companies returned for a keyword** — try the search manually in your browser; LinkedIn sometimes adds filter walls that need adjusting.
- **Account got a warning** — stop running. Wait several days. Drop `daily_limit` further when you resume.

## Files

- `main.py` — orchestrator + CLI
- `linkedin.py` — Selenium wrapper (login, search, scrape, connect)
- `tracker.py` — Excel I/O and dedup
- `config.yaml` — all tunables
- `chrome_profile/` — persistent browser session (gitignored)
- `data/outreach.xlsx` — generated tracker