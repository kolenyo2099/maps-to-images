import asyncio
import json
import os
import re # For regular expressions
import unicodedata # For sanitizing filenames
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
import logging
import aiohttp # For downloading images asynchronously

# --- Configuration for Debugging & Downloading ---
SAVE_DEBUG_FILES = True
DEBUG_FILE_PREFIX = "gm_debug"
MAIN_DOWNLOAD_DIR = "downloaded_google_maps_images" # Main folder for all downloaded images
MAX_PLACES_TO_PROCESS = 3
# --- End Configuration ---

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Known Google image hosts
IMAGE_HOSTS_REGEX = re.compile(
    r'(?:https?:)?//(?:lh[3-6]\.googleusercontent\.com|[^/]+\.ggpht\.com|[^/]+\.googleusercontent\.com/profile/picture)/[a-zA-Z0-9\-_./=&?%]+'
)
# Filter to exclude very small, icon-like images
TINY_IMAGE_PARAM_REGEX = re.compile(r'[=&?]s(?:1[6-9]|[2-6]\d)($|\W)')


def ensure_dir_exists(dir_path):
    if not os.path.exists(dir_path):
        os.makedirs(dir_path)
        logger.info(f"Created directory: {dir_path}")

def get_debug_filepath(filename_suffix):
    debug_dir = os.path.join(os.getcwd(), "debug_output")
    ensure_dir_exists(debug_dir)
    return os.path.join(debug_dir, f"{DEBUG_FILE_PREFIX}_{filename_suffix}")

def sanitize_filename(name: str, max_length: int = 200) -> str:
    """Sanitizes a string to be used as a valid filename/directory name."""
    if not name or not name.strip():
        name = "unknown_place"
    name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('ascii')
    name = re.sub(r'[\s/\\:\*\?"<>\|;,&]+', '_', name)
    name = re.sub(r'[^\w\-_]', '', name)
    name = re.sub(r'__+', '_', name)
    name = re.sub(r'--+', '-', name)
    name = name.strip('_-')
    if not name:
        name = "sanitized_unknown_place"
    return name[:max_length]


def get_nested_value(data, path, default=None):
    current = data
    for i, key_or_index in enumerate(path):
        if isinstance(key_or_index, int):
            if isinstance(current, list) and 0 <= key_or_index < len(current):
                current = current[key_or_index]
            else: return default
        elif isinstance(key_or_index, str):
            if isinstance(current, dict) and key_or_index in current:
                current = current[key_or_index]
            else: return default
        else: return default
    return current

def find_image_urls_recursively(data_structure, found_urls_set):
    if isinstance(data_structure, dict):
        for key, value in data_structure.items():
            if isinstance(value, str):
                match = IMAGE_HOSTS_REGEX.fullmatch(value)
                if match:
                    url_to_add = match.group(0)
                    if url_to_add.startswith('//'): url_to_add = 'https:' + url_to_add
                    if not TINY_IMAGE_PARAM_REGEX.search(url_to_add):
                        is_profile_pic = "/profile/picture/" in url_to_add
                        is_small_sized_profile_pic = False
                        if is_profile_pic:
                            size_match = re.search(r"[=/]s(\d+)", url_to_add)
                            if size_match and int(size_match.group(1)) < 100:
                                is_small_sized_profile_pic = True
                        if not is_small_sized_profile_pic:
                            found_urls_set.add(url_to_add)
            else: find_image_urls_recursively(value, found_urls_set)
    elif isinstance(data_structure, list):
        for item in data_structure:
            if isinstance(item, str):
                match = IMAGE_HOSTS_REGEX.fullmatch(item)
                if match:
                    url_to_add = item
                    if url_to_add.startswith('//'): url_to_add = 'https:' + url_to_add
                    if not TINY_IMAGE_PARAM_REGEX.search(url_to_add):
                        is_profile_pic = "/profile/picture/" in url_to_add
                        is_small_sized_profile_pic = False
                        if is_profile_pic:
                            size_match = re.search(r"[=/]s(\d+)", url_to_add)
                            if size_match and int(size_match.group(1)) < 100:
                                is_small_sized_profile_pic = True
                        if not is_small_sized_profile_pic:
                            found_urls_set.add(url_to_add)
            else: find_image_urls_recursively(item, found_urls_set)

async def download_image(session: aiohttp.ClientSession, url: str, folder_path: str, image_counter: int):
    try:
        url_no_size = re.sub(r'=[swh]\d+(-[wh]\d+)?(-[a-zA-Z0-9]+)?$', '', url)
        if url_no_size != url:
            logger.debug(f"        Attempting download from modified URL: {url_no_size} (original: {url})")
        else:
            logger.debug(f"        Attempting download from URL: {url}")

        async with session.get(url_no_size, timeout=30, allow_redirects=True) as response:
            response.raise_for_status()
            content_type = response.headers.get('Content-Type', '').lower()
            ext = '.jpg'
            if 'jpeg' in content_type or 'jpg' in content_type: ext = '.jpg'
            elif 'png' in content_type: ext = '.png'
            elif 'gif' in content_type: ext = '.gif'
            elif 'webp' in content_type: ext = '.webp'
            else:
                url_path_part = url.split('?')[0].split('/')[-1]
                if '.' in url_path_part:
                    potential_ext = '.' + url_path_part.split('.')[-1].lower()
                    if potential_ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp']: ext = potential_ext
            image_filename = f"image_{image_counter:03d}{ext}"
            filepath = os.path.join(folder_path, image_filename)
            with open(filepath, 'wb') as f:
                while True:
                    chunk = await response.content.read(8192)
                    if not chunk: break
                    f.write(chunk)
            logger.info(f"      Successfully downloaded: {url_no_size} -> {filepath}")
            return True
    except aiohttp.ClientError as e: logger.error(f"      AIOHTTP ClientError downloading {url_no_size}: {e}")
    except asyncio.TimeoutError: logger.error(f"      Timeout downloading {url_no_size}")
    except Exception as e: logger.error(f"      Error downloading {url_no_size}: {e}", exc_info=False)
    return False

async def extract_images_for_place(page, place_url, place_index=0):
    logger.info(f"  Processing Place {place_index + 1}: Navigating to {place_url}")
    place_data_to_return = {"title": None, "address": None, "image_urls": [], "place_url": place_url}
    raw_json_data_str = None
    json_data_obj = None

    try:
        await page.goto(place_url, wait_until='domcontentloaded', timeout=60000)
        logger.info(f"    Place {place_index + 1}: Page 'domcontentloaded'. URL: {page.url}")
        place_title_selector = "h1"
        try:
            await page.wait_for_selector(place_title_selector, timeout=20000)
            logger.info(f"    Place {place_index + 1}: Key element '{place_title_selector}' found.")
        except PlaywrightTimeoutError:
            logger.warning(f"    Place {place_index + 1}: Key element '{place_title_selector}' not found after 20s.")
            if SAVE_DEBUG_FILES: await page.screenshot(path=get_debug_filepath(f"place_{place_index+1}_key_element_timeout.png"))

        for attempt in range(3):
            logger.info(f"    Place {place_index + 1}: Attempting to extract APP_INITIALIZATION_STATE (Attempt {attempt + 1}/3)...")
            js_expression = "() => { try { return window.APP_INITIALIZATION_STATE[3][6]; } catch (e) { return null; } }"
            evaluated_data = await page.evaluate(js_expression)
            
            if evaluated_data:
                if isinstance(evaluated_data, str):
                    raw_json_data_str = evaluated_data
                    logger.info(f"    Place {place_index + 1}: JS evaluate returned a STRING. Attempting to parse after stripping prefix.")
                    current_json_string = raw_json_data_str[4:] if raw_json_data_str.startswith(")]}'\n") else raw_json_data_str
                    try:
                        json_data_obj = json.loads(current_json_string)
                        logger.info(f"    Place {place_index + 1}: Successfully PARSED string on attempt {attempt + 1}.")
                        break
                    except json.JSONDecodeError as jde:
                        logger.error(f"    Place {place_index + 1}: JSONDecodeError on attempt {attempt + 1}: {jde}. Raw string (first 100 chars): {current_json_string[:100]}")
                        json_data_obj = None
                else:
                    json_data_obj = evaluated_data
                    logger.info(f"    Place {place_index + 1}: JS evaluate returned an OBJECT/ARRAY on attempt {attempt + 1}.")
                    break
            
            if not evaluated_data: logger.warning(f"    Place {place_index + 1}: APP_INITIALIZATION_STATE not directly found by JS evaluate on attempt {attempt + 1}.")
            if attempt < 2: logger.warning(f"    Waiting 3s before retry..."); await asyncio.sleep(3)

        if SAVE_DEBUG_FILES:
            await page.screenshot(path=get_debug_filepath(f"place_{place_index+1}_page_after_js_attempts.png"))
            if raw_json_data_str:
                 with open(get_debug_filepath(f"place_{place_index+1}_RAW_APP_INIT_STATE_3_6.json"), "w", encoding="utf-8") as f: f.write(raw_json_data_str)
                 logger.info(f"    Place {place_index + 1}: Saved RAW string from APP_INITIALIZATION_STATE[3][6]")
            elif json_data_obj :
                with open(get_debug_filepath(f"place_{place_index+1}_PARSED_APP_INIT_STATE_3_6.json"), "w", encoding="utf-8") as f: json.dump(json_data_obj, f, indent=2)
                logger.info(f"    Place {place_index + 1}: Saved PARSED object from APP_INITIALIZATION_STATE[3][6]")
            else: logger.error(f"    Place {place_index + 1}: APP_INITIALIZATION_STATE data is null/empty after all attempts. Cannot save JSON.")

        if json_data_obj:
            logger.debug(f"    Place {place_index + 1}: Type of json_data_obj: {type(json_data_obj)}")
            if isinstance(json_data_obj, list): logger.debug(f"    Place {place_index + 1}: json_data_obj is a LIST. Length: {len(json_data_obj)}")
            
            darray = get_nested_value(json_data_obj, [1, 11, 0, 0]) # Corrected based on user's JSON file
            if darray:
                logger.info(f"    Place {place_index + 1}: Successfully extracted 'darray' (from json_data_obj[1][11][0][0]).")
                title_val = get_nested_value(darray, [1]) # Corrected
                if title_val and isinstance(title_val, str): place_data_to_return["title"] = title_val.strip()
                else: logger.warning(f"      Title not found/string at darray[1]. Type: {type(title_val)}")
                
                addr_list = get_nested_value(darray, [2]) # Corrected (address is a list of parts)
                if addr_list and isinstance(addr_list, list):
                    address_parts = [str(part) for part in addr_list if part is not None]
                    cleaned_addr = ", ".join(address_parts).strip()
                    if place_data_to_return["title"] and cleaned_addr.startswith(place_data_to_return["title"]):
                        cleaned_addr = cleaned_addr[len(place_data_to_return["title"]):].lstrip(", ").strip()
                    place_data_to_return["address"] = cleaned_addr
                elif addr_list:
                     place_data_to_return["address"] = str(addr_list).strip()
                     logger.warning(f"      Address at darray[2] was not a list, converted to string: {place_data_to_return['address']}")
                else: 
                    logger.warning(f"      Address not found or not a list at darray[2]. Type: {type(addr_list)}")
                logger.info(f"      Place {place_index + 1}: Title: '{place_data_to_return['title']}', Address: '{place_data_to_return['address']}'")
            else:
                logger.warning(f"    Place {place_index + 1}: 'darray' (expected at json_data_obj[1][11][0][0]) is STILL missing or None. Title/Address extraction will fail.")

            logger.info(f"    Place {place_index + 1}: Starting recursive search for image URLs in the entire json_data_obj...")
            image_urls_found_recursively = set()
            find_image_urls_recursively(json_data_obj, image_urls_found_recursively)
            place_data_to_return["image_urls"] = sorted(list(image_urls_found_recursively))
            logger.info(f"    Place {place_index + 1}: Recursive search found {len(place_data_to_return['image_urls'])} potential image URLs.")
            if not place_data_to_return["image_urls"]:
                 logger.warning(f"    Place {place_index + 1}: Recursive search did not find any image URLs matching patterns.")
        else: 
             logger.error(f"    Place {place_index + 1}: No json_data_obj to process for details or images.")
    except PlaywrightTimeoutError as pte:
        logger.error(f"    Place {place_index + 1}: Main navigation or key element timeout for {place_url}: {pte}")
        if SAVE_DEBUG_FILES: await page.screenshot(path=get_debug_filepath(f"place_{place_index+1}_nav_timeout_error.png"))
    except Exception as e:
        logger.error(f"    Place {place_index + 1}: Error processing {place_url}: {e}", exc_info=True)
        if SAVE_DEBUG_FILES: await page.screenshot(path=get_debug_filepath(f"place_{place_index+1}_general_error.png"))

    if place_data_to_return.get("image_urls"):
        logger.info(f"    Place {place_index + 1}: Returning data with {len(place_data_to_return['image_urls'])} image URL(s).")
    else:
        logger.info(f"    Place {place_index + 1}: Returning data with no image URLs found.")
    return place_data_to_return

async def get_google_maps_images_data(query: str):
    # Ensure the debug output directory exists before any debug files are written
    debug_output_dir = os.path.join(os.getcwd(), "debug_output")
    ensure_dir_exists(debug_output_dir)
    
    all_places_data = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.4896.127 Safari/537.36",
            java_script_enabled=True, accept_downloads=False, bypass_csp=False
        )
        await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = await context.new_page()
        logger.info(f"Browser launched. Navigating to Google Maps for query: '{query}'")

        try:
            await page.goto("https://www.google.com/maps/place/Kipriakon/data=!4m2!3m1!1s0x14e732fd76f0d90d:0xe5415928d6702b47!10m1!1e18", wait_until='domcontentloaded', timeout=60000)
            logger.info(f"  Navigated to Google Maps. Current URL: {page.url}")
            if SAVE_DEBUG_FILES: await page.screenshot(path=get_debug_filepath("initial_maps_page.png"))

            cookie_consent_selectors = [
                "//button[.//span[contains(translate(text(), 'ACDEILPT', 'acdeilpt'), 'accept all')]]",
                "//button[.//span[contains(translate(text(), 'ACDEILPT', 'acdeilpt'), 'alle akzeptieren')]]",
                "//button[contains(translate(., 'ACDEILPT', 'acdeilpt'), 'accept all')]",
                "//button[contains(translate(., 'ACDEILPT', 'acdeilpt'), 'reject all')]/preceding-sibling::button[1]",
                "//div[contains(@class, 'consent') or contains(@id, 'consent')]//button[contains(translate(., 'ACDEILPT', 'acdeilpt'), 'accept') or contains(translate(., 'ACDEILPT', 'acdeilpt'), 'agree')][1]"
            ]
            consent_accepted = False
            for i, selector in enumerate(cookie_consent_selectors):
                try:
                    consent_button = page.locator(selector).first
                    if await consent_button.is_visible(timeout=2000):
                        logger.info(f"  Cookie consent dialog found with selector {i+1}. Clicking...")
                        await consent_button.click(timeout=3000)
                        await page.wait_for_load_state('domcontentloaded', timeout=5000)
                        if SAVE_DEBUG_FILES: await page.screenshot(path=get_debug_filepath("after_cookie_consent.png"))
                        consent_accepted = True; break
                except PlaywrightTimeoutError: logger.debug(f"  Cookie consent selector {i+1} not visible.")
                except Exception as e: logger.warning(f"  Error with cookie consent selector {i+1}: {e}")
            if not consent_accepted: logger.info("  No cookie consent dialog found or handled.")

            search_input_selector = 'input[name="q"], input[aria-label*="Search Google Maps"], input#searchboxinput'
            await page.fill(search_input_selector, query, timeout=15000)
            await page.press(search_input_selector, 'Enter')
            logger.info(f"  Search submitted for '{query}'. Waiting for results page to load...")

            try:
                await page.wait_for_load_state('domcontentloaded', timeout=25000)
                logger.info(f"  DOM content loaded after search. Current URL: {page.url}")
                await asyncio.sleep(3) 
                logger.info(f"  Waited 3s extra. Current URL: {page.url}")
            except PlaywrightTimeoutError:
                logger.warning(f"  DOM content did not load within 25s or other timeout after search. Proceeding. URL: {page.url}")

            if SAVE_DEBUG_FILES:
                await page.screenshot(path=get_debug_filepath("after_search_and_wait.png"))
                with open(get_debug_filepath("after_search_and_wait.html"), "w", encoding="utf-8") as f: f.write(await page.content())

            place_urls_to_process = []
            if "/maps/place/" in page.url:
                logger.info("  Landed directly on a place page.")
                place_urls_to_process.append(page.url)
            else:
                logger.info("  Attempting to find search results feed/list...")
                feed_selector = 'div[role="feed"]'
                try:
                    await page.wait_for_selector(feed_selector, timeout=30000)
                    logger.info(f"  Search results feed ('{feed_selector}') loaded.")
                    
                    place_link_elements = await page.locator(f'{feed_selector} div[jsaction] a[href*="/maps/place/"]').all()
                    if not place_link_elements:
                        logger.info("  Specific feed link selector found no elements, trying broader link search...")
                        place_link_elements = await page.locator('a[href*="/maps/place/"]').all()
                        
                    logger.info(f"  Found {len(place_link_elements)} potential place link elements.")
                    
                    processed_links_count = 0
                    unique_hrefs = set()
                    for link_el in place_link_elements:
                        full_url = None # Initialize full_url for each link element
                        if processed_links_count >= MAX_PLACES_TO_PROCESS:
                            logger.info(f"  Reached MAX_PLACES_TO_PROCESS limit ({MAX_PLACES_TO_PROCESS})."); break
                        try:
                            href = await link_el.get_attribute('href')
                            aria_label = await link_el.get_attribute('aria-label') or ""
                            inner_text = (await link_el.inner_text() or "").strip()
                            
                            if href and (aria_label or inner_text):
                                full_url = href if href.startswith("http") else f"https://www.google.com{href}"
                                
                                if full_url and full_url not in unique_hrefs: 
                                    logger.debug(f"    Adding valid place link: {full_url} (Label: '{aria_label}', Text: '{inner_text}')")
                                    place_urls_to_process.append(full_url)
                                    unique_hrefs.add(full_url)
                                    processed_links_count += 1
                                elif not full_url: 
                                    logger.warning(f"    Skipping link as full_url was not properly constructed. href: '{href}'")
                        except Exception as el_ex: 
                            logger.error(f"    Error processing a potential link element: {el_ex}")
                    if not place_urls_to_process: logger.warning("  No valid place URLs extracted from feed after filtering.")

                except PlaywrightTimeoutError:
                    logger.error(f"  Timeout waiting for search results feed ('{feed_selector}').")
                    if SAVE_DEBUG_FILES:
                        await page.screenshot(path=get_debug_filepath("feed_timeout_error.png"))
                        with open(get_debug_filepath("feed_timeout_error.html"), "w", encoding="utf-8") as f_timeout: f_timeout.write(await page.content())
                    if "/maps/place/" in page.url: 
                        logger.info("  Although feed timed out, current URL is a place page. Processing this page."); place_urls_to_process.append(page.url)
                    else: logger.warning("  No results feed and not a direct place page.")
            
            if not place_urls_to_process: logger.warning("No place URLs identified to process.")
            else: logger.info(f"Will process {len(place_urls_to_process)} place(s) for images.")

            for i, place_url in enumerate(place_urls_to_process):
                place_info = await extract_images_for_place(page, place_url, place_index=i)
                if place_info and (place_info.get("title") or place_info.get("address") or place_info.get("image_urls")):
                    all_places_data.append(place_info)
                else:
                     logger.warning(f"  No significant data extracted for place {i+1} ({place_url}), not adding to results list.")
                if i < len(place_urls_to_process) - 1:
                    logger.info(f"  Pausing for 1 second before next place..."); await asyncio.sleep(1)
        except PlaywrightTimeoutError as pte:
            logger.error(f"Major timeout during main navigation or search for '{query}': {pte}")
            if SAVE_DEBUG_FILES: await page.screenshot(path=get_debug_filepath("major_timeout_error.png"))
        except Exception as e:
            logger.error(f"An unexpected error occurred in main data extraction: {e}", exc_info=True)
            if SAVE_DEBUG_FILES:
                try: await page.screenshot(path=get_debug_filepath("unexpected_error_extraction.png"))
                except Exception as page_err: logger.error(f"Could not save debug screenshot during error: {page_err}")
        finally:
            logger.info("Closing browser after data extraction."); await browser.close()
    return all_places_data

async def main_with_downloads():
    search_query = input("Enter Google Maps search query (e.g., restaurants in Berkeley): ")
    if not search_query.strip():
        search_query = "restaurants in Berkeley"
        logger.info(f"No query entered, using default: '{search_query}'")

    logger.info(f"\n--- Starting Data Extraction for: '{search_query}' ---")
    # Ensure the function name matches how it's defined
    extracted_data = await get_google_maps_images_data(search_query) 

    if extracted_data:
        json_output_filename = "google_maps_extracted_data.json"
        ensure_dir_exists(os.getcwd())
        with open(json_output_filename, "w", encoding="utf-8") as outfile:
            json.dump(extracted_data, outfile, indent=2)
        logger.info(f"Saved all extracted data to '{json_output_filename}'")

        print("\n--- Extracted Place Information Summary ---")
        for i, place in enumerate(extracted_data):
            print(f"\n--- Place {i+1} ---")
            print(f"Title: {place.get('title', 'N/A')}")
            print(f"Address: {place.get('address', 'N/A')}")
            print(f"Place URL: {place.get('place_url', 'N/A')}") # Changed from place['place_url']
            image_urls = place.get('image_urls', [])
            if image_urls:
                print(f"Image URLs ({len(image_urls)}):")
                for url_idx, url in enumerate(image_urls[:5]):
                    print(f"  - {url}")
                if len(image_urls) > 5:
                    print(f"  - ... and {len(image_urls)-5} more.")
            else: print("Image URLs: None found")
        logger.info(f"\nTotal places with some data extracted: {len(extracted_data)}")

        logger.info("\n--- Starting Image Downloads ---")
        ensure_dir_exists(MAIN_DOWNLOAD_DIR)
        async with aiohttp.ClientSession() as http_session:
            for place_idx, place_info in enumerate(extracted_data):
                place_title_for_folder = place_info.get('title')
                if not place_title_for_folder:
                    place_title_for_folder = f"unknown_place_{place_idx + 1}_{sanitize_filename(place_info.get('place_url', ''))[:30]}"
                
                sanitized_folder_name = sanitize_filename(place_title_for_folder)
                place_folder_path = os.path.join(MAIN_DOWNLOAD_DIR, sanitized_folder_name)
                ensure_dir_exists(place_folder_path)
                
                image_urls_to_dl = place_info.get('image_urls', [])
                if image_urls_to_dl:
                    logger.info(f"  Downloading {len(image_urls_to_dl)} images for '{place_title_for_folder}' into '{place_folder_path}'")
                    download_tasks = []
                    for img_counter, img_url in enumerate(image_urls_to_dl):
                        task = download_image(http_session, img_url, place_folder_path, img_counter + 1)
                        download_tasks.append(task)
                    if download_tasks:
                        download_results = await asyncio.gather(*download_tasks)
                        succeeded_count = sum(1 for r in download_results if r)
                        logger.info(f"    Finished for '{place_title_for_folder}'. {succeeded_count}/{len(download_tasks)} images downloaded.")
                else:
                    logger.info(f"    No image URLs to download for '{place_title_for_folder}'.")
        logger.info(f"--- All image downloads attempted. Check the '{MAIN_DOWNLOAD_DIR}' folder. ---")
    else:
        logger.warning("\nNo data extracted, so no images to download.")

    if SAVE_DEBUG_FILES:
        logger.info(f"Debug files (if any) were saved to '{os.path.join(os.getcwd(), 'debug_output')}' directory.")

if __name__ == '__main__':
    asyncio.run(main_with_downloads())
