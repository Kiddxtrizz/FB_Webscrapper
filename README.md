# FB_Webscrapper

Tools for collecting competitor dispensary menu data.

| Script | Status | Purpose |
| --- | --- | --- |
| `product_search.py` | Working | Scrapes every product from a dispensary's online menu into a CSV |

## Requirements

- **Python 3.10+** (required by the pinned Selenium version)
- **Google Chrome** installed locally. You do not need to download ChromeDriver; Selenium (4.6+) fetches a matching driver automatically on first run, so the first run needs internet access.
- The script opens a visible Chrome window and asks you for input, so run it on a machine with a display. It does not work headless or in CI.

## Setup

```bash
git clone <repo-url> FB_Webscrapper
cd FB_Webscrapper

python -m venv .venv
# Windows:      .venv\Scripts\activate
# macOS/Linux:  source .venv/bin/activate

pip install -r requirements.txt
```

## Running `product_search.py`

```bash
python product_search.py
```

The script is interactive:

1. **`Competitor menu URL:`**: paste the dispensary's online menu URL.
2. Chrome opens that page. **In the Chrome window**, clear any age-verification or store/location pop-ups and go to the product menu.
3. Back in the terminal, press **Enter** once products are visible.
4. The script walks through the menu categories it can find, along with pagination and "Load more" / infinite scroll, and gathers every page.
5. **`Dispensary name for export filenames:`**: type a short name (the script suggests one based on the URL).

### Output

Two files are written to the **current working directory**:

| File | Contents |
| --- | --- |
| `<Dispensary>_<YYYY-MM-DD>.txt` | Raw visible text from every menu page captured (useful for debugging parsing) |
| `<Dispensary>_<YYYY-MM-DD>.csv` | One row per product |

If a file with that name already exists, a suffix is added (`_2`, `_3`, …), so existing exports are never overwritten. `.gitignore` excludes these files; please don't commit scraped data.

CSV columns:

| Column | Example | Notes |
| --- | --- | --- |
| `brand` | `Cresco` | Line above the product name on the card |
| `product` | `Sour Diesel \| 3.5g` | |
| `category` | `Flower` | From the card, or guessed from keywords in the product name |
| `size` | `3.5g` | From the product name or the price unit; blank if neither has one |
| `price` | `45.0` | Numeric, first price on the card |
| `price_text` | `$45.00 / 3.5g` | Raw price line as shown |

## How it works

The parser does not depend on site-specific CSS selectors. It reads the page's **visible text** and splits it into product cards at each `Add to cart` / `Add to bag` line. This works across most hosted menu platforms. Expect it to break if a site:

- uses a different call-to-action label (add it to the `re.split` in `extract_products`),
- lays out brand / product / price in a different order, or
- shows prices without a `$`.

Known site-specific handling: `rootedinroxbury.com` is redirected to `/shop/menu/all`.

If the script finds an "X–Y of Z results" counter but can't locate a working Next button, it **stops with an error** rather than saving a partial menu.

## Troubleshooting

| Symptom | Likely cause |
| --- | --- |
| `SessionNotCreatedException` / driver errors | Chrome isn't installed or is very outdated, or there's no network access on the first run to download the driver |
| CSV has 0 rows but `.txt` has content | The site uses a different card layout or button label; look at the `.txt` to see what text was captured |
| `could not identify an enabled Next/Load More control` | The site's pagination markup isn't recognized; see `find_next_page_control` |
