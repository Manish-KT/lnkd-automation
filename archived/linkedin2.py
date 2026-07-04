"""
LinkedIn interaction layer: login check, company search, people scrape, connect.

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
from urllib.parse import (
        urlparse,
        parse_qs,
        urlencode,
        urlunparse,
    )

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
    """A scraped person plus a bound .connect() that acts on their card on the current search page."""
    person: Person
    _li: "LinkedIn"

    def connect(self, note: str = "") -> tuple[str, str]:
        return self._li._connect_by_profile_url(self.person.profile_url, note)

# ==========================================================
# Placeholder XPaths
# ==========================================================
ALL_FILTERS_BUTTON = "//span[normalize-space()='All filters']/parent::button"

LOCATION_SECTION = "//button[.//span[normalize-space()='Add a location']]"
LOCATION_INPUT = "//input[@placeholder='Add a location']"
LOCATION_OPTION = "//*[@role='option'][contains(., '{value}')]"

INDUSTRY_SECTION = "//button[.//span[normalize-space()='Add an industry']]"
INDUSTRY_INPUT ="//input[@placeholder='Add an industry']"
INDUSTRY_OPTION = "//*[@role='option'][contains(., '{value}')]"

COMPANY_SIZE_SECTION = "//*[contains(.,'Company size')]"
COMPANY_SIZE_OPTION = "//p[contains(normalize-space(), 'employees')]"

APPLY_BUTTON = "//span[normalize-space()='Show results']/parent::a"


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

    def _apply_company_filters(
        self,
        locations: list[str],
        company_sizes: list[str],
        industries: list[str]
    ) -> None:
        """
        Apply LinkedIn company search filters.
        """

        if not locations and not company_sizes:
            return

        log.info(
            "Applying company filters: locations=%s, company_sizes=%s",
            locations,
            company_sizes,
        )

        wait = WebDriverWait(self.driver, 10)

        # ==========================================================
        # Open All Filters
        # ==========================================================
        all_filters = wait.until(
            EC.element_to_be_clickable(
                (By.XPATH, ALL_FILTERS_BUTTON)
            )
        )

        self._highlight(
            all_filters,
            color="#00ff00",
            label="ALL_FILTERS",
        )

        if self.focus_highlight:
            time.sleep(0.3)

        all_filters.click()

        self._sleep(1, 2)

        # ==========================================================
        # Locations
        # ==========================================================
        if locations:

            location_section = wait.until(
                EC.element_to_be_clickable(
                    (By.XPATH, LOCATION_SECTION)
                )
            )

            self._highlight(
                location_section,
                color="#00bfff",
                label="LOCATION_SECTION",
            )

            if self.focus_highlight:
                time.sleep(0.3)

            location_section.click()

            self._sleep(1, 2)

            for location in locations:

                log.info("Selecting location: %s", location)

                input_box = wait.until(
                    EC.element_to_be_clickable(
                        (By.XPATH, LOCATION_INPUT)
                    )
                )

                self._highlight(
                    input_box,
                    color="#ff00ff",
                    label="LOCATION_INPUT",
                )

                input_box.clear()
                input_box.send_keys(location)

                self._sleep(1, 2)

                try:
                    option = wait.until(
                        EC.element_to_be_clickable(
                            (
                                By.XPATH,
                                LOCATION_OPTION.format(value=location),
                            )
                        )
                    )

                    self._highlight(
                        option,
                        color="#ffaa00",
                        label=f"LOCATION: {location}",
                    )

                    if self.focus_highlight:
                        time.sleep(0.3)

                    option.click()
                
                except:
                    log.info("No matching filter for location: %s", location)
                
                finally:
                    # Always reset the search box for the next industry
                    input_box.send_keys(Keys.CONTROL, "a")
                    input_box.send_keys(Keys.DELETE)

                self._sleep(1, 3)
        
        # ==========================================================
        # Industry
        # ==========================================================
        if industries:

            industry_section = wait.until(
                EC.element_to_be_clickable(
                    (By.XPATH, INDUSTRY_SECTION)
                )
            )

            self._highlight(
                industry_section,
                color="#00bfff",
                label="INDUSTRY_SECTION",
            )

            if self.focus_highlight:
                time.sleep(0.3)

            industry_section.click()

            self._sleep(1, 2)

            for industry in industries:

                log.info("Selecting industry: %s", industry)

                input_box = wait.until(
                    EC.element_to_be_clickable(
                        (By.XPATH, INDUSTRY_INPUT)
                    )
                )

                self._highlight(
                    input_box,
                    color="#ff00ff",
                    label="INDUSTRY_INPUT",
                )

                input_box.clear()
                input_box.send_keys(industry)

                self._sleep(1, 2)

                try:
                    option = wait.until(
                        EC.element_to_be_clickable(
                            (
                                By.XPATH,
                                INDUSTRY_OPTION.format(value=industry),
                            )
                        )
                    )

                    self._highlight(
                        option,
                        color="#ffaa00",
                        label=f"INDUSTRY: {industry}",
                    )

                    if self.focus_highlight:
                        time.sleep(0.3)

                    option.click()
                
                except:
                    log.info("No matching filter for industry: %s", industry)
                
                finally:
                    # Always reset the search box for the next industry
                    input_box.send_keys(Keys.CONTROL, "a")
                    input_box.send_keys(Keys.DELETE)

                self._sleep(1, 2)

        # ==========================================================
        # Company Size
        # ==========================================================
        if company_sizes:

            company_size_section = wait.until(
                EC.presence_of_element_located(
                    (By.XPATH, COMPANY_SIZE_SECTION)
                )
            )

            self._highlight(
                company_size_section,
                color="#ff8800",
                label="COMPANY_SIZE_SECTION",
            )

            self._sleep(1, 2)

            # Get every option containing "employees" within the section
            options = company_size_section.find_elements(
                By.XPATH,
                COMPANY_SIZE_OPTION
            )

            configured_sizes = {
                size.strip().lower()
                for size in company_sizes
            }

            log.info("Configured company sizes: %s", configured_sizes)

            for option in options:

                text = option.text.strip()

                if not text:
                    continue

                log.info("Found company size option: %s", text)

                if text.lower() not in configured_sizes:
                    continue

                self._highlight(
                    option,
                    color="#ff4444",
                    label=f"SIZE: {text}",
                )

                if self.focus_highlight:
                    time.sleep(0.3)

                option.click()

                self._sleep(1, 2)

        # ==========================================================
        # Show Results
        # ==========================================================
        apply_button = wait.until(
            EC.element_to_be_clickable(
                (By.XPATH, APPLY_BUTTON)
            )
        )

        self._highlight(
            apply_button,
            color="#00ff00",
            label="SHOW_RESULTS",
        )

        if self.focus_highlight:
            time.sleep(0.3)

        apply_button.click()

        self._sleep(2, 4)

    def search_companies(
        self,
        keyword: str,
        limit: int,
        locations: list[str] | None = None,
        company_sizes: list[str] | None = None,
        industries: list[str] | None = None,
        skip_companies: set[str] | None = None,
    ) -> list[Company]:
        """
        Search LinkedIn companies using the configured filters.

        Parameters
        ----------
        keyword:
            Search keyword.

        limit:
            Maximum number of companies to return after filtering.

        skip_companies:
            Optional set of company names to ignore
            (typically loaded from the outreach tracker).
        """

        skip_companies = {
            company.strip().lower()
            for company in (skip_companies or set())
        }

        q = urllib.parse.quote_plus(keyword)
        url = f"{self.BASE}/search/results/companies/?keywords={q}"

        self._goto(url)
        self._sleep(3, 5)

        if locations or company_sizes or industries:
            self._apply_company_filters(
                locations=locations or [],
                company_sizes=company_sizes or [],
                industries=industries or [],
            )

        self._sleep(2, 4)

        # Load additional results.
        self.driver.execute_script("window.scrollBy(0, 800);")
        self._sleep(1, 2)

        anchors = self.driver.find_elements(
            By.CSS_SELECTOR,
            "a[href*='/company/']",
        )

        seen: set[str] = set()
        results: list[Company] = []

        for anchor in anchors:

            self._highlight(
                anchor,
                color="#ffea00",
                label="COMPANY",
            )

            if self.focus_highlight:
                time.sleep(0.15)

            href = (
                (anchor.get_attribute("href") or "")
                .split("?")[0]
                .rstrip("/")
            )

            if "/company/" not in href:
                continue

            try:
                slug = href.split("/company/")[1].split("/")[0]
            except IndexError:
                continue

            if not slug or slug in seen:
                continue

            seen.add(slug)

            name = (
                (anchor.text or "").strip().splitlines()[0]
                if anchor.text
                else slug
            )

            normalized_name = name.strip().lower()

            # Skip companies already processed.
            if normalized_name in skip_companies:
                log.info(
                    "Skipping company (already processed): %s",
                    name,
                )
                continue

            log.info("Found company: %s", name)

            results.append(
                Company(
                    name=name,
                    url=f"{self.BASE}/company/{slug}/",
                )
            )

            if len(results) >= limit:
                break

        log.info(
            "Found %d new companies for '%s'",
            len(results),
            keyword,
        )

        return results

    # ---------- employee search flow ----------
    def _with_title(
        self,
        url: str,
        title: str,
    ) -> str:
        """
        Add a LinkedIn title filter to an employee search URL.
        """

        parsed = urlparse(url)

        query = parse_qs(parsed.query)

        query["origin"] = ["FACETED_SEARCH"]
        query["title"] = [f'"{title}"']

        return urlunparse(
            parsed._replace(
                query=urlencode(query, doseq=True),
            )
        )

    def iter_employees(
        self,
        company: Company,
        max_pages: int,
        title_filters: list[str],
    ):
        """
        Open the company's employee search and yield PersonOnPage objects.

        If title filters are configured, LinkedIn is queried once per title
        (in the order provided). This preserves title priority while greatly
        reducing the number of profiles that need to be scraped.

        Duplicate profiles (for example someone matching both CEO and Founder)
        are skipped automatically.
        """

        search_url = self._open_employees_search(company)

        if not search_url:
            log.warning(
                "No employees search link found for %s — skipping",
                company.name,
            )
            return
    
        seen: set[str] = set()

        # Search titles in priority order.
        titles = title_filters or [""]

        for title in titles:

            if title:
                log.info("Searching title '%s' for %s", title, company.name)
                base_url = self._with_title(search_url, title)
            else:
                base_url = search_url

            has_pagination: Optional[bool] = None

            for page in range(1, max_pages + 1):

                page_url = self._with_page(base_url, page)

                self._goto(page_url)
                self._sleep(3, 6)

                # Trigger lazy loading.
                for _ in range(3):
                    self.driver.execute_script(
                        "window.scrollBy(0, 700);"
                    )
                    self._sleep(1, 2)

                self.driver.execute_script(
                    "window.scrollTo(0, 0);"
                )

                self._sleep(1, 2)

                if has_pagination is None:

                    has_pagination = self._has_people_pagination()

                    if not has_pagination:
                        log.info(
                            "No pagination detected for %s (%s)",
                            company.name,
                            title or "all",
                        )

                # No title filtering needed anymore.
                people, total_profiles = self._collect_page_people([])

                log.info(
                    "Title '%s' | Page %d: %d profiles found",
                    title or "ALL",
                    page,
                    total_profiles,
                )

                if not people:
                    break

                for person in people:

                    if person.profile_url in seen:
                        continue

                    seen.add(person.profile_url)

                    yield PersonOnPage(
                        person=person,
                        _li=self,
                    )

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
        """Open profile in a new tab, extract the connect URL, open it in another tab, and send."""
        original_window = self.driver.current_window_handle
        
        # 1. Open the profile URL in a new tab (Tab 2)
        self.driver.execute_script("window.open(arguments[0], '_blank');", profile_url)
        for window_handle in self.driver.window_handles:
            if window_handle != original_window:
                self.driver.switch_to.window(window_handle)
                break
                
        # Wait for the profile page to load
        self._sleep(3, 5)

        try:
            try:
                container = self.driver.find_element(By.TAG_NAME, "main")
            except Exception:
                container = self.driver.find_element(By.TAG_NAME, "body")

            connect_link = self._find_connect_link(container, profile_url)
            
            if connect_link is None:
                if self._find_text_button(container, ["Message"]) is not None:
                    return "already_connected", ""
                if self._find_text_button(container, ["Pending"]) is not None:
                    return "skipped", ""
                    
                more_btn = self._find_text_button(container, ["More"])
                if more_btn is not None:
                    try:
                        self._click(more_btn)
                        self._sleep(1, 2)
                        connect_link = self._find_connect_link(container, profile_url) 
                    except Exception:
                        connect_link = None
                        
            if connect_link is None:
                return "skipped", ""

            # 2. Extract the href URL instead of clicking it
            connect_href = connect_link.get_attribute("href")
            if not connect_href:
                return "failed", ""

            profile_window = self.driver.current_window_handle
            
            # 3. Open the actual connection URL in a new tab (Tab 3)
            self.driver.execute_script("window.open(arguments[0], '_blank');", connect_href)
            
            # Switch to the new connection tab
            for window_handle in self.driver.window_handles:
                if window_handle != original_window and window_handle != profile_window:
                    self.driver.switch_to.window(window_handle)
                    break

            self._sleep(3, 5)

            # 4. Find and click the Send button on this dedicated page
            send_btn = None
            
            xpaths = [
                "//button[@aria-label='Send without a note']",
                "//button[.//span[normalize-space()='Send without a note']]",
                "//button[.//span[normalize-space()='Send']]",
                "//button[normalize-space()='Send']"
            ]
            
            for xp in xpaths:
                try:
                    buttons = self.driver.find_elements(By.XPATH, xp)
                    for b in buttons:
                        if b.is_displayed() and b.is_enabled():
                            send_btn = b
                            break
                except Exception:
                    continue
                if send_btn:
                    break

            if send_btn:
                try:
                    self._click(send_btn)
                except Exception:
                    self.driver.execute_script("arguments[0].click();", send_btn)
                
                # Wait for the action to process and any potential error toasts to appear
                self._sleep(2, 4)
                
                # ---------- NEW LOGIC: Check for LinkedIn Error Messages ----------
                try:
                    # Look for elements containing standard error keywords
                    error_xp = (
                        "//*[contains(normalize-space(), 'Invitation not sent') or "
                        "contains(normalize-space(), 'weeks after withdrawing') or "
                        "contains(normalize-space(), 'reached your connection limit')]"
                    )
                    error_elements = self.driver.find_elements(By.XPATH, error_xp)
                    
                    for el in error_elements:
                        if el.is_displayed():
                            # Extract text and log it
                            error_text = el.text.replace('\n', ' ').strip()
                            log.warning(f"LinkedIn Error Intercepted: {error_text}")
                            return "failed_error_msg", ""
                except Exception:
                    # If no error element is found or an exception occurs, proceed normally
                    pass
                # ------------------------------------------------------------------

                return "sent_without_note", ""
            else:
                return "failed", ""

        finally:
            # 5. Clean up ALL tabs and return to the main tab
            current_handles = self.driver.window_handles
            for handle in current_handles:
                if handle != original_window:
                    self.driver.switch_to.window(handle)
                    self.driver.close()
            
            # Switch focus safely back to your original search page
            self.driver.switch_to.window(original_window)
            self._sleep(1, 2)

    # ---------- selector helpers ----------

    @staticmethod
    def _find_connect_link(card, profile_url: str):
        """
        Finds the Connect link by looking for the custom-invite href 
        containing the user's vanity name, ignoring visible text labels entirely.
        """
        slug = profile_url.rstrip("/").split("/in/")[-1].split("?")[0]
        xp = f".//a[contains(@href, 'custom-invite') and contains(@href, '{slug}')]"

        try:
            links = card.find_elements(By.XPATH, xp)
            for link in links:
                if link.is_displayed() and link.is_enabled():
                    return link
        except Exception:
            pass
            
        return None

    @staticmethod
    def _find_text_button(card, labels: list[str]):
        """
        Finds utility buttons (Message, Pending, More) based on visible text.
        Checks anchors and buttons to account for LinkedIn DOM variations.
        """
        for label in labels:
            xp = (
                f".//*[self::button or self::a or @role='button']"
                f"[normalize-space()='{label}' or .//span[normalize-space()='{label}']]"
            )
            try:
                buttons = card.find_elements(By.XPATH, xp)
                for b in buttons:
                    if b.is_displayed() and b.is_enabled():
                        return b
            except Exception:
                continue
        return None

    def _close_modal(self) -> None:
        try:
            close = self.driver.find_element(
                By.XPATH, "//div[@role='dialog']//button[@aria-label='Dismiss']"
            )
            if close.is_displayed():
                self._click(close)
                self._sleep(1, 2)
        except Exception:
            pass

    def _highlight(self, element, color: str = "#ffea00", label: str = "") -> None:
        """Draw a short-lived overlay around an element to show current automation focus."""

        if not self.focus_highlight:
            return

        try:
            self.driver.execute_script(
                """
                const el = arguments[0];
                const color = arguments[1];
                const label = arguments[2];
                const durationMs = 4200;

                // Always-visible viewport frame (fallback HUD)
                const oldHud = document.getElementById('__bot_focus_hud');
                if (oldHud) oldHud.remove();

                const hud = document.createElement('div');
                hud.id = '__bot_focus_hud';
                hud.style.position = 'fixed';
                hud.style.inset = '6px';
                hud.style.border = `6px solid ${color}`;
                hud.style.borderRadius = '12px';
                hud.style.boxShadow = `0 0 0 4px ${color}80, inset 0 0 0 2px rgba(0,0,0,0.2)`;
                hud.style.pointerEvents = 'none';
                hud.style.zIndex = '2147483647';

                const hudBadge = document.createElement('div');
                hudBadge.textContent = label ? `BOT FOCUS: ${label}` : 'BOT FOCUS';
                hudBadge.style.position = 'fixed';
                hudBadge.style.top = '14px';
                hudBadge.style.left = '14px';
                hudBadge.style.padding = '6px 10px';
                hudBadge.style.font = '700 13px Arial';
                hudBadge.style.background = color;
                hudBadge.style.color = '#111';
                hudBadge.style.border = '2px solid #111';
                hudBadge.style.borderRadius = '8px';
                hud.appendChild(hudBadge);

                document.body.appendChild(hud);

                if (!el) {
                    setTimeout(() => hud.remove(), durationMs);
                    return;
                }

                const rect = el.getBoundingClientRect();
                if (!rect || rect.width <= 1 || rect.height <= 1) {
                    setTimeout(() => hud.remove(), durationMs);
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
                overlay.style.background = `${color}33`;
                overlay.style.boxShadow = `0 0 0 3px ${color}AA, 0 10px 28px rgba(0,0,0,.35)`;
                overlay.style.borderRadius = '8px';
                overlay.style.pointerEvents = 'none';
                overlay.style.zIndex = '2147483647';

                el.style.setProperty('outline', `5px solid ${color}`, 'important');
                el.style.setProperty('outline-offset', '2px', 'important');
                el.style.setProperty('box-shadow', `0 0 0 3px ${color}AA`, 'important');

                if (label) {
                    const badge = document.createElement('div');
                    badge.textContent = label;
                    badge.style.position = 'absolute';
                    badge.style.top = '-24px';
                    badge.style.left = '-1px';
                    badge.style.padding = '2px 8px';
                    badge.style.font = '600 11px Arial';
                    badge.style.background = color;
                    badge.style.color = '#111';
                    badge.style.borderRadius = '999px';
                    overlay.appendChild(badge);
                }

                document.body.appendChild(overlay);

                setTimeout(() => {
                    overlay.remove();
                    hud.remove();
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
            log.debug("Highlight failed: %s", str(e)[:200])

    def _click(self, element) -> None:
        self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
        self._sleep(0.5, 1.2)
        self._highlight(element, color="#ffea00", label="CLICK")
        self._sleep(0.25, 0.45)
        element.click()