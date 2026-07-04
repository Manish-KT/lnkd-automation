"""Entry point. Orchestrates: search companies -> scrape people -> send connections."""
from __future__ import annotations

import argparse
import logging
import random
import sys
import time
from pathlib import Path

import yaml

from linkedin import LinkedIn
from tracker import Contact, Tracker

log = logging.getLogger("outreach")


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def render_note(template: str, *, first_name: str, company: str) -> str:
    try:
        return template.format(first_name=first_name, company=company)
    except KeyError:
        return template


def random_sleep(lo: float, hi: float) -> None:
    """Sleep for a random duration between the configured limits."""
    duration = random.uniform(lo, hi)
    log.info("Sleeping for %.1f seconds...", duration)
    time.sleep(duration)


def run(cfg: dict, dry_run: bool, headless: bool) -> int:
    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------
    search_cfg = cfg["search"]
    connection_cfg = cfg["connection"]
    delays = cfg["delays"]

    daily_limit = connection_cfg["daily_limit"]
    note_template = connection_cfg["note_template"]
    send_note = connection_cfg["send_note"]

    max_people = search_cfg["max_people_per_company"]
    max_pages = search_cfg.get("max_pages_per_company", 5)

    title_filters = search_cfg.get("target_titles", [])
    locations = search_cfg.get("target_locations", [])
    company_sizes = search_cfg.get("target_company_sizes", [])
    industries = search_cfg.get("target_industries", [])

    tracker = Tracker(cfg["paths"]["tracker_file"])
    already_sent_today = tracker.sent_today()

    log.info("Already sent today: %d / %d", already_sent_today, daily_limit)

    if already_sent_today >= daily_limit and not dry_run:
        log.warning("Daily limit already reached. Exiting.")
        return 0
    
    paths = cfg["paths"]
    debug = cfg.get("debug", {})

    li = LinkedIn(
        profile_dir=paths["chrome_profile"],
        chrome_binary=paths.get("chrome_binary") or "",
        headless=headless,
        focus_highlight=debug.get("focus_highlight", True),
    )

    processed_companies = tracker.get_processed_companies()

    sent_this_run = 0

    try:
        li.ensure_logged_in()
        random_sleep(delays["action_min"], delays["action_max"])

        for keyword in search_cfg["keywords"]:
            log.info("=== Keyword: %s ===", keyword)

            companies = li.search_companies(
                keyword=keyword,
                limit=search_cfg["max_companies_per_keyword"],
                locations=locations,
                company_sizes=company_sizes,
                industries=industries,
                skip_companies=processed_companies,
            )

            random_sleep(delays["action_min"], delays["action_max"])

            for company in companies:
                log.info("-> Company: %s (%s)", company.name, company.url)

                processed = 0   

                for pop in li.iter_employees(
                    company,
                    max_pages=max_pages,
                    title_filters=title_filters,
                ):
                    if processed >= max_people:
                        break

                    person = pop.person

                    if tracker.already_contacted(person.profile_url):
                        log.info("   Already contacted: %s", person.name)
                        continue

                    total_sent = already_sent_today + sent_this_run

                    if total_sent >= daily_limit and not dry_run:
                        log.warning("Daily limit reached (%d). Stopping.", daily_limit)
                        return sent_this_run

                    first_name = person.name.split()[0] if person.name else "there"

                    note = (
                        render_note(
                            note_template,
                            first_name=first_name,
                            company=company.name,
                        )
                        if send_note
                        else ""
                    )

                    contact = Contact(
                        company=company.name,
                        company_url=company.url,
                        name=person.name,
                        title=person.title,
                        profile_url=person.profile_url,
                        keyword=keyword,
                    )

                    if dry_run:
                        contact.status = "skipped"
                        contact.note_sent = note
                        contact.error = "dry-run"

                        log.info(
                            "   DRY RUN -> %s (%s)",
                            person.name,
                            person.title,
                        )

                    else:
                        try:
                            status, sent_note = pop.connect(note=note)

                            contact.status = status
                            contact.note_sent = sent_note

                            if status == "sent_without_note":
                                sent_this_run += 1

                        except Exception as e:
                            contact.status = "failed"
                            contact.error = str(e)[:200]

                            log.exception(
                                "Connect failed for %s",
                                person.profile_url,
                            )

                        log.info(
                            "   %s: %s — %s",
                            contact.status,
                            person.name,
                            person.title,
                        )

                    tracker.record(contact)

                    processed += 1

                    random_sleep(
                        delays["between_people_min"],
                        delays["between_people_max"],
                    )

                random_sleep(
                    delays["between_companies_min"],
                    delays["between_companies_max"],
                )

        return sent_this_run

    finally:
        li.quit()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="LinkedIn non-profit outreach automation."
    )

    parser.add_argument("--config", default="config.yaml")

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scrape and log only; do not send connection requests.",
    )

    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run Chrome headless (not recommended for first run).",
    )

    parser.add_argument("--verbose", action="store_true")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    cfg_path = Path(args.config)

    if not cfg_path.exists():
        print(f"Config not found: {cfg_path}", file=sys.stderr)
        return 2

    cfg = load_config(str(cfg_path))

    sent = run(
        cfg,
        dry_run=args.dry_run,
        headless=args.headless,
    )

    log.info("Done. Sent this run: %d", sent)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())