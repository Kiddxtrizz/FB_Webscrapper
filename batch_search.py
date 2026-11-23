"""Scrape many dispensary menus in one unattended run.

Usage:
    python batch_search.py stores.csv
    python batch_search.py stores.xlsx

The spreadsheet needs a column of menu URLs (header containing "url", "link",
"website" or "menu") and ideally a store-name column (header containing
"store", "name", "dispensary" or "location"). See README.md for details.

Results go into a batch_<date>/ folder:
    stores/<store>.csv and .txt   one pair per store (same as product_search.py)
    all_stores.csv                every product from every store, with store + url
    summary.csv                   one row per store: status, product count, notes
    screenshots/<store>.png       what Chrome showed when a store failed

Rerunning the same command on the same day skips stores that already
succeeded, so after fixing a URL (or if the run was interrupted) you can just
run it again.
"""

import argparse
import csv
import re
import sys
import time
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from product_search import (
    PRODUCT_COLUMNS,
    apply_site_redirects,
    collect_category_menu_pages,
    extract_location_from_url,
    extract_products,
    has_product_cards,
    page_body_loaded,
    unique_products,
    write_products_csv,
)


PRODUCT_WAIT_SECONDS = 45
FEW_PRODUCTS_WARNING = 10
SUMMARY_COLUMNS = ["store", "url", "status", "products", "seconds", "notes"]

# Clicks through age gates, "recreational or medical?" prompts and cookie
# banners. Explicit age answers ("I am 21+") are clicked anywhere on the page;
# generic words ("Yes", "Enter", "Continue") only inside a pop-up/overlay so we
# don't click random links on the page.
CLEAR_POPUPS_JS = r"""
const explicit = /^(yes,? )?(i am|i'm|im) (at least |over )?(21|18)\b|^(21|18)\+|^(yes,? )?(i am |i'm )?(over|of legal age)|^i am of (legal )?age|^verify (my )?age|^recreational\b|^adult[- ]use\b|^shop (rec|recreational|adult[- ]use)\b/i;
const generic = /^(yes|yes,? please|enter|enter (the )?(site|store|now)|continue|confirm|i agree|agree|accept|accept( all)?( cookies)?|ok|okay|got it|submit|let me in|start shopping|shop now)\b[.!]?$/i;
const avoid = /\b(no|exit|leave|under|not 21|medical only|decline|reject|cancel|sign ?in|log ?in|sign ?up)\b/i;
const overlay = "[role='dialog'], [aria-modal='true'], [class*='modal' i], [class*='popup' i], [class*='overlay' i], [class*='age' i], [id*='age' i], [class*='gate' i], [class*='verif' i], [class*='cookie' i], [id*='cookie' i], [class*='consent' i]";

const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== "hidden" && s.display !== "none";
};

let clicked = [];
for (const el of document.querySelectorAll("button, a, [role='button'], input[type='button'], input[type='submit'], label")) {
    if (!visible(el)) continue;
    const text = (el.innerText || el.value || el.getAttribute("aria-label") || "").trim().replace(/\s+/g, " ");
    if (!text || text.length > 40 || avoid.test(text)) continue;
    const inOverlay = !!el.closest(overlay);
    if (explicit.test(text) || (inOverlay && generic.test(text))) {
        // Age gates sometimes need a "remember me"/"I am 21" checkbox ticked first.
        const box = el.closest(overlay)?.querySelector("input[type='checkbox']:not(:checked)");
        if (box) box.click();
        el.click();
        clicked.push(text);
    }
}
return clicked;
"""

# Menu platforms that are often embedded in a dispensary's site in an iframe.
# Their page text isn't visible from the outer page, so open the iframe directly.
MENU_IFRAME_HOSTS = (
    "dutchie", "iheartjane", "jane.", "leafly", "weedmaps", "sweede", "meadow",
    "tymber", "blaze", "treez", "dispenseapp", "joint-ecommerce", "jointcommerce",
    "buddi", "alpineiq", "posabit",
)


def read_store_list(path):
    """Return [(store name, url)] from a CSV or Excel file."""
    if path.suffix.casefold() in {".xlsx", ".xlsm"}:
        try:
            import openpyxl
        except ImportError:
            sys.exit(
                "Reading Excel files needs openpyxl: run  pip install -r requirements.txt\n"
                "(or save the spreadsheet as CSV and pass that instead)."
            )
        sheet = openpyxl.load_workbook(path, read_only=True, data_only=True).active
        rows = [
            ["" if cell is None else str(cell).strip() for cell in row]
            for row in sheet.iter_rows(values_only=True)
        ]
    else:
        with path.open(newline="", encoding="utf-8-sig") as file:
            rows = [[cell.strip() for cell in row] for row in csv.reader(file)]

    rows = [row for row in rows if any(row)]
    if not rows:
        sys.exit(f"{path} is empty.")

    def looks_like_url(value):
        return bool(re.match(r"(?i)^(https?://|www\.)\S+$", value)) or bool(
            re.match(r"(?i)^[\w-]+(\.[\w-]+)+(/\S*)?$", value)
        )

    header = [cell.casefold() for cell in rows[0]]
    has_header = not any(looks_like_url(cell) for cell in rows[0])
    data = rows[1:] if has_header else rows

    def find_column(keywords):
        if not has_header:
            return None
        for keyword in keywords:
            for index, title in enumerate(header):
                if keyword in title:
                    return index
        return None

    url_column = find_column(["menu url", "url", "link", "website", "menu", "site"])
    if url_column is None:
        width = max(len(row) for row in data)
        url_column = max(
            range(width),
            key=lambda index: sum(looks_like_url(row[index]) for row in data if index < len(row)),
        )
    name_column = find_column(["store", "dispensary", "name", "location", "competitor"])
    if name_column == url_column:
        name_column = None

    stores = []
    used_names = set()
    for row in data:
        url = row[url_column] if url_column < len(row) else ""
        if not looks_like_url(url):
            continue
        if not url.casefold().startswith("http"):
            url = f"https://{url}"
        name = row[name_column] if name_column is not None and name_column < len(row) else ""
        name = name or extract_location_from_url(url) or urlsplit(url).hostname or "store"
        # Chains can share a name; keep each store's files separate.
        unique_name = name
        suffix = 2
        while unique_name.casefold() in used_names:
            unique_name = f"{name} ({suffix})"
            suffix += 1
        used_names.add(unique_name.casefold())
        stores.append((unique_name, url))

    if not stores:
        sys.exit(f"No menu URLs found in {path}.")
    return stores


def safe_filename(name):
    return re.sub(r"[^\w.-]+", "_", name, flags=re.UNICODE).strip(" ._") or "store"


def open_embedded_menu(driver):
    """If the menu lives in an iframe from a menu platform, open the iframe itself."""
    for frame in driver.find_elements(By.CSS_SELECTOR, "iframe[src]"):
        src = frame.get_attribute("src") or ""
        host = (urlsplit(src).hostname or "").casefold()
        if any(marker in host for marker in MENU_IFRAME_HOSTS):
            print(f"  Menu is embedded from {host}; opening it directly.")
            driver.get(src)
            return True
    return False


def wait_for_products(driver):
    """Clear pop-ups until products show up. Returns a list of notes."""
    notes = []
    opened_iframe = False
    deadline = time.monotonic() + PRODUCT_WAIT_SECONDS
    while time.monotonic() < deadline:
        try:
            if has_product_cards(driver):
                return notes
            clicked = driver.execute_script(CLEAR_POPUPS_JS) or []
        except WebDriverException:
            clicked = []
        if clicked:
            notes.append("clicked " + " / ".join(f'"{text}"' for text in clicked))
            print(f"  Clicked pop-up button(s): {', '.join(clicked)}")
        if not opened_iframe and open_embedded_menu(driver):
            opened_iframe = True
            notes.append("opened embedded menu")
            WebDriverWait(driver, 15).until(page_body_loaded)
        time.sleep(3)
    raise TimeoutException(
        f"no products found after {PRODUCT_WAIT_SECONDS}s "
        "(age/location pop-up not cleared, wrong URL, or unsupported menu layout)"
    )


def scrape_store(driver, name, url, store_dir, screenshot_dir):
    started = time.monotonic()
    notes = []
    try:
        driver.get(url)
        WebDriverWait(driver, 20).until(page_body_loaded)
        notes += wait_for_products(driver)
        apply_site_redirects(driver)
        page_text = collect_category_menu_pages(driver)

        file_stem = safe_filename(name)
        (store_dir / f"{file_stem}.txt").write_text(page_text, encoding="utf-8")
        products = unique_products(extract_products(page_text))
        write_products_csv(store_dir / f"{file_stem}.csv", products)

        if not products:
            status = "no products parsed"
            notes.append("page loaded but no product cards could be parsed; see the .txt file")
        elif len(products) < FEW_PRODUCTS_WARNING:
            status = "check"
            notes.append(f"only {len(products)} products; may be incomplete")
        else:
            status = "ok"
        product_count = len(products)
    except Exception as error:  # one bad store must not stop the batch
        status = "failed"
        product_count = 0
        message = (getattr(error, "msg", None) or str(error) or type(error).__name__).strip()
        notes.append(message.splitlines()[0])
        try:
            driver.save_screenshot(str(screenshot_dir / f"{safe_filename(name)}.png"))
        except WebDriverException:
            pass

    return {
        "store": name,
        "url": url,
        "status": status,
        "products": product_count,
        "seconds": round(time.monotonic() - started),
        "notes": "; ".join(notes),
    }


def read_summary(summary_path):
    if not summary_path.exists():
        return {}
    with summary_path.open(newline="", encoding="utf-8") as file:
        return {row["store"]: row for row in csv.DictReader(file)}


def write_summary(summary_path, results):
    with summary_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=SUMMARY_COLUMNS)
        writer.writeheader()
        writer.writerows(results.values())


def write_combined(combined_path, stores, results, store_dir):
    total = 0
    with combined_path.open("w", newline="", encoding="utf-8") as out:
        writer = csv.DictWriter(out, fieldnames=["store", "url", *PRODUCT_COLUMNS])
        writer.writeheader()
        for name, url in stores:
            store_csv = store_dir / f"{safe_filename(name)}.csv"
            if results.get(name, {}).get("status") == "failed" or not store_csv.exists():
                continue
            with store_csv.open(newline="", encoding="utf-8") as file:
                for row in csv.DictReader(file):
                    writer.writerow({"store": name, "url": url, **row})
                    total += 1
    return total


def main():
    parser = argparse.ArgumentParser(description="Scrape many dispensary menus in one run.")
    parser.add_argument("store_list", type=Path, help="CSV or Excel file of store names and menu URLs")
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="re-scrape every store, even ones that already succeeded today",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="run Chrome without a window (some sites block this; visible is more reliable)",
    )
    args = parser.parse_args()
    chrome_options = webdriver.ChromeOptions()
    if args.headless:
        chrome_options.add_argument("--headless=new")

    stores = read_store_list(args.store_list)
    batch_dir = Path(f"batch_{date.today().isoformat()}")
    store_dir = batch_dir / "stores"
    screenshot_dir = batch_dir / "screenshots"
    store_dir.mkdir(parents=True, exist_ok=True)
    screenshot_dir.mkdir(exist_ok=True)
    summary_path = batch_dir / "summary.csv"
    combined_path = batch_dir / "all_stores.csv"

    previous = {} if args.fresh else read_summary(summary_path)
    results = {name: previous[name] for name, _ in stores if name in previous}
    print(f"Loaded {len(stores)} stores from {args.store_list}. Output folder: {batch_dir}")

    driver = None
    try:
        for number, (name, url) in enumerate(stores, start=1):
            if results.get(name, {}).get("status") in {"ok", "check"} and results[name]["url"] == url:
                print(f"[{number}/{len(stores)}] {name}: already done today, skipping.")
                continue

            print(f"\n[{number}/{len(stores)}] {name}: {url}")
            if driver is None:
                driver = webdriver.Chrome(options=chrome_options)
                driver.set_page_load_timeout(60)
            else:
                # Clear the previous site's cookies (age-gate answers, selected store).
                driver.delete_all_cookies()

            result = scrape_store(driver, name, url, store_dir, screenshot_dir)
            results[name] = result
            write_summary(summary_path, results)  # saved after every store
            print(f"  -> {result['status']}: {result['products']} products ({result['seconds']}s)")

            if result["status"] == "failed":
                # A crashed/stuck browser would break every later store; start fresh.
                try:
                    driver.quit()
                except WebDriverException:
                    pass
                driver = None
    except KeyboardInterrupt:
        print("\nStopped. Progress so far is saved; run the same command again to continue.")
    finally:
        if driver is not None:
            driver.quit()

    write_summary(summary_path, results)
    total = write_combined(combined_path, stores, results, store_dir)

    counts = {}
    for result in results.values():
        counts[result["status"]] = counts.get(result["status"], 0) + 1
    print("\n" + ", ".join(f"{count} {status}" for status, count in sorted(counts.items())))
    print(f"Combined CSV: {combined_path} ({total} products)")
    print(f"Summary:      {summary_path}")
    needs_attention = [r["store"] for r in results.values() if r["status"] != "ok"]
    if needs_attention:
        print(
            "Needs attention (see summary notes and screenshots; run these by hand with "
            "product_search.py if needed): " + ", ".join(needs_attention)
        )


if __name__ == "__main__":
    main()
