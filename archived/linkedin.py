"""LinkedIn interaction layer: login check, company search, people scrape, connect.

Selectors are LinkedIn's biggest source of breakage. Each lookup uses several
fallbacks; if one combination stops working, add a new pattern alongside the others.
"""
from __future__ import annotations

import logging
import os
import random
import time
import urllib.parse
from dataclasses import dataclass
from typing import Optional

from selenium import webdriver
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    NoSuchElementException,
    TimeoutException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

log = logging.getLogger(__name__)

# ==========================================================
# Placeholder XPaths
# Replace these after inspecting LinkedIn's DOM.
# ==========================================================

ALL_FILTERS_BUTTON = "XPATH_TO_ALL_FILTERS_BUTTON"

LOCATION_SECTION = "XPATH_TO_LOCATION_FILTER_SECTION"
LOCATION_INPUT = "XPATH_TO_LOCATION_SEARCH_INPUT"
LOCATION_OPTION = (
    "//span[normalize-space()='{value}']"
)

COMPANY_SIZE_SECTION = "XPATH_TO_COMPANY_SIZE_SECTION"
COMPANY_SIZE_OPTION = (
    "//label[.//*[contains(normalize-space(), '{value}')]]"
)

APPLY_BUTTON = "XPATH_TO_APPLY_BUTTON"


@dataclass
class Person:
    name: str
    title: str
    profile_url: str


@dataclass
class Company:
    name: str
    url: str  # canonical /company/{slug}/ URL


@dataclass
class PersonOnPage:
    """A scraped person plus a bound .connect() that acts on their card on the current search page."""
    person: Person
    _li: "LinkedIn"

    def connect(self, note: str = "") -> tuple[str, str]:
        return self._li._connect_by_profile_url(self.person.profile_url, note)


class LinkedIn:
    BASE = "https://www.linkedin.com"
    

    def __init__(
        self,
        profile_dir: str,
        chrome_binary: str = "",
        headless: bool = False,
        focus_highlight: bool = True,
    ):
        self.driver = self._build_driver(profile_dir, chrome_binary, headless)
        self.wait = WebDriverWait(self.driver, 15)
        self.focus_highlight = focus_highlight
        log.info("Focus highlight enabled: %s", self.focus_highlight)

    # ---------- driver setup ----------

    @staticmethod
    def _build_driver(profile_dir: str, chrome_binary: str, headless: bool) -> webdriver.Chrome:
        os.makedirs(profile_dir, exist_ok=True)
        opts = Options()
        opts.add_argument(f"--user-data-dir={os.path.abspath(profile_dir)}")
        opts.add_argument("--profile-directory=Default")
        opts.add_argument("--start-maximized")
        opts.add_argument("--disable-blink-features=AutomationControlled")
        opts.add_experimental_option("excludeSwitches", ["enable-automation"])
        opts.add_experimental_option("useAutomationExtension", False)
        if chrome_binary:
            opts.binary_location = chrome_binary
        if headless:
            opts.add_argument("--headless=new")
        driver = webdriver.Chrome(options=opts)
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"},
        )
        return driver

    # ---------- helpers ----------

    @staticmethod
    def _sleep(lo: float, hi: float) -> None:
        time.sleep(random.uniform(lo, hi))

    def _goto(self, url: str) -> None:
        self.driver.get(url)
        # Wait for body; LinkedIn renders the rest progressively.
        self.wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))

    def quit(self) -> None:
        try:
            self.driver.quit()
        except Exception:
            pass

    # ---------- auth ----------

    def ensure_logged_in(self) -> None:
        """Go to the feed. If LinkedIn redirects to login, prompt the user and wait."""
        self._goto(f"{self.BASE}/feed/")
        self._sleep(2, 4)
        if "/login" in self.driver.current_url or "/checkpoint" in self.driver.current_url:
            print("\n[!] Not logged in. A Chrome window is open — please log in manually.")
            print("    After you reach your LinkedIn feed, press Enter here to continue...")
            input()
            self._goto(f"{self.BASE}/feed/")
            self._sleep(2, 4)
            if "/login" in self.driver.current_url:
                raise RuntimeError("Still not logged in after manual prompt.")
        log.info("Logged in.")

    # ---------- company search ----------

    def search_companies(
        self,
        keyword: str,
        limit: int,
        locations: list[str] | None = None,
        company_sizes: list[str] | None = None,
    ):
        q = urllib.parse.quote_plus(keyword)
        url = f"{self.BASE}/search/results/companies/?keywords={q}"
        self._goto(url)
        self._sleep(3, 5)

        # Scroll to load more results.
        self.driver.execute_script("window.scrollBy(0, 800);")
        self._sleep(1, 2)

        anchors = self.driver.find_elements(
            By.CSS_SELECTOR, "a[href*='/company/']"
        )
        seen: set[str] = set()
        results: list[Company] = []
        for a in anchors:
            self._highlight(a, color="#ffea00", label="COMPANY_LINK")
            if self.focus_highlight:
                time.sleep(0.1)
            href = a.get_attribute("href") or ""
            if "/company/" not in href:
                continue
            # Canonical form: https://www.linkedin.com/company/{slug}/
            base = href.split("?")[0].rstrip("/")
            # Trim sub-paths like /people, /jobs, /about
            parts = base.split("/company/")
            if len(parts) != 2:
                continue
            slug = parts[1].split("/")[0]
            if not slug or slug in seen:
                continue
            seen.add(slug)
            name = (a.text or "").strip().splitlines()[0] if a.text else slug
            results.append(Company(name=name or slug, url=f"{self.BASE}/company/{slug}/"))
            if len(results) >= limit:
                break
        log.info("Found %d companies for '%s'", len(results), keyword)
        return results

    # ---------- employee search flow ----------

    def iter_employees(
        self,
        company: Company,
        max_pages: int,
        title_filters: list[str],
    ):
        """Open the company's "See all N employees" search and yield PersonOnPage objects.

        Each yielded item carries .connect(note) which re-finds the card on the live page
        and clicks Connect — robust against stale element refs.
        """
        search_url = self._open_employees_search(company)
        if not search_url:
            log.warning("No employees search link found for %s — skipping", company.name)
            return

        filters_lower = [t.lower() for t in title_filters] if title_filters else []
        seen: set[str] = set()
        has_pagination: Optional[bool] = None

        for page in range(1, max_pages + 1):
            page_url = self._with_page(search_url, page)
            self._goto(page_url)
            self._sleep(3, 6)
            # Trigger lazy loading of cards.
            for _ in range(3):
                self.driver.execute_script("window.scrollBy(0, 700);")
                self._sleep(1, 2)
            self.driver.execute_script("window.scrollTo(0, 0);")
            self._sleep(1, 2)

            if has_pagination is None:
                has_pagination = self._has_people_pagination()
                if not has_pagination:
                    log.info(
                        "No pagination detected for %s — processing current people list only",
                        company.name,
                    )

            people, total_profiles_on_page = self._collect_page_people(filters_lower)
            log.info(
                "Page %d: %d total profiles found on %s",
                page,
                total_profiles_on_page,
                company.name,
            )
            if not people:
                log.info("Page %d empty — stopping pagination for %s", page, company.name)
                break
            log.info("Page %d: %d candidates on %s", page, len(people), company.name)

            for person in people:
                if person.profile_url in seen:
                    continue
                seen.add(person.profile_url)
                yield PersonOnPage(person=person, _li=self)

            if has_pagination is False:
                break

    def _open_employees_search(self, company: Company) -> Optional[str]:
        self._goto(company.url)
        self._sleep(3, 5)
        # Some company pages hide the employees link below the fold.
        self.driver.execute_script("window.scrollBy(0, 400);")
        self._sleep(1, 2)

        # Primary: the "See all N employees" link uses /search/results/people/ + currentCompany filter.
        links = self.driver.find_elements(
            By.XPATH,
            "//a[contains(@href, '/search/results/people/') and contains(@href, 'currentCompany')]",
        )
        for link in links:
            self._highlight(link, color="#ffea00", label="OPEN_EMPLOYEES_LINK")
            if self.focus_highlight:
                time.sleep(0.1)
            href = link.get_attribute("href")
            if href and "/search/results/people/" in href:
                return href

        # Fallback 1: an <a> whose visible text or aria-label mentions "employee".
        links = self.driver.find_elements(
            By.XPATH,
            "//main//a[contains(translate(@aria-label, 'EMPLOY', 'employ'), 'employee') "
            "or contains(translate(., 'EMPLOY', 'employ'), 'employee')]",
        )
        for link in links:
            self._highlight(link, color="#ffea00", label="OPEN_EMPLOYEES_LINK")
            if self.focus_highlight:
                time.sleep(0.1)
            href = link.get_attribute("href") or ""
            if "/search/results/people/" in href:
                return href

        return None

    @staticmethod
    def _with_page(url: str, page: int) -> str:
        if page <= 1:
            return url
        parsed = urllib.parse.urlparse(url)
        params = [(k, v) for k, v in urllib.parse.parse_qsl(parsed.query) if k != "page"]
        params.append(("page", str(page)))
        return urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(params)))

    def _has_people_pagination(self) -> bool:
        """Return True when visible pagination controls exist on the current people results page."""
        # Primary pagination container path observed on company people pages.
        pagination_xpath = (
            "/html/body/div/div[2]/div[2]/div[2]/main/div/div/div/div[2]/div/section/div/div/div/div[3]"
        )
        for container in self.driver.find_elements(By.XPATH, pagination_xpath):
            try:
                if container.is_displayed() and container.find_elements(
                    By.XPATH,
                    ".//button | .//a | .//li",
                ):
                    self._highlight(container, color="#ffea00", label="PAGINATION")
                    if self.focus_highlight:
                        time.sleep(0.15)
                    return True
            except Exception:
                continue

        for xp in (
            "//main//*[contains(@class, 'artdeco-pagination')]",
            "//main//button[contains(translate(@aria-label, 'NEXT', 'next'), 'next')]",
            "//main//ul[contains(@class, 'artdeco-pagination__pages')]//li",
        ):
            for el in self.driver.find_elements(By.XPATH, xp):
                try:
                    if el.is_displayed():
                        self._highlight(el, color="#ffea00", label="PAGINATION")
                        if self.focus_highlight:
                            time.sleep(0.15)
                        return True
                except Exception:
                    continue
        return False

    def _collect_page_people(self, filters_lower: list[str]) -> tuple[list[Person], int]:
        """Scrape name/title/profile URL from every result card on the current page."""
        cards = []
        cards_by_url: dict[str, object] = {}

        # Primary path observed on the redirected employees page.
        container_xpath = (
            "/html/body/div/div[2]/div[2]/div[2]/main/div/div/div/div[2]/div/section/div/div/div/div[1]/div[1]/div"
        )
        containers = self.driver.find_elements(By.XPATH, container_xpath)
        if containers:
            self._highlight(containers[0], color="#ffea00", label="LIST")
            time.sleep(0.35)
        for container in containers:
            cards.extend(
                container.find_elements(
                    By.XPATH,
                    "./div[.//a[contains(@href, '/in/')]]",
                )
            )

        # Fallbacks for other LinkedIn result layouts.
        if not cards:
            for xp in (
                "//main//li[.//a[contains(@href, '/in/')]]",
                "//main//div[contains(@class, 'entity-result') and .//a[contains(@href, '/in/')]]",
                "//main//div[@data-chameleon-result-urn and .//a[contains(@href, '/in/')]]",
                "//main//div[contains(@class, 'org-people-profile-card') and .//a[contains(@href, '/in/')]]",
                "//main//li[contains(@class, 'org-people-profile-card') and .//a[contains(@href, '/in/')]]",
            ):
                found = self.driver.find_elements(By.XPATH, xp)
                if found:
                    cards = found
                    break

        # Last-resort fallback: grab profile anchors and map each to a nearby ancestor card.
        if not cards:
            anchors = self.driver.find_elements(By.XPATH, "//main//a[contains(@href, '/in/')]")
            for a in anchors:
                href = (a.get_attribute("href") or "").split("?")[0].rstrip("/")
                if "/in/" not in href or href in cards_by_url:
                    continue
                try:
                    card = a.find_element(
                        By.XPATH,
                        "ancestor::*[self::li or self::div][.//a[contains(@href, '/in/')]][1]",
                    )
                except NoSuchElementException:
                    card = a
                cards_by_url[href] = card
            cards = list(cards_by_url.values())

        if not cards:
            try:
                main_el = self.driver.find_element(By.XPATH, "//main")
                self._highlight(main_el, color="#ffea00", label="NO CARDS")
                time.sleep(0.5)
            except NoSuchElementException:
                pass

        log.debug("Employee cards found on page: %d", len(cards))
        people: list[Person] = []
        seen_on_page: set[str] = set()
        filtered_out_by_title = 0
        for card in cards:
            self._highlight(card, color="#3a86ff", label="SCRAPE")
            if self.focus_highlight:
                time.sleep(0.2)
            try:
                link = card.find_element(By.XPATH, ".//a[contains(@href, '/in/')]")
            except NoSuchElementException:
                continue
            self._highlight(link, color="#ffea00", label="EMPLOYEE_LINK")
            if self.focus_highlight:
                time.sleep(0.1)
            href = (link.get_attribute("href") or "").split("?")[0].rstrip("/")
            if "/in/" not in href or href in seen_on_page:
                continue
            seen_on_page.add(href)

            name, title = self._extract_card_text(card)
            if not name:
                name = (link.text or "").strip()
            if not name:
                continue
            if filters_lower and not any(f in title.lower() for f in filters_lower):
                filtered_out_by_title += 1
                continue
            people.append(Person(name=name, title=title, profile_url=href + "/"))

        if filtered_out_by_title and not people:
            log.info(
                "Profiles found but filtered out by target_titles (%d filtered)",
                filtered_out_by_title,
            )
        return people, len(seen_on_page)

    @staticmethod
    def _extract_card_text(card) -> tuple[str, str]:
        text = (card.text or "").strip()
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        noise = {
            "1st", "2nd", "3rd", "3rd+", "connect", "message", "follow",
            "pending", "more", "view profile", "status is reachable",
        }
        filtered = [ln for ln in lines if ln.lower() not in noise]
        name = filtered[0] if filtered else ""
        title = filtered[1] if len(filtered) >= 2 else ""
        return name, title

    # ---------- connect from a search-results card ----------

    def _connect_by_profile_url(self, profile_url: str, note: str = "") -> tuple[str, str]:
        """Find the card for this profile on the current page and click its Connect button."""
        slug = profile_url.rstrip("/").split("/in/")[-1]
        cards = self.driver.find_elements(
            By.XPATH,
            f"//main//li[.//a[contains(@href, '/in/{slug}')]]",
        )
        if not cards:
            return "skipped", ""
        card = cards[0]
        self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", card)
        self._highlight(card, color="#ffbe0b", label="CARD")
        if self.focus_highlight:
            time.sleep(0.35)
        self._sleep(0.7, 1.5)

        # Inspect the card's action button.
        connect_btn = self._find_card_button(card, ["Connect"])
        if connect_btn is None:
            # Distinguish already-connected vs blocked-by-state.
            if self._find_card_button(card, ["Message"]) is not None:
                return "already_connected", ""
            if self._find_card_button(card, ["Pending"]) is not None:
                return "skipped", ""
            # Sometimes Connect lives under a "More" button on the card.
            more_btn = self._find_card_button(card, ["More"])
            if more_btn is not None:
                try:
                    self._click(more_btn)
                    self._sleep(1, 2)
                    connect_btn = self._find_dropdown_item(["Connect"])
                except Exception:
                    connect_btn = None
            if connect_btn is None:
                return "skipped", ""

        try:
            self._click(connect_btn)
        except ElementClickInterceptedException:
            self._sleep(1, 2)
            self._click(connect_btn)
        self._sleep(2, 4)

        # Modal flow.
        sent_note = ""
        if note:
            add_note_btn = self._find_modal_button(["add a note"])
            if add_note_btn is not None:
                self._click(add_note_btn)
                self._sleep(1, 2)
                try:
                    textarea = self.driver.find_element(
                        By.CSS_SELECTOR, "textarea[name='message'], #custom-message"
                    )
                    self._highlight(textarea, color="#ffea00", label="NOTE_TEXTAREA")
                    if self.focus_highlight:
                        time.sleep(0.2)
                    textarea.send_keys(note[:300])
                    sent_note = note[:300]
                    self._sleep(1, 2)
                except NoSuchElementException:
                    pass

        send_btn = self._find_modal_button(
            ["send now", "send invitation", "send without a note", "send"]
        )
        if send_btn is None:
            self._close_modal()
            return "failed", sent_note
        self._click(send_btn)
        self._sleep(2, 4)
        return "sent", sent_note

    # ---------- selector helpers ----------

    @staticmethod
    def _find_card_button(card, labels: list[str]):
        for label in labels:
            xp = (
                f".//button[normalize-space()='{label}' "
                f"or .//span[normalize-space()='{label}'] "
                f"or starts-with(@aria-label, 'Invite')]"
                if label == "Connect"
                else f".//button[normalize-space()='{label}' or .//span[normalize-space()='{label}']]"
            )
            try:
                buttons = card.find_elements(By.XPATH, xp)
            except NoSuchElementException:
                continue
            for b in buttons:
                if b.is_displayed() and b.is_enabled():
                    return b
        return None

    def _find_dropdown_item(self, labels: list[str]):
        for label in labels:
            xp = (
                f"//div[contains(@class,'artdeco-dropdown__content') or @role='menu']"
                f"//*[normalize-space()='{label}']"
            )
            for el in self.driver.find_elements(By.XPATH, xp):
                if el.is_displayed():
                    return el
        return None

    def _find_modal_button(self, label_substrings: list[str]):
        for label in label_substrings:
            xp = (
                f"//div[@role='dialog']//button[contains(translate(normalize-space(.), "
                f"'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{label}')]"
            )
            for b in self.driver.find_elements(By.XPATH, xp):
                if b.is_displayed() and b.is_enabled():
                    return b
        return None

    def _close_modal(self) -> None:
        try:
            close = self.driver.find_element(
                By.XPATH, "//div[@role='dialog']//button[@aria-label='Dismiss']"
            )
            if close.is_displayed():
                self._click(close)
                self._sleep(1, 2)
        except NoSuchElementException:
            pass

    def _highlight(self, element, color: str = "#ffea00", label: str = "") -> None:
        """Draw a short-lived overlay around an element to show current automation focus."""
        if not self.focus_highlight:
            return
        try:
            self.driver.execute_script(
                """
                const el = arguments[0];
                const color = '#ffea00';
                const label = arguments[2];
                const durationMs = 4200;

                // Always-visible viewport frame (fallback HUD) so focus feedback is obvious.
                const oldHud = document.getElementById('__bot_focus_hud');
                if (oldHud) oldHud.remove();
                const hud = document.createElement('div');
                hud.id = '__bot_focus_hud';
                hud.style.position = 'fixed';
                hud.style.inset = '6px';
                hud.style.border = '6px solid #ffea00';
                hud.style.borderRadius = '12px';
                hud.style.boxShadow = '0 0 0 4px rgba(255, 234, 0, 0.45), inset 0 0 0 2px rgba(0,0,0,0.2)';
                hud.style.pointerEvents = 'none';
                hud.style.zIndex = '2147483647';

                const hudBadge = document.createElement('div');
                hudBadge.textContent = label ? `BOT FOCUS: ${label}` : 'BOT FOCUS';
                hudBadge.style.position = 'fixed';
                hudBadge.style.top = '14px';
                hudBadge.style.left = '14px';
                hudBadge.style.padding = '6px 10px';
                hudBadge.style.font = '700 13px/1.2 Arial, sans-serif';
                hudBadge.style.letterSpacing = '0.4px';
                hudBadge.style.background = '#ffea00';
                hudBadge.style.color = '#111';
                hudBadge.style.border = '2px solid #111';
                hudBadge.style.borderRadius = '8px';
                hudBadge.style.boxShadow = '0 6px 14px rgba(0,0,0,0.25)';
                hud.appendChild(hudBadge);
                document.body.appendChild(hud);

                if (!el) {
                    setTimeout(() => {
                        const activeHud = document.getElementById('__bot_focus_hud');
                        if (activeHud) activeHud.remove();
                    }, durationMs);
                    return;
                }

                const rect = el.getBoundingClientRect();
                if (!rect || rect.width <= 1 || rect.height <= 1) {
                    setTimeout(() => {
                        const activeHud = document.getElementById('__bot_focus_hud');
                        if (activeHud) activeHud.remove();
                    }, durationMs);
                    return;
                }

                const old = document.getElementById('__bot_focus_overlay');
                if (old) old.remove();

                const overlay = document.createElement('div');
                overlay.id = '__bot_focus_overlay';
                overlay.style.position = 'fixed';
                overlay.style.left = `${Math.max(0, rect.left - 3)}px`;
                overlay.style.top = `${Math.max(0, rect.top - 3)}px`;
                overlay.style.width = `${Math.max(8, rect.width + 6)}px`;
                overlay.style.height = `${Math.max(8, rect.height + 6)}px`;
                overlay.style.border = `5px solid ${color}`;
                overlay.style.background = 'rgba(255, 234, 0, 0.22)';
                overlay.style.boxShadow = `0 0 0 3px rgba(255, 234, 0, 0.65), 0 10px 28px rgba(0,0,0,0.35)`;
                overlay.style.borderRadius = '8px';
                overlay.style.pointerEvents = 'none';
                overlay.style.zIndex = '2147483647';

                // Also force an outline on the element itself in case overlay is clipped.
                el.style.setProperty('outline', `5px solid ${color}`, 'important');
                el.style.setProperty('outline-offset', '2px', 'important');
                el.style.setProperty('box-shadow', '0 0 0 3px rgba(255, 234, 0, 0.65)', 'important');

                if (label) {
                    const badge = document.createElement('div');
                    badge.textContent = label;
                    badge.style.position = 'absolute';
                    badge.style.top = '-24px';
                    badge.style.left = '-1px';
                    badge.style.padding = '2px 8px';
                    badge.style.font = '600 11px/1.3 Arial, sans-serif';
                    badge.style.letterSpacing = '0.4px';
                    badge.style.background = color;
                    badge.style.color = '#111';
                    badge.style.borderRadius = '999px';
                    badge.style.boxShadow = '0 4px 12px rgba(0,0,0,0.25)';
                    overlay.appendChild(badge);
                }

                document.body.appendChild(overlay);
                setTimeout(() => {
                    const active = document.getElementById('__bot_focus_overlay');
                    if (active) active.remove();
                    const activeHud = document.getElementById('__bot_focus_hud');
                    if (activeHud) activeHud.remove();
                    el.style.removeProperty('outline');
                    el.style.removeProperty('outline-offset');
                    el.style.removeProperty('box-shadow');
                }, durationMs);
                """,
                element,
                color,
                label,
            )
        except Exception as e:
            # Best-effort visual aid only; never break automation flow.
            log.debug("Highlight failed: %s", str(e)[:200])
            return

    def _click(self, element) -> None:
        self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
        self._sleep(0.5, 1.2)
        self._highlight(element, color="#ffea00", label="CLICK")
        self._sleep(0.25, 0.45)
        element.click()