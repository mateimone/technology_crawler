import asyncio
import copy
from collections import defaultdict, deque
from pathlib import Path
from time import perf_counter

from playwright.async_api import BrowserContext, async_playwright

from fingerprint_loader import compile_definition_patterns, load_webappanalyzer
from technology_collection import *
from util import write_results

ROOT = Path(__file__).parent.parent
SITES_FILE = ROOT / 'sites.jsonl'
RESULTS_FILE = ROOT / 'results_2.json'
DEFAULT_TIMEOUT = 30_000
compiled_patterns: dict[str, dict[str, Any]] = {}

def read_sites(file: Path):
    sites = []
    for line in file.read_text(encoding='utf-8-sig').splitlines():
        site = line.strip()
        if not site:
            continue
        if site not in sites:
            sites.append(site)
    return sites

def requirements_for(definition):
    return set(definition.get('requires', [])) | set(definition.get('requiresCategory', []))

async def browser_starter():
    technologies = load_webappanalyzer()
    compiled_patterns.clear()
    original_queue: deque[tuple[dict[str, Any], Detection]] = deque()
    found_techs = set()
    results = {}

    for definition in technologies.values():
        compile_definition_patterns(definition, compiled_patterns)
        unmet_requirements = requirements_for(definition)
        detection = Detection(definition['name'], '', 0, [], unmet_requirements)
        original_queue.append((definition, detection))

    async with async_playwright() as p:
        headless_browser = await p.chromium.launch(headless=True)
        browser = await p.chromium.launch(headless=False)
        try:
            for site in read_sites(SITES_FILE):
                for beg in ('https://', 'http://'):
                    url = beg + site
                    context = await headless_browser.new_context(ignore_https_errors=True) # get rid of certificate errors
                    try:
                        context.set_default_timeout(DEFAULT_TIMEOUT)
                        status, detections = await inspect_url(url, context, copy.deepcopy(original_queue), technologies)
                        found_techs.update(detections)
                        write_results(results, site, detections, RESULTS_FILE, failed=not 0 < status < 400)
                        if 0 < status < 400:
                            mode = 'headless'
                            break
                    finally:
                        await context.close()

                    context = await browser.new_context()
                    try:
                        context.set_default_timeout(DEFAULT_TIMEOUT)
                        status, detections = await inspect_url(url, context, copy.deepcopy(original_queue), technologies)
                        found_techs.update(detections)
                        write_results(results, site, detections, RESULTS_FILE, failed=not 0 < status < 400)
                        if 0 < status < 400:
                            mode = 'headed'
                            break
                    finally:
                        await context.close()
                tech_string = ', '.join(str(dtx) for dtx in detections.values())
                if 0 < status < 400:
                    print(f'{url} | {mode} | {tech_string}')
                else:
                    print(f'{url} | Failed | {tech_string}')
                print(f"Unique technologies so far: {len(found_techs)}")

            total_detections = sum(
                sum(name != 'failed' for name in site_results)
                for site_results in results.values()
            )
            average_detections = total_detections / len(results) if results else 0
            print(f'Average technologies per website: {average_detections:.2f}')
        finally:
            await headless_browser.close()
            await browser.close()


async def inspect_url(url: str, context: BrowserContext, queue: deque[tuple[dict[str, Any], Detection]], technologies: dict[str, Any]):
    script_responses: list[Response] = []                   # responses of every script
    finished_requests: set[Request] = set()                 # set of finished requests
    navigation_responses: list[Response] = []               # responses pertaining to navigation requests
    headers: list[dict[str, str]] = []                      # headers obtained until final page was reached
    detections: dict[str, Detection] = {}                   # detections for this domain
    for definition, detection in queue:
        detection.requirements = requirements_for(definition)
    xhr_hosts: list[str] = []
    page: Page = await context.new_page()

    try:
        ''' ALL HEADERS, EXTERNAL SCRIPT RESPONSES, NAVIGATION RESPONSES '''
        response_collector = await collect_response(page, headers, navigation_responses, script_responses)
        def request_finished_marker(request: Request):
            finished_requests.add(request)

        ''' XHR REQUESTS '''
        request_collector = await collect_request(xhr_hosts)

        page.on('response', response_collector)
        page.on('requestfinished', request_finished_marker)
        page.on('request', request_collector)
        response = await page.goto(url, wait_until='domcontentloaded', timeout=DEFAULT_TIMEOUT)

        if response is None:
            return -1, {}

        await page.wait_for_load_state('domcontentloaded', timeout=DEFAULT_TIMEOUT)
        await page.wait_for_timeout(2000)

        html = await collect_html(page)
        meta_tags = await collect_meta(page)
        external_script_sources = await collect_script_sources(page)
        inline_scripts = await collect_inline_scripts(page)
        cookies = await collect_cookies(context, page)
        # response.text() can wait for an unfinished body. Only read scripts whose
        # requestfinished event has already fired.
        finished_script_responses = [
            script_response
            for script_response in script_responses
            if script_response.request in finished_requests
        ]
        external_scripts = await collect_external_scripts(finished_script_responses)
        js_values = await collect_js(page, queue)
        dom_values = await collect_dom(page, queue)

        js_scripts = external_scripts + inline_scripts # scripts
        # String keys are technology names; integer keys are category IDs.
        awaiting_requirements = defaultdict(list)
        available_requirements = set()

        def save_current():
            save_detection(awaiting_requirements, detection, detections, queue,
                           technologies, available_requirements)

        final_response = navigation_responses[-1] if len(navigation_responses) > 0 else response
        while queue:
            definition, detection = queue.popleft()
            detection.requirements = requirements_for(definition) - available_requirements
            if detection.requirements:
                for req in detection.requirements:
                    awaiting_requirements[req].append((definition, detection))
                continue

            detect_headers(definition, headers, compiled_patterns, detection)
            if detection.confidence == 100: save_current(); continue

            detect_cookies(definition, cookies, compiled_patterns, detection)
            if detection.confidence == 100: save_current(); continue

            detect_meta(definition, meta_tags, compiled_patterns, detection)
            if detection.confidence == 100: save_current(); continue

            detect_js(definition, js_values, compiled_patterns, detection)
            if detection.confidence == 100: save_current(); continue

            detect_xhr(definition, xhr_hosts, compiled_patterns, detection)
            if detection.confidence == 100: save_current(); continue

            detect_script_sources(definition, external_script_sources, compiled_patterns, detection)
            if detection.confidence == 100: save_current(); continue

            detect_dom(definition, dom_values, compiled_patterns, detection)
            if detection.confidence == 100: save_current(); continue

            detect_html(definition, html, compiled_patterns, detection)
            if detection.confidence == 100: save_current(); continue

            detect_scripts(definition, js_scripts, compiled_patterns, detection)
            # if detection.confidence == 100: save_current(); continue

            if detection.source != []:
                save_current()
    except Exception as e:
        return -1, {name: detection for name, detection in detections.items() if detection.confidence >= 50}
    finally:
        if page is not None:
            page.remove_listener('response', response_collector)
            page.remove_listener('requestfinished', request_finished_marker)
            page.remove_listener('request', request_collector)

    return final_response.status if final_response is not None else -1, {
        name: detection for name, detection in detections.items() if detection.confidence >= 50
    }

def save_detection(awaiting_requirements, detection, detections, queue,
                   technologies, available_requirements):
    if not detection.source:
        return
    add_or_aggregate_detection(detections, copy.deepcopy(detection))
    current = detections[detection.name]
    if current.confidence < 50:
        return

    available_requirements.add(current.name)
    definition = technologies.get(current.name)
    if definition:
        available_requirements.update(definition.get('cats', []))

    for requirement in list(awaiting_requirements):
        if requirement not in available_requirements:
            continue
        for waiting_definition, waiting_detection in awaiting_requirements.pop(requirement):
            waiting_detection.requirements.discard(requirement)
            if not waiting_detection.requirements:
                queue.append((waiting_definition, waiting_detection))


def add_or_aggregate_detection(detections: dict[str, Detection], dtx: Detection):
    original = detections.get(dtx.name)
    if original is None:
        detections[dtx.name] = dtx
    else:
        detections[dtx.name] += dtx

if __name__ == '__main__':
    started_at = perf_counter()
    try:
        asyncio.run(browser_starter())
    finally:
        elapsed = perf_counter() - started_at
        print(f'Total crawl time: {elapsed:.2f} seconds ({elapsed / 60:.2f} minutes)')


# first try - 428 technologies, 50 minutes
# second try -  (only collect text from finished requests, but always collect headers)
