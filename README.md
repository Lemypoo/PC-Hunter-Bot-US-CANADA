# Item Hunter for Canada and the US

A local price watcher for PC parts, for Canada and the United States. Add a part (for example
`9950X3D`), and Item Hunter searches the stores below on a timer using your own PC and your
installed Edge browser. When a listing gets cheaper, or a new listing appears below your current
best price, it shows up in the feed and you get a notification you can click to open the listing.

| Country | Stores |
| --- | --- |
| Canada | Amazon.ca (new, used and Amazon Resale), Newegg.ca, Best Buy Canada, Canada Computers, eBay.ca, Facebook Marketplace |
| United States | Amazon.com (new, used and Amazon Resale), Newegg.com, eBay.com, Facebook Marketplace |

Pick the country in Settings; the first start takes it from your computer's region setting. Each
country's listings are kept apart, so switching back and forth never mixes Canadian and US prices.
Best Buy US and Micro Center are not included.

**Anywhere else?** Use [Item Hunter International](https://github.com/Lemypoo/PC-Hunter-Bot-International),
which covers every other country with Amazon, eBay and Facebook Marketplace in your own currency.

## Run

Double-click `start.bat`, or:

```
python app.py
```

Your browser opens at http://127.0.0.1:8787/. Leave the window running. Each store is checked
on its own clock, and two stores are read at the same time. At Normal speed that is Best Buy
every 90 seconds, Newegg every 3 minutes, eBay every 4, Facebook every 5, Amazon every 15 and
Canada Computers every 20. Settings has a Fast / Normal / Relaxed switch that scales all of
them, and the number of stores read at once (1 to 4, takes effect after a restart). Amazon and
Canada Computers stay slow on purpose: both start refusing a home connection that searches
them more often. Nothing leaves your machine except the searches themselves and any alerts
you switch on.

## Requirements

Python 3.10+ with `pip install -r requirements.txt`. Scraping uses Microsoft Edge (already on
Windows 11) or Chrome. If neither is installed, run `playwright install chromium` once.

## Phone alerts

Settings has two optional channels besides the Windows notification, both with a test button:

- **Email**: enter the address to notify plus an account to send from. Gmail and Outlook
  need a 16-character app password, not the normal one (Gmail: smtp.gmail.com port 587;
  Outlook: smtp.office365.com port 587). The push channel below needs no password at all.
- **Phone push**: install the free ntfy app, press "Generate topic", subscribe to that topic in
  the app, and alerts arrive as push notifications within seconds. No account needed.

Every channel uses the same rule: a listing that beats an item's best price, drops to within
5% of it, or reaches the item's target price. At most three alerts per store per check.

## Used and open-box offers

Amazon sells used and open-box stock through Amazon Resale (formerly Warehouse Deals), often
well under the new price. Item Hunter reads both the "used & new offers" line on the search
page and the Resale storefront, and shows the cheapest such offer for a product as its own
row marked "used". Alerts treat it like any other listing. Turn it off in Settings with
"Include used and open-box offers" to watch new-in-box prices only.

## Amazon only

Each item has a "Stores to check" list under Edit with an "Amazon only" button, and Settings
has the same button for every item at once.

## Tips

- Only in-stock listings are shown. A listing that goes out of stock disappears from its card
  and comes back (with a "Back in stock" entry in the feed) when the store has it again.
  Listings sold for parts or "as is" are left out.

- Keep the search short: `9950X3D`, `RTX 5080`, `9800X3D`. Every word you type must appear
  in a listing's title for it to count. Edit the "must contain" and "exclude" lists per item.
- Prebuilt PCs and bundles are excluded by default (words like "gaming pc", "bundle").
  Set a max price on the item to cut the rest.
- Set a target price on an item to always get notified when any listing reaches it.
- The feed shows drops, restocks and new listings that land within 15% of an item's current
  best price. A Windows notification fires when a listing beats the best price, or drops to
  within 5% of it, at most three per store per check.
- Bundles are caught by the "+" exclude (matches "CPU + Motherboard + RAM" titles). A CPU
  search also ignores motherboards and graphics cards by default; a GPU search ignores CPUs,
  chipsets, cases, mounts and power supplies "ready for" the card.
- A marketplace listing far below every retail price (the $1 "message me" kind) is tagged
  "price?" and never counts as the best price or triggers a notification.
- Hide a listing you never want to see again with the Hide button on its row.
- Facebook Marketplace searches anonymously within about 65 km of the city in Settings: the
  word after `/marketplace/` when you browse Marketplace for your city, e.g. `vancouver`,
  `toronto` or `nyc`. If Facebook does not know the name it quietly shows a different city;
  Item Hunter notices and says so instead of searching the wrong place.
- If a store blocks the automated browser, a red chip names it at the top of the page and the
  app leaves that store alone for 30 minutes before trying again. Canada Computers does this
  after a burst of searches, and the block also hits your normal browser for a while, so the
  app spaces its requests to each store (45 seconds for Canada Computers, 20 for Amazon, eBay
  and Facebook, less for the rest). If a store keeps blocking, set the speed to Relaxed.
- Memory Express is not included: its Cloudflare check blocks automated browsers.

## Your data

Everything lives in the `data` folder: `state.json` holds your items, price history and
settings (including the SMTP password, in plain text), `state.json.bak` is the previous copy
that the app recovers from if the live file is ever damaged, and the `browser_state` files are
the stores' cookies. The folder is in `.gitignore`, so none of it ends up on GitHub.

## Tests

```
python tests/test_hunter.py
```

About 100 checks covering matching, Amazon page detection, alerts, switching between Canada
and the US, the state file and the parallel checkers. None of them touch the network.
