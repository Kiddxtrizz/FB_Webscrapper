import csv
import re
from datetime import date
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit

from selenium import webdriver
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    NoAlertPresentException,
    StaleElementReferenceException,
    TimeoutException,
    UnexpectedAlertPresentException,
)


PRICE_PATTERN = re.compile(
    r"\$\s*(\d+(?:,\d{3})*(?:\.\d{1,2})?)"
    r"(?:\s*/\s*(\d+(?:\.\d+)?\s*(?:mg|mcg|g|kg|oz|ml|l|pk|pack|ct|count)))?",
    re.IGNORECASE,
)
SIZE_PATTERN = re.compile(
    r"(?<![\w:])(\d+(?:\.\d+)?\s*(?:mg|mcg|g|kg|oz|ml|l|pk|pack|ct|count|cones)\b)",
    re.IGNORECASE,
)
FRACTIONAL_SIZE_PATTERN = re.compile(r'(?<![\w:])((?:\d+\s+)?\d+/\d+\s*["″])')
NON_PRODUCT_LINES = {
    "featured",
    "special offer",
    "staff pick",
    "multiple specials",
    "add to cart",
    "add to bag",
}
PAGE_RANGE_PATTERN = re.compile(
    r"(\d+)\s*-\s*(\d+)\s+of\s+([\d,]+)\s+results",
    re.IGNORECASE,
)


def get_page_marker(driver):
    page_text = driver.find_element(By.TAG_NAME, "body").text
    page_range = PAGE_RANGE_PATTERN.search(page_text)
    return page_range.group(0) if page_range else page_text


def find_next_page_control(driver, next_page_number=None, prefer_numbered=False):
    for attempt in range(3):
        numbered_controls = []
        next_controls = []
        try:
            elements = driver.find_elements(
                By.CSS_SELECTOR,
                "nav[aria-label*='pagination'] button, "
                "nav[aria-label*='pagination'] a, "
                "nav[aria-label*='pagination'] [role='button'], "
                "[role='navigation'][aria-label*='pagination'] button, "
                "[role='navigation'][aria-label*='pagination'] a, "
                "[role='navigation'][aria-label*='pagination'] [role='button']",
            )
            if not elements:
                elements = driver.find_elements(By.CSS_SELECTOR, "button, a, [role='button']")

            for element in elements:
                text = (element.text or "").strip().casefold()
                accessible_label = " ".join(
                    (element.get_attribute(name) or "")
                    for name in ("aria-label", "title", "data-testid", "rel")
                ).casefold()
                class_name = (element.get_attribute("class") or "").casefold()
                icon_labels = " ".join(
                    " ".join(
                        (icon.get_attribute(name) or "")
                        for name in ("aria-label", "title", "class", "data-icon")
                    )
                    for icon in element.find_elements(
                        By.CSS_SELECTOR, "svg, [class*='arrow'], [class*='chevron']"
                    )
                ).casefold()
                semantic_label = re.sub(r"[\s_-]+", " ", accessible_label)
                if any(
                    marker in f"{class_name} {icon_labels} {semantic_label}"
                    for marker in ("carousel", "slider", "go to slide")
                ):
                    continue
                is_next_control = (
                    text in {"next", "next page", "load more", ">", "›", "»", "→"}
                    or any(
                        phrase in semantic_label
                        for phrase in ("next", "load more", "rel next")
                    )
                    or any(
                        marker in f"{class_name} {icon_labels}"
                        for marker in ("chevron-right", "arrow-right", "pagination-next", "icon-next")
                    )
                )
                is_numbered_next = next_page_number is not None and (
                    text == str(next_page_number)
                    or semantic_label.strip() in {
                        f"page {next_page_number}",
                        f"go to page {next_page_number}",
                    }
                )
                is_disabled = (
                    not element.is_enabled()
                    or element.get_attribute("aria-disabled") == "true"
                )
                if not is_disabled:
                    if is_numbered_next:
                        numbered_controls.append(element)
                    elif is_next_control:
                        next_controls.append(element)
        except StaleElementReferenceException as error:
            if attempt == 2:
                raise RuntimeError(
                    "The menu kept refreshing while pagination controls were being checked."
                ) from error
            continue

        preferred_controls = numbered_controls if prefer_numbered else next_controls
        fallback_controls = next_controls if prefer_numbered else numbered_controls
        return next(iter(preferred_controls or fallback_controls), None)


def page_has_advanced(driver, previous_marker, previous_start):
    page_text = driver.find_element(By.TAG_NAME, "body").text
    page_range = PAGE_RANGE_PATTERN.search(page_text)
    if previous_start is not None and page_range:
        return int(page_range.group(1)) > previous_start
    return page_text != previous_marker


def scroll_menu_until_stable(driver, page_text):
    while True:
        driver.execute_script(
            "window.scrollTo(0, document.body.scrollHeight);"
        )
        try:
            WebDriverWait(driver, 5).until(
                lambda browser: (
                    browser.find_element(By.TAG_NAME, "body").text != page_text
                )
            )
        except TimeoutException:
            return page_text

        page_text = driver.find_element(By.TAG_NAME, "body").text
        print("Scrolled menu to load additional content.")


def collect_menu_pages(driver):
    page_texts = []
    seen_markers = set()

    while True:
        page_text = driver.find_element(By.TAG_NAME, "body").text
        page_range = PAGE_RANGE_PATTERN.search(page_text)
        marker = page_range.group(0) if page_range else page_text
        if marker in seen_markers:
            break

        seen_markers.add(marker)
        page_texts.append(page_text)
        print(f"Captured menu page {len(page_texts)}: {marker[:80]}")

        next_page_number = None
        page_start = None
        if page_range:
            page_start = int(page_range.group(1))
            page_end = int(page_range.group(2))
            page_size = page_end - page_start + 1
            next_page_number = (page_start - 1) // page_size + 2

        next_control = find_next_page_control(
            driver,
            next_page_number,
            prefer_numbered=True,
        )
        if next_control is None:
            page_text = scroll_menu_until_stable(driver, page_text)
            page_texts[-1] = page_text
            page_range = PAGE_RANGE_PATTERN.search(page_text)
            marker = page_range.group(0) if page_range else page_text
            next_page_number = None
            page_start = None
            if page_range:
                page_start = int(page_range.group(1))
                page_end = int(page_range.group(2))
                page_size = page_end - page_start + 1
                next_page_number = (page_start - 1) // page_size + 2
            next_control = find_next_page_control(
                driver,
                next_page_number,
                prefer_numbered=True,
            )

        if next_control is None:
            if page_range:
                end_index = int(page_range.group(2))
                total_count = int(page_range.group(3).replace(",", ""))
                if end_index < total_count:
                    raise RuntimeError(
                        f"Found {page_range.group(0)}, but could not identify an "
                        "enabled Next/Load More control. The export was stopped "
                        "instead of silently saving only part of the menu."
                    )
            break

        driver.execute_script(
            "arguments[0].scrollIntoView({block: 'center', inline: 'nearest', behavior: 'instant'});",
            next_control,
        )
        try:
            WebDriverWait(driver, 10).until(
                lambda browser: (
                    next_control.is_displayed()
                    and next_control.is_enabled()
                    and browser.execute_script(
                        "const rect = arguments[0].getBoundingClientRect(); "
                        "return rect.top >= 0 && rect.bottom <= window.innerHeight;",
                        next_control,
                    )
                )
            )
            next_control.click()
        except ElementClickInterceptedException:
            print("Pagination click was intercepted; retrying with pointer actions.")
            ActionChains(driver).move_to_element(next_control).pause(0.2).click().perform()

        try:
            WebDriverWait(driver, 60).until(
                lambda browser: page_has_advanced(browser, marker, page_start)
            )
        except TimeoutException as error:
            raise RuntimeError(
                f"The menu stayed on {marker} for 60 seconds after the page click."
            ) from error

    return "\n\n".join(page_texts)


def has_product_cards(driver):
    page_text = driver.find_element(By.TAG_NAME, "body").text
    has_cart_action = re.search(r"(?im)^\s*Add to (?:cart|bag)\s*$", page_text)
    return bool(has_cart_action and PRICE_PATTERN.search(page_text))


def collect_category_menu_pages(driver):
    current_url = urlsplit(driver.current_url)
    menu_urls = [(current_url.geturl(), "current")]
    seen_urls = {current_url._replace(query="", fragment="").geturl().rstrip("/")}
    current_segments = [segment.casefold() for segment in current_url.path.split("/") if segment]
    menu_prefix = current_segments[:-1]
    has_menu_prefix = any(
        segment in {"menu", "category", "categories"}
        for segment in menu_prefix
    )
    category_names = {
        "all",
        "accessories",
        "beverage",
        "concentrate",
        "edible",
        "flower",
        "gear",
        "grow",
        "merch",
        "pre roll",
        "preroll",
        "tincture",
        "topical",
        "vape",
    }

    for link in driver.find_elements(By.CSS_SELECTOR, "a[href]"):
        category_url = link.get_attribute("href")
        if not category_url:
            continue

        parsed_url = urlsplit(category_url)
        path_segments = [segment for segment in parsed_url.path.split("/") if segment]
        normalized_segments = [segment.casefold() for segment in path_segments]
        normalized_label = re.sub(r"[^a-z]+", " ", (link.text or "").casefold()).strip()
        is_sibling_category = (
            has_menu_prefix
            and len(normalized_segments) == len(current_segments)
            and normalized_segments[:-1] == menu_prefix
        )
        if (
            parsed_url.hostname != current_url.hostname
            or not path_segments
            or any(segment.casefold() in {"product", "products"} for segment in path_segments)
            or not (
                normalized_label in category_names
                or any(segment.casefold() in {"category", "categories"} for segment in path_segments)
                or is_sibling_category
            )
        ):
            continue

        normalized_url = parsed_url._replace(query="", fragment="").geturl().rstrip("/")
        if normalized_url not in seen_urls:
            menu_urls.append((normalized_url, (link.text or path_segments[-1]).strip()))
            seen_urls.add(normalized_url)

    page_texts = []
    for category_url, category_name in menu_urls:
        if category_name != "current":
            print(f"Opening menu category: {category_name}")
            driver.get(category_url)
            try:
                WebDriverWait(driver, 30).until(has_product_cards)
            except TimeoutException:
                print(
                    f"No products appeared in the {category_name} category; "
                    "skipping it and continuing."
                )
                continue
        page_texts.append(collect_menu_pages(driver))

    return "\n\n".join(page_texts)


def extract_products(page_text):
    """Extract product fields from menu text with one card per cart action."""
    products = []
    card_blocks = re.split(r"(?im)^\s*Add to (?:cart|bag)\s*$", page_text)

    for block in card_blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        price_index = next(
            (index for index, line in enumerate(lines) if PRICE_PATTERN.search(line)),
            None,
        )
        if price_index is None:
            continue

        price_match = PRICE_PATTERN.search(lines[price_index])
        candidates = [
            line for line in lines[:price_index]
            if line.casefold() not in NON_PRODUCT_LINES
            and not line.casefold().startswith("only a few left")
        ]
        if len(candidates) < 2:
            continue

        product_index = next(
            (
                index for index, line in enumerate(candidates)
                if "|" in line.strip("| ")
            ),
            1,
        )
        if product_index == 0:
            continue

        product_name = candidates[product_index]
        category = next(
            (line for line in candidates[product_index + 1:] if "|" not in line),
            "",
        )
        if not category:
            category_hints = (
                ("paper", "Accessories"),
                ("wrap", "Accessories"),
                ("cone", "Accessories"),
                ("preroll", "Pre-Rolls"),
                ("pre-roll", "Pre-Rolls"),
                ("flower", "Flower"),
                ("vaporizer", "Vaporizers"),
                ("gumm", "Edibles"),
                ("beverage", "Beverages"),
                ("concentrate", "Concentrates"),
                ("topical", "Topicals"),
                ("tincture", "Tinctures"),
            )
            category = next(
                (label for hint, label in category_hints if hint in product_name.casefold()),
                "",
            )
        size_match = SIZE_PATTERN.search(product_name) or FRACTIONAL_SIZE_PATTERN.search(product_name)
        size = size_match.group(1) if size_match else (price_match.group(2) or "")

        products.append({
            "brand": candidates[product_index - 1],
            "product": product_name,
            "category": category,
            "size": size,
            "price": float(price_match.group(1).replace(",", "")),
            "price_text": lines[price_index],
        })

    return products


def extract_location_from_url(menu_url):
    """Get a readable store/location name from a menu URL."""
    parsed_url = urlsplit(menu_url if "://" in menu_url else f"https://{menu_url}")
    query_locations = {
        "location",
        "locationname",
        "storename",
        "store",
        "dispensary",
        "dispensaryname",
        "slug",
    }
    for key, value in parse_qsl(parsed_url.query):
        normalized_key = re.sub(r"[^a-z]", "", key.casefold())
        value = unquote(value).strip()
        if normalized_key in query_locations and value and not value.isdigit():
            return value

    generic_segments = {
        "dispensary",
        "location",
        "locations",
        "menu",
        "menus",
        "shop",
        "products",
        "catalog",
        "order",
        "online-ordering",
    }
    segments = [unquote(segment).strip() for segment in parsed_url.path.split("/") if segment]
    while segments and segments[-1].casefold() in generic_segments:
        segments.pop()

    for segment in reversed(segments):
        if segment.casefold() not in generic_segments and not segment.isdigit():
            return re.sub(r"[-_]+", " ", segment).strip()
    return ""


def get_export_paths(location):
    safe_location = re.sub(r"[^\w.-]+", "_", location, flags=re.UNICODE).strip(" ._")
    if not safe_location:
        raise ValueError("A valid store/location name is required for export filenames.")

    base_name = f"{safe_location}_{date.today().isoformat()}"
    name = base_name
    suffix = 2
    while Path(f"{name}.txt").exists() or Path(f"{name}.csv").exists():
        name = f"{base_name}_{suffix}"
        suffix += 1

    return Path(f"{name}.txt"), Path(f"{name}.csv")


def main():
    menu_url = input("Competitor menu URL: ").strip()
    if not menu_url:
        raise ValueError("A competitor menu URL is required.")

    driver = webdriver.Chrome()
    try:
        driver.get(menu_url)

        def page_body_loaded(browser):
            try:
                return browser.find_element(By.TAG_NAME, "body")
            except UnexpectedAlertPresentException:
                try:
                    alert = browser.switch_to.alert
                    print(f"Dismissing browser alert: {alert.text}")
                    alert.accept()
                except NoAlertPresentException:
                    print("Browser alert closed before it could be dismissed; retrying.")
                return False

        WebDriverWait(driver, 15).until(
            page_body_loaded
        )
        input(
            "In Chrome, complete any age/location prompts and open the menu. "
            "Press Enter here when the products are visible..."
        )
        current_url = urlsplit(driver.current_url)
        if (
            current_url.hostname in {"rootedinroxbury.com", "www.rootedinroxbury.com"}
            and current_url.path.rstrip("/") == ""
        ):
            driver.get(current_url._replace(path="/shop/menu/all", query="", fragment="").geturl())
            try:
                WebDriverWait(driver, 30).until(has_product_cards)
            except TimeoutException as error:
                raise RuntimeError(
                    "Opened Rooted In's All products menu, but no product cards loaded. "
                    "Confirm the menu is visible in Chrome and try again."
                ) from error
        host = urlsplit(driver.current_url).hostname
        page_text = collect_category_menu_pages(driver)

        suggested_dispensary = extract_location_from_url(menu_url)
        if suggested_dispensary:
            print(f"URL-based dispensary name suggestion: {suggested_dispensary}")
        dispensary = input("Dispensary name for export filenames: ").strip()
        if not dispensary:
            raise ValueError("A dispensary name is required for export filenames.")
        text_path, csv_path = get_export_paths(dispensary)

        with text_path.open("w", encoding="utf-8") as file:
            file.write(page_text)

        products = extract_products(page_text)
        with csv_path.open("w", newline="", encoding="utf-8") as file:
            columns = ["brand", "product", "category", "size", "price", "price_text"]
            writer = csv.DictWriter(file, fieldnames=columns)
            writer.writeheader()
            writer.writerows(products)

        print(
            f"Saved raw menu text to {text_path} and "
            f"{len(products)} products to {csv_path}."
        )
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
