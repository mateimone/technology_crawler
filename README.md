# Website Technology Detector

A Python and Playwright crawler that identifies technologies from the evidence a website exposes to a browser: response headers, cookies, metadata, JavaScript, network requests, and the rendered page.

The detector looks for technologies across the WebAppAnalyzer fingerprinting database. This database contains many technologies
and rules to detect them in different parts of a website. 

## Results

The [crawl log](results_full_log) reports 428 unique technology names for the [200-domain input](sites.jsonl).
The JSON output is present inside [results.json](results.json).

| Saved-run metric           | Result |
|----------------------------|-------:|
| Input domains              |    200 |
| Successful headless crawls |    161 |
| Successful headed crawls   |     14 |
| Erroneous domains          |     25 |
| Unique technologies found  |    428 |

These numbers describe the saved run, seen in the [crawl log](results_full_log). Sites in `headless` mode that cannot be crawled due 
to http errors are also tested in a `headed` mode. The [https://disneystore.com](https://disneystore.com) domain is an example
of a website that can only be crawled in `headed` mode. If `HTTPS` fails, the crawler retries the domain over `HTTP`. 
Technologies can also be found in websites that return an unsuccessful status, 
for which reason they are also counted.

## Approach and design decisions

### Use a browser to observe technologies
 
Since many websites are JavaScript heavy, the crawler runs each domain in a browser, being able
to observe many dynamic components.
Playwright provides access to the rendered DOM, runtime JavaScript values, cookies, script responses,
and XHR/fetch requests.

Each domain is tried in this order, stopping when an attempt returns a status between 1 and 399.
If one attempt throws an exception, the next one is tried.

1. HTTPS with headless Chromium.
2. HTTPS with a visible Chromium browser.
3. HTTP with headless Chromium.
4. HTTP with a visible Chromium browser.

Each attempt uses a fresh browser context and checks the website's resources against every 
technology present in the fingerprint database.
The crawler waits for `domcontentloaded` and then another two seconds before collecting page data.
Error responses can still provide technology evidence.

### Used a fingerprint database

The [WebAppAnalyzer](databases/webappanalyzer/README.md) database supplies JSON definitions for 
recognizable technology signatures. It is one of the largest open-source fingerprinting databases.
The presence of a technology on a domain is determined by matching rules corresponding to one or 
more of the following fields.

| Fingerprint field | Evidence checked                                                   |
| --- |--------------------------------------------------------------------|
| `headers` | Headers from main-frame navigation responses, including redirects  |
| `cookies` | First-part cookies of the final page URL                           |
| `meta` | Content of meta tags identified by `name` or `property`            |
| `js` | Stringified values at specified paths on `window`                  |
| `xhr` | Hostnames of observed XHR and fetch requests                       |
| `scriptSrc` | URLs from rendered `script[src]` elements                          |
| `dom` | Main-document selector existence, text, attributes, and properties |
| `html` | Rendered page HTML                                                 |
| `scripts` | Inline script text and collected external script bodies            |

Fields are matched in order of increasing complexity to delay heavier checks as much as possible;
once a technology is present with maximum confidence, the crawler stops and moves to the next 
technology.

Regexes relating to rules are compiled once at startup. For each domain, JavaScript and DOM checks
are batched and performed once, so only one evaluation call is made to the website per these two rules for all
technologies.

The database provides relationships between some technologies, in the sense that one technology can imply or exclude 
the presence of another technology. It can also provide requirements for a technology in the form of an array
of technologies or technology categories that must already be present for the former one to also be present.
The implies/excludes relations were not implemented. See the **Limitations** chapter at the end for more.

## Detection confidence

The above rules may also specify the version and a confidence for the
presence of the technology. If unspecified, the confidence for the presence of the technology is
maximum. The confidence ranges between 0 and 100. 

The crawler currently maintains a detected technology if its confidence is at least 50. 
Whenever a rule corresponding to a technology matches information from the
domain, its confidence value is retrieved and stored. If the confidence is 100, the crawler moves
on to the next technology. If not, it continues checking for other matches and if it finds one, 
adds its given confidence to the cumulated one, being capped at 100.

This is done in the sense that matching rules are independent of each other. As per the WebAppAnalyzer [README](databases/webappanalyzer/README.md),
"the aim is to achieve a combined confidence of 100%". My assumption is this means that if two different rules belonging to a technology
are matched on a given domain, then we should add their confidence together. The full structure of
a WebAppAnalyzer entry is given in their [README](databases/webappanalyzer/README.md).

## Running the project

The environment uses Python 3.14.6 and Playwright 1.62.0. Do not modify file locations.
Clone the project, enter its folder, then install the requirements and run.

```powershell
git clone <repository-url>
cd <repository-folder>

py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe src\crawler.py
```

```bash
git clone <repository-url>
cd <repository-folder>

python3 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt
./.venv/bin/python -m playwright install --with-deps chromium
./.venv/bin/python src/crawler.py
```

### JSON output

The crawler writes to `results_2.json` in the repository root after each attempt. This is such that
restarting the crawler does not overwrite the already existing `results.json`. Each
input domain maps to its detected technologies, with only `version` and
`confidence` for each technology. Only the first version detected is kept.
Unknown versions are `null`. Failed attempts also add `"failed": ""`. 

```json
{
  "abc.com": {
    "tech1": {"version": "1.2", "confidence": 100}
  },
  "failed.com": {
    "tech2": {"version": null, "confidence": 50},
    "failed": ""
  }
}
```

## Project guide

| File                                                         | Responsibility                                          |
|--------------------------------------------------------------|---------------------------------------------------------|
| [src/crawler.py](src/crawler.py)                             | Browser lifecycle, domain crawling, and output          |
| [src/technology_collection.py](src/technology_collection.py) | Evidence collection from domain and per-source matching |
| [src/fingerprint_loader.py](src/fingerprint_loader.py)       | Database loading and regex compilation                  |
| [src/Detection.py](src/Detection.py)                         | Detection object for a possibly present technology      |
| [src/util.py](src/util.py)                                   | Utilities, such as saving to a file                     |
| [results.json](results.json)                                 | 200-domain crawl JSON                                   |
| [results_full_log](results_full_log)                         | Logs from the 200-domain crawl                          |


## Main issues and how I would tackle them

1. **The crawler was very slow.** The main reasons were collecting large external scripts in full and repeatedly applying 
hundreds of regular expressions to their contents. Some expressions could also take
an excessive amount of time to complete. While this meant the crawler missed as little information as 
possible when identifying technologies, it reduced the crawling speed considerably. To address
this, I processed only completed requests, limited retained external source code to three million 
characters in total and 500.000 characters per resource, and introduced a 30-millisecond timeout for
each regular-expression search. Together, these changes reduced the crawling time from approximately 
50 minutes to 26 minutes. <u>**While doing so, the total number of technologies and average number of technologies
found per website barely changed. This means the limits barely hurt the detection.**</u> 

2. **Different database formats.** This crawler uses only one database, but the integration of 
another fingerprinting database would require additional manual work. For this reason, only one database was used. 
However, this process should be separate from the crawling itself. Fingerprinting databases should go 
through a schema mapping process and then be integrated into the full database that is used by the 
crawler.

## Scaling to millions of domains in 1–2 months

The first thing is definitely to start multiple crawlers and make the application concurrent. The code
is already asynchronous, but requires concurrent data structures or locks to function correctly. Work stealing
should also be implemented. 

Next, much harsher limits should be placed for waiting on requests and the amount of characters checked 
for rules. We observed that with the imposed limits we obtained a 50% reduction with 
no issues related to number of detected technologies. If we'd like to crawl 
3 million websites in 30 days, we'd have to crawl 1.15 domains per second, as opposed to 
200/(26*60) = 0.12 domains per second, so roughly a 9x increase. This means we have to further reduce those limits and
add new ones to other resources that are being checked, while observing how the average number of detected 
technologies per domain drops.

If the task is specific, rather than broad, another matter would be reducing the technologies we look for to a 
much smaller subset. For the Shopify example, the database could maintain technologies that represent 
competing products, or technologies that are known to be used in building the competing products.

Finally, we could also reduce the confidence required to classify a technology as present on a domain.
It can be reduced from 100 to 70-80, in order to possibly stop the checks earlier and avoid later heavier
checks.

## Discovering new technologies

To discover new technologies, I see 2 possibilities.
### Continuously integrate new information from the databases that make up our own repository and search for databases that can provide new information
Whenever a database that was used to form our own repository is populated with new technologies or
rules for technologies, we should update our repository as well. We can do this once at a 
given time (ex. per month). Listeners would not be feasible because we would either have to hold the connection
ourselves or have the maintainers notify us, which I assume is improbable. We should also search
for new databases, as they might contain information that we did not have previously.

### Extend our repository with technologies and rules with an in-house algorithm or through machine learning.
Unknown information such as script hosts, JavaScript and DOM elements, cookies, and others, can be
grouped across crawled sites. Repeated signals should be investigated. An in-house (machine learning)
algorithm could do this automatically. 

However, this would also induce a cost on performance. For the JavaScript and DOM fingerprints, the current crawler
reads specific properties from the domain directly rather than collecting them all and then checking them 
internally. Fully inspecting these parts for possible, unknown fingerprints would make the crawl much slower.

## Limitations
Due to time concerns, some limitations are present.

Some properties from the WebAppAnalyzer database were left unchecked on websites. However, 
these are present in a very small amount of technologies, compared to the number of technologies
in the database. Furthermore, implies/excludes relations were not implemented; a correct 
implementation would require a cascading behavior.

The crawler also does not support concurrent workers at the moment.