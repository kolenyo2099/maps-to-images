# Google Maps Image Scraper

A Python script that extracts and downloads images from Google Maps for given places/locations.

## Description

This script allows you to:
1. Search for places on Google Maps based on your query
2. Extract place information (title, address)
3. Find and download all images associated with those places
4. Save the data in organized folders

## Requirements

- Python 3.7+
- Playwright
- aiohttp

## Installation

1. Clone this repository:
   ```
   git clone [<repository-url>](https://github.com/kolenyo2099/maps-to-images)
   cd <repository-directory>
   ```

2. Install required packages:
   ```
   pip install playwright aiohttp
   ```

3. Install Playwright browsers:
   ```
   playwright install chromium
   ```

## Usage

1. Run the script:
   ```
   python appv2.py
   ```

2. Enter your search query when prompted (e.g., "restaurants in Berkeley", "coffee shops in New York")

3. The script will:
   - Search Google Maps for your query
   - Process up to 3 places by default (configurable)
   - Extract place details and images
   - Download all images to the "downloaded_google_maps_images" folder
   - Save extracted data to "google_maps_extracted_data.json"

## Configuration

You can modify these variables at the top of the script:

```python
SAVE_DEBUG_FILES = True           # Set to False to disable debug file generation
DEBUG_FILE_PREFIX = "gm_debug"    # Prefix for debug files
MAIN_DOWNLOAD_DIR = "downloaded_google_maps_images"  # Main folder for downloads
MAX_PLACES_TO_PROCESS = 3         # Maximum number of places to scrape
```

## Output Structure

- `downloaded_google_maps_images/`: Main folder containing all downloaded images
  - `[Place Name 1]/`: Folder for each place (sanitized name)
    - `image_001.jpg`, `image_002.jpg`, etc.
  - `[Place Name 2]/`
    - ...

- `google_maps_extracted_data.json`: JSON file containing all extracted data
- `debug_output/`: Folder containing debug files (if enabled)

## Notes

- This script uses web scraping techniques and might break if Google Maps changes its structure
- Use responsibly and in accordance with Google's Terms of Service
- Set `SAVE_DEBUG_FILES = False` in production to reduce disk usage

## License

[Add your license information here] 
