# FB_Webscrapper

Tools for collecting competitor dispensary menu data.

| Script | Status | Purpose |
| --- | --- | --- |
| `product_search.py` | Working | Scrapes every product from a dispensary's online menu into a CSV |
| `batch_search.py` | Working | Runs the same scrape unattended for a whole list of stores |

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

## Running many stores: `batch_search.py`

Put the stores in a spreadsheet (CSV or Excel) with a **menu URL** column and a **store name** column. Headers are matched loosely: anything containing `url`, `link`, `website` or `menu` is the URL; `store`, `dispensary`, `name` or `location` is the name. Other columns (address, distance, …) are ignored. Use the URL of the store's **menu page**, not its homepage, when you can.

```bash
python batch_search.py stores.xlsx
```

For each store the script opens the menu, clicks through common age gates ("I am 21+", "Yes", "Enter"), "recreational?" prompts, cookie banners and email sign-up pop-ups ("No thanks", "Close"; never the close button on an age gate), opens embedded Dutchie/Jane/etc. menus directly, then scrapes every category exactly like `product_search.py`. No typing is needed while it runs. Expect roughly 1–2 minutes per store.

**Helping it through pop-ups:** if a store's products haven't appeared after ~15 s of automatic tries, the script beeps, prints `>>> <store> NEEDS YOUR HELP`, stops clicking, and waits **30 s** for you to clear the pop-ups in Chrome. It carries on by itself as soon as products show. Change the wait with `--help-time 60`, or use `--help-time 0` for an unattended run (stores it can't clear are just logged as failed).

Results go into `batch_<date>/`:

| File | Contents |
| --- | --- |
| `all_stores.csv` | Every product from every store, with `store` and `url` columns added |
| `summary.csv` | One row per store: `status`, product count, time taken, notes |
| `stores/<store>.csv` / `.txt` | Per-store files, same as `product_search.py` |
| `screenshots/<store>.png` | What Chrome showed when a store failed |

`status` is `ok`, `check` (fewer than 10 products, so look at it), `no products parsed` (page loaded but the layout wasn't recognized; see the `.txt`), `blocked` (the site showed a "verify you are human" / bot check; the script doesn't try to get past these, so collect that store another way), or `failed` (no products appeared within 45 s, usually a pop-up it couldn't clear or a wrong URL; see `notes` and the screenshot).

**Rerunning:** run the same command again the same day and stores that already finished (`ok`/`check`) are skipped, so only the failures are retried. This also lets you resume after **Ctrl + C**. Use `--fresh` to redo everything. For a store that keeps failing, fix its URL in the spreadsheet or run it by hand with `product_search.py`.

`--headless` runs Chrome without a window, but more sites block that, so the default visible window is more reliable.

## How it works

The parser does not depend on site-specific CSS selectors. It reads the page's **visible text** and splits it into product cards at each `Add to cart` / `Add to bag` line. This works across most hosted menu platforms. Expect it to break if a site:

- uses a different call-to-action label (add it to the `re.split` in `extract_products`),
- lays out brand / product / price in a different order, or
- shows prices without a `$`.

Known site-specific handling: `rootedinroxbury.com` is redirected to `/shop/menu/all`.

Curaleaf-platform menus (Curaleaf, Rebelle, …) have no "Add to cart" button. Their cards start with a quantity counter ("0 in cart") and a "<Category> by <Brand>" line, and the script recognizes and parses that layout automatically.

If a menu has no "Add to cart" text but uses an icon-only cart/bag/basket button on each product (recognized from its aria-label, title, test ID, class or icon name), the script inserts an "Add to cart" label next to each icon so the cards can be split. Cart icons in the header, nav, footer or pop-ups are ignored.

The same product can appear on the "All products" page and again on its category page, so identical rows are removed before the CSV is written.

If the script finds an "X–Y of Z results" counter but can't locate a working Next button, it **stops with an error** rather than saving a partial menu.

## Troubleshooting

| Symptom | Likely cause |
| --- | --- |
| `SessionNotCreatedException` / driver errors | Chrome isn't installed or is very outdated, or there's no network access on the first run to download the driver |
| CSV has 0 rows but `.txt` has content | The site uses a different card layout or button label; look at the `.txt` to see what text was captured |
| `could not identify an enabled Next/Load More control` | The site's pagination markup isn't recognized; see `find_next_page_control` |
