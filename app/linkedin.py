"""
LinkedIn interaction layer: login check, company search, people scrape, connect.
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
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from app.xpath_selectors import Selectors, LINKEDIN_BASE_URL

log = logging.getLogger(__name__)

@dataclass
class Person:
    name: str
    title: str
    profile_url: str

@dataclass
class Company:
    name: str
    url: str 

@dataclass
class PersonOnPage:
    person: Person
    _li: "LinkedIn"

    def connect(self, note: str = "") -> tuple[str, str]:
        return self._li._connect_by_profile_url(self.person.profile_url, note)


class LinkedIn:
    BASE = LINKEDIN_BASE_URL

    def __init__(self, profile_dir: str, chrome_binary: str = "", headless: bool = False, focus_highlight: bool = True):
        self.driver = self._build_driver(profile_dir, chrome_binary, headless)
        self.wait = WebDriverWait(self.driver, 15)
        self.focus_highlight = focus_highlight
        log.info("Focus highlight enabled: %s", self.focus_highlight)

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

    @staticmethod
    def _sleep(lo: float, hi: float) -> None:
        time.sleep(random.uniform(lo, hi))

    def _goto(self, url: str) -> None:
        self.driver.get(url)
        self.wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))

    def quit(self) -> None:
        try:
            self.driver.quit()
        except Exception:
            pass

    def ensure_logged_in(self) -> None:
        self._goto(f"{self.BASE}/feed/")
        self._sleep(2, 4)
        if any(path in self.driver.current_url for path in ["/login", "/checkpoint"]):
            print("\n[!] Not logged in. A Chrome window is open — please log in manually.")
            print("    After you reach your LinkedIn feed, press Enter here to continue...")
            input()
            self._goto(f"{self.BASE}/feed/")
            self._sleep(2, 4)
            if "/login" in self.driver.current_url:
                raise RuntimeError("Still not logged in after manual prompt.")
        log.info("Logged in.")

    def _apply_dropdown_filter(self, items: list[str], section_xpath: str, input_xpath: str, option_xpath_fmt: str, color: str, label_prefix: str) -> None:
        if not items:
            return

        section = self.wait.until(EC.element_to_be_clickable((By.XPATH, section_xpath)))
        self._highlight(section, color=color, label=f"{label_prefix}_SECTION")
        if self.focus_highlight:
            time.sleep(0.3)
            
        section.click()
        self._sleep(1, 2)

        for item in items:
            log.info("Selecting %s: %s", label_prefix.lower(), item)
            input_box = self.wait.until(EC.element_to_be_clickable((By.XPATH, input_xpath)))
            self._highlight(input_box, color="#ff00ff", label=f"{label_prefix}_INPUT")
            
            input_box.clear()
            input_box.send_keys(item)
            self._sleep(1, 2)

            try:
                option = self.wait.until(EC.element_to_be_clickable((By.XPATH, option_xpath_fmt.format(value=item))))
                self._highlight(option, color="#ffaa00", label=f"{label_prefix}: {item}")
                if self.focus_highlight:
                    time.sleep(0.3)
                option.click()
            except TimeoutException:
                log.info("No matching filter for %s: %s", label_prefix.lower(), item)
            finally:
                input_box.send_keys(Keys.CONTROL, "a")
                input_box.send_keys(Keys.DELETE)

            self._sleep(1, 3)

    def _apply_company_filters(self, locations: list[str], company_sizes: list[str], industries: list[str]) -> None:
        if not locations and not company_sizes and not industries:
            return

        log.info("Applying company filters: locations=%s, company_sizes=%s, industries=%s", locations, company_sizes, industries)

        all_filters = self.wait.until(EC.element_to_be_clickable((By.XPATH, Selectors.ALL_FILTERS_BUTTON)))
        self._highlight(all_filters, color="#00ff00", label="ALL_FILTERS")
        if self.focus_highlight:
            time.sleep(0.3)
        all_filters.click()
        self._sleep(1, 2)

        self._apply_dropdown_filter(locations, Selectors.LOCATION_SECTION, Selectors.LOCATION_INPUT, Selectors.LOCATION_OPTION, "#00bfff", "LOCATION")
        self._apply_dropdown_filter(industries, Selectors.INDUSTRY_SECTION, Selectors.INDUSTRY_INPUT, Selectors.INDUSTRY_OPTION, "#00bfff", "INDUSTRY")

        if company_sizes:
            size_section = self.wait.until(EC.presence_of_element_located((By.XPATH, Selectors.COMPANY_SIZE_SECTION)))
            self._highlight(size_section, color="#ff8800", label="COMPANY_SIZE_SECTION")
            self._sleep(1, 2)

            options = size_section.find_elements(By.XPATH, Selectors.COMPANY_SIZE_OPTION)
            configured_sizes = {size.strip().lower() for size in company_sizes}

            for option in options:
                text = option.text.strip()
                if not text or text.lower() not in configured_sizes:
                    continue

                self._highlight(option, color="#ff4444", label=f"SIZE: {text}")
                if self.focus_highlight:
                    time.sleep(0.3)
                option.click()
                self._sleep(1, 2)

        apply_button = self.wait.until(EC.element_to_be_clickable((By.XPATH, Selectors.APPLY_BUTTON)))
        self._highlight(apply_button, color="#00ff00", label="SHOW_RESULTS")
        if self.focus_highlight:
            time.sleep(0.3)
        apply_button.click()
        self._sleep(2, 4)

    def search_companies(self, keyword: str, limit: int, locations: list[str] | None = None, company_sizes: list[str] | None = None, industries: list[str] | None = None, skip_companies: set[str] | None = None) -> list[Company]:
        skip_companies = {company.strip().lower() for company in (skip_companies or set())}
        q = urllib.parse.quote_plus(keyword)
        
        # 1. Navigate to the initial search page
        self._goto(f"{self.BASE}/search/results/companies/?keywords={q}")
        self._sleep(3, 5)

        # 2. Apply all dropdown filters on the first page
        self._apply_company_filters(locations or [], company_sizes or [], industries or [])
        self._sleep(2, 4)

        # Capture the URL with all applied filter parameters so we can paginate it
        base_search_url = self.driver.current_url
        
        seen, results = set(), []
        page = 1

        # 3. Paginate until we hit the limit or run out of results
        while len(results) < limit:
            if page > 1:
                page_url = self._with_page(base_search_url, page)
                self._goto(page_url)
                self._sleep(3, 5)

            # Scroll to the bottom to ensure all lazy-loaded company cards render
            self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            self._sleep(2, 4)

            anchors = self.driver.find_elements(By.CSS_SELECTOR, "a[href*='/company/']")
            new_companies_on_page = 0

            for anchor in anchors:
                self._highlight(anchor, color="#ffea00", label="COMPANY")
                if self.focus_highlight:
                    time.sleep(0.15)

                href = (anchor.get_attribute("href") or "").split("?")[0].rstrip("/")
                if "/company/" not in href:
                    continue

                try:
                    slug = href.split("/company/")[1].split("/")[0]
                except IndexError:
                    continue

                if not slug or slug in seen:
                    continue
                
                seen.add(slug)
                new_companies_on_page += 1

                name = (anchor.text or "").strip().splitlines()[0] if anchor.text else slug
                if name.strip().lower() in skip_companies:
                    continue

                results.append(Company(name=name, url=f"{self.BASE}/company/{slug}/"))
                
                if len(results) >= limit:
                    break

            log.info("Page %d: Found %d companies (Total valid so far: %d/%d)", page, new_companies_on_page, len(results), limit)

            # Guardrail: If a page loads but yields 0 new company profiles, we've reached the end
            if new_companies_on_page == 0:
                log.info("No more company results found. Ending company search early.")
                break
                
            page += 1

        return results

    def _with_title(self, url: str, title: str) -> str:
        parsed = urllib.parse.urlparse(url)
        query = urllib.parse.parse_qs(parsed.query)
        query.update({"origin": ["FACETED_SEARCH"], "title": [f'"{title}"']})
        return urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(query, doseq=True)))

    @staticmethod
    def _with_page(url: str, page: int) -> str:
        if page <= 1: return url
        parsed = urllib.parse.urlparse(url)
        params = [(k, v) for k, v in urllib.parse.parse_qsl(parsed.query) if k != "page"] + [("page", str(page))]
        return urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(params)))

    def iter_employees(self, company: Company, max_pages: int, title_filters: list[str]):
        search_url = self._open_employees_search(company)
        if not search_url:
            return

        seen = set()
        titles = title_filters or [""]

        for title in titles:
            base_url = self._with_title(search_url, title) if title else search_url
            has_pagination = None

            for page in range(1, max_pages + 1):
                self._goto(self._with_page(base_url, page))
                self._sleep(3, 6)

                for _ in range(3):
                    self.driver.execute_script("window.scrollBy(0, 700);")
                    self._sleep(1, 2)
                self.driver.execute_script("window.scrollTo(0, 0);")
                self._sleep(1, 2)

                if has_pagination is None:
                    has_pagination = self._has_people_pagination()

                people, _ = self._collect_page_people([])
                if not people:
                    break

                for person in people:
                    if person.profile_url not in seen:
                        seen.add(person.profile_url)
                        yield PersonOnPage(person=person, _li=self)

                if has_pagination is False:
                    break

    def _open_employees_search(self, company: Company) -> Optional[str]:
        self._goto(company.url)
        self._sleep(3, 5)
        self.driver.execute_script("window.scrollBy(0, 400);")
        self._sleep(1, 2)

        xpaths = [
            "//a[contains(@href, '/search/results/people/') and contains(@href, 'currentCompany')]",
            "//main//a[contains(translate(@aria-label, 'EMPLOY', 'employ'), 'employee') or contains(translate(., 'EMPLOY', 'employ'), 'employee')]"
        ]
        
        for xp in xpaths:
            for link in self.driver.find_elements(By.XPATH, xp):
                self._highlight(link, color="#ffea00", label="OPEN_EMPLOYEES_LINK")
                if self.focus_highlight:
                    time.sleep(0.1)
                href = link.get_attribute("href")
                if href and "/search/results/people/" in href:
                    return href
        return None

    def _has_people_pagination(self) -> bool:
        pagination_xpaths = [
            "/html/body/div/div[2]/div[2]/div[2]/main/div/div/div/div[2]/div/section/div/div/div/div[3]",
            "//main//*[contains(@class, 'artdeco-pagination')]",
            "//main//button[contains(translate(@aria-label, 'NEXT', 'next'), 'next')]",
            "//main//ul[contains(@class, 'artdeco-pagination__pages')]//li"
        ]
        for xp in pagination_xpaths:
            for el in self.driver.find_elements(By.XPATH, xp):
                try:
                    if el.is_displayed():
                        self._highlight(el, color="#ffea00", label="PAGINATION")
                        return True
                except Exception:
                    continue
        return False

    def _collect_page_people(self, filters_lower: list[str]) -> tuple[list[Person], int]:
        cards, cards_by_url = [], {}
        container_xp = "/html/body/div/div[2]/div[2]/div[2]/main/div/div/div/div[2]/div/section/div/div/div/div[1]/div[1]/div"
        
        containers = self.driver.find_elements(By.XPATH, container_xp)
        if containers:
            self._highlight(containers[0], color="#ffea00", label="LIST")
            for c in containers:
                cards.extend(c.find_elements(By.XPATH, "./div[.//a[contains(@href, '/in/')]]"))

        fallback_xpaths = [
            "//main//li[.//a[contains(@href, '/in/')]]",
            "//main//div[contains(@class, 'entity-result') and .//a[contains(@href, '/in/')]]",
            "//main//div[@data-chameleon-result-urn and .//a[contains(@href, '/in/')]]",
        ]
        
        if not cards:
            for xp in fallback_xpaths:
                found = self.driver.find_elements(By.XPATH, xp)
                if found:
                    cards = found
                    break

        people, seen_on_page = [], set()
        for card in cards:
            self._highlight(card, color="#3a86ff", label="SCRAPE")
            try:
                link = card.find_element(By.XPATH, ".//a[contains(@href, '/in/')]")
            except NoSuchElementException:
                continue

            href = (link.get_attribute("href") or "").split("?")[0].rstrip("/")
            if "/in/" not in href or href in seen_on_page:
                continue
            seen_on_page.add(href)

            name, title = self._extract_card_text(card)
            name = name or (link.text or "").strip()
            
            if not name or (filters_lower and not any(f in title.lower() for f in filters_lower)):
                continue
                
            people.append(Person(name=name, title=title, profile_url=href + "/"))

        return people, len(seen_on_page)

    @staticmethod
    def _extract_card_text(card) -> tuple[str, str]:
        lines = [ln.strip() for ln in (card.text or "").splitlines() if ln.strip()]
        noise = {"1st", "2nd", "3rd", "3rd+", "connect", "message", "follow", "pending", "more", "view profile", "status is reachable"}
        filtered = [ln for ln in lines if ln.lower() not in noise]
        return (filtered[0] if filtered else "", filtered[1] if len(filtered) >= 2 else "")

    # ---------- connect from a search-results card ----------

    def _evaluate_profile_status(self, container) -> str | None:
        """
        Guardrail: Checks the profile page for indicators that we shouldn't attempt to connect.
        Returns a status string if a stopping condition is met, otherwise None.
        """
        # Check for 1st-degree connection indicators
        if self._find_text_button(container, ["Message", "Remove Connection"]):
            log.info("Guardrail: Profile is already a 1st-degree connection.")
            return "already_connected"
            
        # Check for pending invitation indicators
        if self._find_text_button(container, ["Pending", "Withdraw"]):
            log.info("Guardrail: Connection request is already pending.")
            return "skipped" # You can change this to "pending" if you update tracker.py
            
        return None

    def _connect_by_profile_url(self, profile_url: str, note: str = "") -> tuple[str, str]:
        original_window = self.driver.current_window_handle
        self.driver.execute_script("window.open(arguments[0], '_blank');", profile_url)
        
        # Switch to the new profile tab
        self.driver.switch_to.window(self.driver.window_handles[-1])
        self._sleep(3, 5)

        try:
            try:
                container = self.driver.find_element(By.TAG_NAME, "main")
            except NoSuchElementException:
                container = self.driver.find_element(By.TAG_NAME, "body")

            # Guardrail 1: Check if we should even try to connect
            early_status = self._evaluate_profile_status(container)
            if early_status:
                return early_status, ""

            # Attempt to find standard Connect link (URL-based)
            connect_link = self._find_connect_link(container, profile_url)
            connect_btn = None
            
            # Guardrail 2: If standard link isn't found, check the "More" dropdown
            if not connect_link:
                more_btn = self._find_text_button(container, ["More"])
                if more_btn:
                    try:
                        self._click(more_btn)
                        self._sleep(1, 2)
                        
                        # Re-evaluate status just in case it was hiding in the dropdown
                        dropdown_status = self._evaluate_profile_status(container)
                        if dropdown_status:
                            return dropdown_status, ""
                            
                        # Look for a button that literally says "Connect" in the dropdown
                        connect_btn = self._find_text_button(container, ["Connect"])
                    except Exception:
                        pass
                        
            # If neither a link nor a button was found, skip
            if not connect_link and not connect_btn:
                log.info("Guardrail: No Connect option available (likely out of network).")
                return "skipped", ""

            # Flow A: We found a dedicated connection URL href
            if connect_link:
                connect_href = connect_link.get_attribute("href")
                if not connect_href:
                    return "failed", ""

                self.driver.execute_script("window.open(arguments[0], '_blank');", connect_href)
                self.driver.switch_to.window(self.driver.window_handles[-1])
                self._sleep(3, 5)
            
            # Flow B: We found a Connect button (likely inside 'More'), which opens a modal
            elif connect_btn:
                self._click(connect_btn)
                self._sleep(2, 3)

            # Handle the actual sending (works for both dedicated page and modal)
            send_xpaths = [
                "//button[@aria-label='Send without a note']",
                "//button[.//span[normalize-space()='Send without a note']]",
                "//button[.//span[normalize-space()='Send']]",
                "//button[normalize-space()='Send']"
            ]
            
            send_target = next((b for xp in send_xpaths for b in self.driver.find_elements(By.XPATH, xp) if b.is_displayed() and b.is_enabled()), None)

            if send_target:
                try:
                    self._click(send_target)
                except Exception:
                    self.driver.execute_script("arguments[0].click();", send_target)
                
                self._sleep(2, 4)
                
                # Guardrail 3: Check for LinkedIn restriction/error modals
                error_xp = "//*[contains(normalize-space(), 'Invitation not sent') or contains(normalize-space(), 'weeks after withdrawing') or contains(normalize-space(), 'reached your connection limit')]"
                error_elements = self.driver.find_elements(By.XPATH, error_xp)
                
                if any(el.is_displayed() for el in error_elements):
                    log.warning("Guardrail: LinkedIn intercepted the request with an error message.")
                    return "failed_error_msg", ""

                return "sent_without_note", ""
                
            return "failed", ""

        finally:
            # Clean up all tabs except the original search page
            for handle in self.driver.window_handles:
                if handle != original_window:
                    self.driver.switch_to.window(handle)
                    self.driver.close()
            self.driver.switch_to.window(original_window)
            self._sleep(1, 2)

    # ---------- selector helpers ----------

    @staticmethod
    def _find_connect_link(card, profile_url: str):
        """Finds the URL-based Connect link."""
        slug = profile_url.rstrip("/").split("/in/")[-1].split("?")[0]
        xp = f".//a[contains(@href, 'custom-invite') and contains(@href, '{slug}')]"
        return next((link for link in card.find_elements(By.XPATH, xp) if link.is_displayed() and link.is_enabled()), None)

    @staticmethod
    def _find_text_button(card, labels: list[str]):
        """Finds utility buttons based on exact visible text (case-sensitive as rendered)."""
        for label in labels:
            # Looks for buttons or spans that equal the text, or div elements acting as buttons
            xp = f".//*[self::button or self::a or @role='button' or self::div[contains(@class, 'pvs-profile-actions')]]" \
                 f"[normalize-space()='{label}' or .//span[normalize-space()='{label}']]"
            
            for b in card.find_elements(By.XPATH, xp):
                if b.is_displayed() and b.is_enabled():
                    return b
        return None

    def _click(self, element) -> None:
        self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
        self._sleep(0.5, 1.2)
        self._highlight(element, color="#ffea00", label="CLICK")
        self._sleep(0.25, 0.45)
        element.click()

    def _highlight(self, element, color: str = "#ffea00", label: str = "") -> None:
        if not self.focus_highlight:
            return
        try:
            self.driver.execute_script("""
                const el = arguments[0]; const color = arguments[1];
                if (!el) return;
                el.style.setProperty('outline', `5px solid ${color}`, 'important');
                el.style.setProperty('outline-offset', '2px', 'important');
                setTimeout(() => {
                    el.style.removeProperty('outline');
                    el.style.removeProperty('outline-offset');
                }, 4200);
            """, element, color)
        except Exception:
            pass