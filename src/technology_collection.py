from typing import Any
from urllib.parse import urlsplit

from playwright.async_api import Page, Request, Response
from playwright.async_api import Error as PlaywrightError

from Detection import Detection


def detect_headers(definition, headers, compiled_patterns, detection: Detection):
    for header_name, raw_pattern in definition.get('headers', {}).items():
        pattern = compiled_patterns[raw_pattern]
        for response_header in headers:
            header_value = response_header.get(header_name.lower())
            if header_value:
                match_and_detect(pattern, header_value, definition['name'], ['headers'], detection)


def detect_cookies(definition, cookies, compiled_patterns, detection: Detection):
    for cookie_name, raw_pattern in definition.get('cookies', {}).items():
        pattern = compiled_patterns[raw_pattern]
        for cookie in cookies:
            if cookie['name'] != cookie_name:
                continue
            match_and_detect(pattern, cookie['value'], definition['name'], ['cookies'], detection)


def detect_meta(definition, meta_tags, compiled_patterns, detection: Detection):
    for property_name, raw_pattern in definition.get('meta', {}).items():
        pattern = compiled_patterns[raw_pattern]
        for meta in meta_tags:
            if meta['name'] != property_name:
                continue
            match_and_detect(pattern, meta['content'], definition['name'], ['meta'], detection)


def detect_js(definition, js_values, compiled_patterns, detection: Detection):
    for property_name, raw_pattern in definition.get('js', {}).items():
        property_value = js_values.get(property_name)
        if property_value is None:
            continue
        match_and_detect(compiled_patterns[raw_pattern], property_value, definition['name'], ['js'], detection)


def detect_xhr(definition, xhr_hosts, compiled_patterns, detection: Detection):
    for raw_pattern in definition.get('xhr', []):
        pattern = compiled_patterns[raw_pattern]
        for hostname in xhr_hosts:
            match_and_detect(pattern, hostname, definition['name'], ['xhr'], detection)


def detect_script_sources(definition, external_script_sources, compiled_patterns, detection: Detection):
    for raw_pattern in definition.get('scriptSrc', []):
        match_and_detect(compiled_patterns[raw_pattern], external_script_sources, definition['name'], ['scriptSrc'], detection)


def detect_dom(definition, dom_values, compiled_patterns, detection: Detection):
    for selector, kind, name, raw_pattern in dom_tests(definition):
        pattern = compiled_patterns[raw_pattern]
        for value in dom_values[(selector, kind, name)]:
            if match_and_detect(pattern, value, definition['name'], ['dom'], detection):
                break


def detect_html(definition, html, compiled_patterns, detection: Detection):
    for raw_pattern in definition.get('html', []):
        match_and_detect(compiled_patterns[raw_pattern], html, definition['name'], ['html'], detection)


def detect_scripts(definition, js_scripts, compiled_patterns, detection: Detection):
    for raw_pattern in definition.get('scripts', []):
        pattern = compiled_patterns[raw_pattern]
        for script in js_scripts:
            match_and_detect(pattern, script, definition['name'], ['scripts'], detection)


def match_and_detect(pattern: dict[str, Any], content: str, tech_name: str, source: list[str], detection: Detection):
    try:
        match = pattern['compiled'].search(content, timeout=0.03)
    except TimeoutError:
        return False
    if match is None:
        return False
    version = ' '
    if pattern['version'] is not None:
        version = match.expand(pattern['version'])
        version = f' {version} '

    detection += Detection(tech_name, version, pattern['confidence'], source)
    return True


async def collect_headers(response, headers):
    headers.insert(0, await response.all_headers())


async def collect_html(page):
    html = await page.content()
    return html


async def collect_meta(page):
    meta_tags = await page.locator('meta[name], meta[property]').evaluate_all('''
        entries => entries.map(entry => {
            return {
                name: entry.getAttribute('name') || entry.getAttribute('property'),
                content: entry.content || ''
            }
        })
    ''')
    return meta_tags


async def collect_script_sources(page):
    external_script_sources = await page.locator('script[src]').evaluate_all(
        'scripts => scripts.map(script => script.src).toString()'
    )
    return external_script_sources


async def collect_inline_scripts(page):
    inline_scripts = await page.locator('script:not([src])').evaluate_all(
        'scripts => scripts.map(script => script.textContent)'
    )
    return inline_scripts


async def collect_cookies(context, page):
    # for third-party cookies, do context.cookies()
    cookies = await context.cookies(page.url) # first-party cookies only
    return cookies


async def collect_external_scripts(script_responses):
    external_scripts = []
    remaining_characters = 3_000_000

    # some responses can correspond to blobs; need to call fetch() on those.
    for script_resp in script_responses:
        if remaining_characters <= 0:
            break
        try:
            code = None
            if script_resp.url.startswith("blob:") and 'challenges.cloudflare' not in script_resp.url:
                code = await script_resp.frame.evaluate(
                    "async url => (await fetch(url)).text()",
                    script_resp.url
                )
            elif 'challenges.cloudflare' not in script_resp.url: # skip challenges.cloudflare.com/... blobs
                code = await script_resp.text()
            # scripts were found in the full text, but downloading all of these seems to be slow on some pages
            if code:
                code = code[:min(remaining_characters, 500_000)]
                external_scripts.append(code)
                remaining_characters -= len(code)
        except PlaywrightError as e:
            print(f'Could not read {script_resp.url}: {e}')
    return external_scripts


async def collect_js(page, queue):
    js_paths = list(dict.fromkeys(
        path for definition, _ in queue for path in definition.get('js', {})
    ))
    js_values = await page.evaluate('''
        paths => Object.fromEntries(paths.map(path => {
            try {
                const value = path.split('.').reduce((obj, key) => obj?.[key], window);
                return [path, value === undefined ? null : String(value)];
            } catch {
                return [path, null];
            }
        }))
    ''', js_paths) if js_paths else {}
    return js_values


def dom_tests(definition):
    dom = definition.get('dom', {})
    if isinstance(dom, str):
        dom = {dom: {'exists': ''}}
    elif isinstance(dom, list):
        dom = {selector: {'exists': ''} for selector in dom}
    for selector, checks in dom.items():
        checks = checks or {'exists': ''}
        for kind in ('exists', 'text'):
            if kind in checks:
                yield selector, kind, None, checks[kind]
        for kind in ('attributes', 'properties'):
            for name, pattern in checks.get(kind, {}).items():
                yield selector, kind, name, pattern


async def collect_dom(page, queue):
    requests = []
    seen_requests = set()

    for definition, detection in queue:
        for selector, check_type, attribute_or_property_name, pattern in dom_tests(definition):
            # The browser only needs to know what value to read, not its regex.
            request = (selector, check_type, attribute_or_property_name)

            # Different technologies may ask for the same DOM value. Read it once.
            if request in seen_requests:
                continue

            requests.append(request)
            seen_requests.add(request)
    if not requests:
        return {}
    result = await page.evaluate('''
        requests => {
            const elementsBySelector = new Map();
            return requests.map(([selector, kind, name]) => {
                if (!elementsBySelector.has(selector)) {
                    try {
                        elementsBySelector.set(selector,
                            [...document.querySelectorAll(selector)]);
                    } catch {
                        elementsBySelector.set(selector, []);
                    }
                }
                const elements = elementsBySelector.get(selector);
                if (kind === 'exists') return elements.length ? [''] : [];
                return elements.map(element => {
                    try {
                        if (kind === 'text') return element.textContent ?? '';
                        if (kind === 'attributes') return element.getAttribute(name);
                        const value = element[name];
                        return value === undefined ? null : String(value);
                    } catch {
                        return null;
                    }
                }).filter(value => value !== null);
            });
        }
    ''', requests)
    return dict(zip(requests, result))


async def collect_response(page: Page, headers: list[dict[str,str]], navigation_responses: list[Response], script_responses: list[Response]):
    async def collect_response(response: Response):
        request = response.request
        # when you are redirected from the site you have, the returned response still corresponds to the initial site.
        # let's collect them all
        if request.is_navigation_request() and request.frame == page.main_frame:
            headers.append(await response.all_headers())
            navigation_responses.append(response)
        if request.resource_type == "script" and response.ok:
            script_responses.append(response)

    return collect_response

async def collect_request(xhr_hosts: list[str]):
    async def collect_request(request: Request):
        if request.resource_type in ('xhr', 'fetch'):
            hostname = urlsplit(request.url).hostname
            if hostname:
                xhr_hosts.append(hostname)

    return collect_request
