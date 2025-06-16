"""
Optimized Flask version of the YouTube Links Scraper
Major performance improvements:
- Concurrent processing with ThreadPoolExecutor
- Smart rate limiting with exponential backoff
- WebDriver pooling for better resource management
- Memory optimization with generators
- Caching system to avoid duplicate processing
- Circuit breaker pattern for error handling
"""

from flask import Flask, request, jsonify, render_template_string, send_file
import pandas as pd
import os
import time
import random
import re
import json
from io import BytesIO
from urllib.parse import urlparse, parse_qs, unquote
import traceback
import gc
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock, RLock
from collections import defaultdict
import hashlib
import pickle
from functools import wraps
import logging

# Selenium imports
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

app = Flask(__name__)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Configuration ---
MAX_WORKERS = 3  # Concurrent threads
INITIAL_DELAY = 2  # Start with 2 seconds
MAX_DELAY = 30  # Maximum delay between requests
CACHE_EXPIRY = 3600  # Cache for 1 hour
CIRCUIT_BREAKER_THRESHOLD = 5  # Failures before circuit opens
DRIVER_POOL_SIZE = 3

# --- Thread-safe storage ---
driver_pool = []
driver_lock = Lock()
rate_limiter_lock = RLock()
cache_lock = Lock()
circuit_breaker_lock = Lock()

# Rate limiting state
rate_limiter_state = {
    'last_request_time': 0,
    'current_delay': INITIAL_DELAY,
    'consecutive_failures': 0,
    'blocked_until': 0
}

# Circuit breaker state
circuit_breaker_state = {
    'failures': 0,
    'last_failure_time': 0,
    'is_open': False
}

# Simple in-memory cache
url_cache = {}

# --- Link Categorization ---
SOCIAL_MEDIA_KEYWORDS = {
    'Facebook': ['facebook.com'],
    'Instagram': ['instagram.com'],
    'Twitter': ['twitter.com', 'x.com'],
    'LinkedIn': ['linkedin.com'],
    'TikTok': ['tiktok.com'],
}

# --- Caching Decorator ---
def cache_result(expiry_seconds=CACHE_EXPIRY):
    """Decorator to cache function results"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Create cache key
            cache_key = hashlib.md5(str(args).encode()).hexdigest()
            
            with cache_lock:
                if cache_key in url_cache:
                    cached_result, timestamp = url_cache[cache_key]
                    if time.time() - timestamp < expiry_seconds:
                        return cached_result
                    else:
                        del url_cache[cache_key]
            
            # Execute function and cache result
            result = func(*args, **kwargs)
            
            with cache_lock:
                url_cache[cache_key] = (result, time.time())
            
            return result
        return wrapper
    return decorator

# --- Circuit Breaker ---
def circuit_breaker(func):
    """Circuit breaker pattern to handle repeated failures"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        with circuit_breaker_lock:
            if circuit_breaker_state['is_open']:
                if time.time() - circuit_breaker_state['last_failure_time'] > 300:  # 5 minutes
                    circuit_breaker_state['is_open'] = False
                    circuit_breaker_state['failures'] = 0
                    logger.info("Circuit breaker: Attempting to close")
                else:
                    return [], "Circuit breaker open - too many failures"
        
        try:
            result = func(*args, **kwargs)
            
            # Reset circuit breaker on success
            with circuit_breaker_lock:
                circuit_breaker_state['failures'] = 0
                circuit_breaker_state['is_open'] = False
            
            return result
            
        except Exception as e:
            with circuit_breaker_lock:
                circuit_breaker_state['failures'] += 1
                circuit_breaker_state['last_failure_time'] = time.time()
                
                if circuit_breaker_state['failures'] >= CIRCUIT_BREAKER_THRESHOLD:
                    circuit_breaker_state['is_open'] = True
                    logger.warning(f"Circuit breaker opened after {circuit_breaker_state['failures']} failures")
            
            raise e
    return wrapper

# --- Smart Rate Limiter ---
def smart_rate_limit():
    """Implement smart rate limiting with exponential backoff"""
    with rate_limiter_lock:
        current_time = time.time()
        
        # Check if we're in a blocked state
        if current_time < rate_limiter_state['blocked_until']:
            wait_time = rate_limiter_state['blocked_until'] - current_time
            logger.info(f"Rate limiter: Waiting {wait_time:.1f}s due to blocking")
            time.sleep(wait_time)
        
        # Calculate delay since last request
        time_since_last = current_time - rate_limiter_state['last_request_time']
        
        if time_since_last < rate_limiter_state['current_delay']:
            sleep_time = rate_limiter_state['current_delay'] - time_since_last
            logger.info(f"Rate limiter: Sleeping {sleep_time:.1f}s")
            time.sleep(sleep_time)
        
        rate_limiter_state['last_request_time'] = time.time()

def handle_rate_limit_response(is_blocked=False):
    """Handle rate limiting response and adjust delays"""
    with rate_limiter_lock:
        if is_blocked:
            rate_limiter_state['consecutive_failures'] += 1
            rate_limiter_state['current_delay'] = min(
                rate_limiter_state['current_delay'] * 2, 
                MAX_DELAY
            )
            # Set blocked until time (2-5 minutes)
            rate_limiter_state['blocked_until'] = time.time() + random.uniform(120, 300)
            logger.warning(f"Rate limited - increasing delay to {rate_limiter_state['current_delay']}s")
        else:
            # Successful request - gradually reduce delay
            rate_limiter_state['consecutive_failures'] = 0
            rate_limiter_state['current_delay'] = max(
                rate_limiter_state['current_delay'] * 0.9,
                INITIAL_DELAY
            )

# --- WebDriver Pool Management ---
def setup_selenium_driver():
    """Setup Chrome WebDriver with optimized options"""
    chrome_options = Options()
    
    # Required for Docker/containerized environments
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    
    # Aggressive memory optimization
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--disable-software-rasterizer")
    chrome_options.add_argument("--disable-plugins")
    chrome_options.add_argument("--disable-images")
    chrome_options.add_argument("--disable-javascript")
    chrome_options.add_argument("--disable-dev-tools")
    chrome_options.add_argument("--mute-audio")
    chrome_options.add_argument("--window-size=800,600")
    chrome_options.add_argument("--blink-settings=imagesEnabled=false")
    chrome_options.add_argument("--js-flags=--max-old-space-size=64")
    chrome_options.add_argument("--memory-pressure-off")
    chrome_options.add_argument("--disable-background-timer-throttling")
    chrome_options.add_argument("--disable-renderer-backgrounding")
    chrome_options.add_argument("--disable-backgrounding-occluded-windows")
    
    # Anti-detection options
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option('useAutomationExtension', False)
    
    # User agent rotation
    user_agents = [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    ]
    chrome_options.add_argument(f"--user-agent={random.choice(user_agents)}")
    
    try:
        if os.environ.get('CHROMEDRIVER_PATH'):
            service = Service(os.environ.get('CHROMEDRIVER_PATH'))
        else:
            service = Service(ChromeDriverManager().install())
            
        driver = webdriver.Chrome(service=service, options=chrome_options)
        driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        
        # Set timeouts
        driver.implicitly_wait(5)
        driver.set_page_load_timeout(30)
        
        return driver
    except Exception as e:
        logger.error(f"Failed to setup Chrome WebDriver: {str(e)}")
        return None

def get_driver():
    """Get a driver from the pool or create a new one"""
    with driver_lock:
        if driver_pool:
            return driver_pool.pop()
        else:
            return setup_selenium_driver()

def return_driver(driver):
    """Return a driver to the pool"""
    if driver is None:
        return
        
    with driver_lock:
        if len(driver_pool) < DRIVER_POOL_SIZE:
            try:
                # Check if driver is still alive
                driver.current_url
                driver_pool.append(driver)
            except:
                try:
                    driver.quit()
                except:
                    pass
        else:
            try:
                driver.quit()
            except:
                pass

def cleanup_driver_pool():
    """Clean up all drivers in the pool"""
    with driver_lock:
        while driver_pool:
            driver = driver_pool.pop()
            try:
                driver.quit()
            except:
                pass

# --- URL Processing Functions ---
def extract_clean_url(redirect_url):
    """Parse YouTube redirect URL to extract destination URL"""
    try:
        parsed_url = urlparse(redirect_url)
        query_params = parse_qs(parsed_url.query)
        if 'q' in query_params:
            return unquote(query_params['q'][0])
    except Exception as e:
        logger.debug(f"Error parsing redirect URL: {e}")
    return None

def is_valid_external_url(url):
    """Validate that URL is external and not YouTube internal"""
    if not url or not url.startswith('http'):
        return False
    
    exclude_domains = [
        'youtube.com', 'youtu.be', 'googleapis.com', 'googleusercontent.com',
        'gstatic.com', 'google.com', 'googlevideo.com'
    ]
    
    try:
        domain = urlparse(url).netloc.lower().replace('www.', '')
        return not any(excluded in domain for excluded in exclude_domains)
    except:
        return False

def find_links_in_json_enhanced(data, path=""):
    """Enhanced recursive search for links in JSON data"""
    if isinstance(data, dict):
        for key, value in data.items():
            current_path = f"{path}.{key}" if path else key
            
            if key in ['aboutChannelViewModel', 'channelMetadataRenderer', 'c4TabbedHeaderRenderer']:
                try:
                    if 'links' in value:
                        return value.get('links', [])
                    elif 'headerLinks' in value:
                        return value.get('headerLinks', [])
                    elif 'customLinks' in value:
                        return value.get('customLinks', [])
                except (KeyError, TypeError):
                    continue
            
            if key in ['contents', 'tabs', 'metadata', 'header'] and isinstance(value, (dict, list)):
                found = find_links_in_json_enhanced(value, current_path)
                if found:
                    return found
            
            if isinstance(value, (dict, list)):
                found = find_links_in_json_enhanced(value, current_path)
                if found:
                    return found
                    
    elif isinstance(data, list):
        for i, item in enumerate(data):
            if isinstance(item, (dict, list)):
                found = find_links_in_json_enhanced(item, f"{path}[{i}]")
                if found:
                    return found
    return None

def parse_links_from_json(links_data):
    """Parse links from JSON data with multiple fallback structures"""
    extracted_links = []
    
    if not isinstance(links_data, list):
        return extracted_links
    
    for link_item in links_data:
        try:
            title = None
            redirect_url = None
            
            # Multiple parsing patterns
            if 'channelExternalLinkViewModel' in link_item:
                link_info = link_item['channelExternalLinkViewModel']
                title = link_info.get('title', {}).get('content', 'No Title')
                redirect_url = (link_info.get('link', {})
                              .get('commandRuns', [{}])[0]
                              .get('onTap', {})
                              .get('innertubeCommand', {})
                              .get('urlEndpoint', {})
                              .get('url'))
            elif 'title' in link_item and 'url' in link_item:
                title = link_item['title']
                redirect_url = link_item['url']
            elif 'navigationEndpoint' in link_item:
                title = link_item.get('text', {}).get('simpleText', 'Link')
                redirect_url = (link_item['navigationEndpoint']
                              .get('urlEndpoint', {})
                              .get('url'))
            else:
                # Generic search for title and URL
                def find_text(obj):
                    if isinstance(obj, dict):
                        for key in ['content', 'simpleText', 'text', 'title']:
                            if key in obj and isinstance(obj[key], str):
                                return obj[key]
                        for value in obj.values():
                            result = find_text(value)
                            if result:
                                return result
                    return None
                
                def find_url(obj):
                    if isinstance(obj, dict):
                        for key in ['url', 'href']:
                            if key in obj and isinstance(obj[key], str):
                                return obj[key]
                        for value in obj.values():
                            result = find_url(value)
                            if result:
                                return result
                    return None
                
                title = find_text(link_item) or 'Link'
                redirect_url = find_url(link_item)
            
            if redirect_url:
                clean_url = extract_clean_url(redirect_url) if '/redirect?' in redirect_url else redirect_url
                if clean_url and is_valid_external_url(clean_url):
                    extracted_links.append({'title': title, 'url': clean_url})
                    
        except (KeyError, IndexError, TypeError):
            continue
    
    return extracted_links

def extract_links_multiple_methods(driver, channel_url):
    """Try multiple methods to extract links"""
    html_content = driver.page_source
    
    # Method 1: Enhanced ytInitialData patterns
    enhanced_patterns = [
        r'var ytInitialData = (\{.*?\});</script>',
        r'window\["ytInitialData"\] = (\{.*?\});',
        r'ytInitialData\s*=\s*(\{.*?\});',
        r'window\.ytInitialData\s*=\s*(\{.*?\});',
    ]
    
    for pattern in enhanced_patterns:
        match = re.search(pattern, html_content, re.DOTALL)
        if match:
            try:
                json_text = match.group(1)
                data = json.loads(json_text)
                links_data = find_links_in_json_enhanced(data)
                if links_data:
                    return parse_links_from_json(links_data), "Success (Enhanced JSON method)"
            except json.JSONDecodeError:
                continue
    
    # Method 2: Direct DOM element extraction
    link_selectors = [
        'a[href*="/redirect?"]',
        'a[href*="youtube.com/redirect"]',
        '[data-target-new-window="true"]',
        '.channel-external-link',
        '.ytd-channel-external-link-view-model',
        'yt-formatted-string a[href^="http"]'
    ]
    
    for selector in link_selectors:
        try:
            link_elements = driver.find_elements(By.CSS_SELECTOR, selector)
            if link_elements:
                extracted_links = []
                for element in link_elements[:10]:
                    try:
                        href = element.get_attribute('href')
                        text = (element.text.strip() or 
                               element.get_attribute('aria-label') or 
                               element.get_attribute('title') or 'Link')
                        
                        if href:
                            if '/redirect?' in href or 'youtube.com/redirect' in href:
                                clean_url = extract_clean_url(href)
                                if clean_url and is_valid_external_url(clean_url):
                                    extracted_links.append({'title': text, 'url': clean_url})
                            elif href.startswith('http') and 'youtube.com' not in href:
                                extracted_links.append({'title': text, 'url': href})
                    except Exception:
                        continue
                
                if extracted_links:
                    return extracted_links, f"Success (DOM method with {selector})"
        except Exception:
            continue
    
    return [], "No links found with any method"

@cache_result()
@circuit_breaker
def get_links_from_channel_url_optimized(channel_url):
    """Optimized version with caching and circuit breaker"""
    if not isinstance(channel_url, str) or not channel_url.startswith('http'):
        return [], f"Invalid channel URL: {channel_url}"
        
    about_url = channel_url.rstrip('/') + '/about'
    driver = None
    
    try:
        # Apply smart rate limiting
        smart_rate_limit()
        
        # Get driver from pool
        driver = get_driver()
        if not driver:
            return [], "Failed to get WebDriver"
        
        # Navigate to about page
        driver.get(about_url)
        
        # Enhanced wait strategy
        try:
            WebDriverWait(driver, 15).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )
            
            WebDriverWait(driver, 10).until(
                EC.any_of(
                    EC.presence_of_element_located((By.TAG_NAME, "ytd-channel-about-metadata-renderer")),
                    EC.presence_of_element_located((By.CSS_SELECTOR, "[data-target-new-window]")),
                    EC.presence_of_element_located((By.CSS_SELECTOR, "a[href*='/redirect?']")),
                    EC.text_to_be_present_in_element((By.TAG_NAME, "body"), "about")
                )
            )
            
            time.sleep(1)  # Brief pause for dynamic content
            
        except TimeoutException:
            return [], "Page load timeout"
        
        # Check for rate limiting
        page_source_lower = driver.page_source.lower()
        if any(phrase in page_source_lower for phrase in ["unusual traffic", "blocked", "captcha", "robot"]):
            handle_rate_limit_response(is_blocked=True)
            return [], "Rate limited by YouTube"
        
        # Extract links
        links, message = extract_links_multiple_methods(driver, channel_url)
        
        # Update rate limiter on success
        handle_rate_limit_response(is_blocked=False)
        
        return links, message
        
    except Exception as e:
        handle_rate_limit_response(is_blocked=True)
        return [], f"Error: {str(e)}"
    finally:
        # Return driver to pool
        return_driver(driver)

def categorize_links(links):
    """Categorize links into social media and other categories"""
    new_columns = ['Website'] + list(SOCIAL_MEDIA_KEYWORDS.keys()) + ['Other Links']
    categorized_links = {col: [] for col in new_columns}
    
    for link in links:
        url = link.get('url', '').lower()
        found_category = False
        
        for category, keywords in SOCIAL_MEDIA_KEYWORDS.items():
            if any(keyword in url for keyword in keywords):
                categorized_links[category].append(link['url'])
                found_category = True
                break
        
        if not found_category:
            categorized_links['Other Links'].append(link['url'])
    
    return categorized_links, new_columns

def process_single_url(url_data):
    """Process a single URL - designed for concurrent execution"""
    index, channel_url = url_data
    
    try:
        logger.info(f"Processing URL {index + 1}: {channel_url}")
        
        if pd.isna(channel_url) or not str(channel_url).strip():
            return index, None, "Empty URL"
        
        links, message = get_links_from_channel_url_optimized(str(channel_url))
        
        if links:
            categorized_links, _ = categorize_links(links)
            return index, categorized_links, message
        else:
            return index, None, message
            
    except Exception as e:
        logger.error(f"Error processing URL {index + 1}: {str(e)}")
        return index, None, str(e)

def process_dataframe_concurrent(df, url_column_name, max_rows=None):
    """Concurrent processing of DataFrame with optimizations"""
    
    if url_column_name not in df.columns:
        return None, f"Column '{url_column_name}' not found. Available columns: {', '.join(df.columns)}"
    
    # Limit rows if specified
    if max_rows and max_rows > 0 and len(df) > max_rows:
        df = df.head(max_rows)
        processing_message = f"Processing limited to first {max_rows} rows"
    else:
        processing_message = None
    
    # Initialize new columns
    new_columns = ['Website'] + list(SOCIAL_MEDIA_KEYWORDS.keys()) + ['Other Links']
    for col in new_columns:
        if col not in df.columns:
            df[col] = ''
        else:
            df[col] = df[col].astype(str)
    
    total_rows = len(df)
    processed = 0
    errors = []
    
    # Prepare data for concurrent processing
    url_data = [(index, row[url_column_name]) for index, row in df.iterrows()]
    
    # Process URLs concurrently
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_url = {executor.submit(process_single_url, url_info): url_info for url_info in url_data}
        
        for future in as_completed(future_to_url):
            try:
                index, categorized_links, message = future.result()
                processed += 1
                
                logger.info(f"Completed {processed}/{total_rows}")
                
                if categorized_links:
                    # Assign links to appropriate columns
                    for col in new_columns:
                        link_list = categorized_links.get(col, [])
                        if col == 'Other Links' and not df.at[index, 'Website']:
                            if link_list:
                                df.at[index, 'Website'] = str(link_list.pop(0))
                        
                        df.at[index, col] = str(', '.join(link_list))
                else:
                    if "No links found" not in message:
                        errors.append(f"Row {index + 1}: {message}")
                
                # Periodic garbage collection
                if processed % 10 == 0:
                    gc.collect()
                    
            except Exception as e:
                errors.append(f"Future error: {str(e)}")
                processed += 1
    
    # Cleanup
    cleanup_driver_pool()
    gc.collect()
    
    logger.info("Processing complete!")
    
    if errors:
        error_message = f"Encountered {len(errors)} errors during processing."
        if processing_message:
            error_message = f"{processing_message}. {error_message}"
    else:
        error_message = processing_message
    
    return df, error_message

def detect_url_column(df):
    """Smart detection of URL column"""
    possible_names = ['url', 'link', 'channel_url', 'youtube_url', 'channel', 'youtube_channel']
    
    # Check exact matches first
    for col in df.columns:
        if col.lower() in possible_names:
            return col
    
    # Check partial matches
    for col in df.columns:
        col_lower = col.lower()
        if any(name in col_lower for name in ['url', 'link', 'channel', 'youtube']):
            return col
    
    # Check if any column contains URLs
    for col in df.columns:
        if df[col].dtype == 'object':
            sample_values = df[col].dropna().head(5)
            if any(str(val).startswith('http') for val in sample_values):
                return col
    
    return None

# --- Flask Routes ---
@app.route('/')
def index():
    return '''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Optimized YouTube Channel Links Scraper</title>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0-alpha1/dist/css/bootstrap.min.css" rel="stylesheet">
        <style>
            body { padding: 20px; background-color: #f8f9fa; }
            .container { max-width: 900px; }
            .header { margin-bottom: 30px; }
            .feature-card { margin-bottom: 20px; }
            .performance-badge { background: linear-gradient(45deg, #28a745, #20c997); }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header text-center">
                <h1 class="display-4">🚀 Optimized YouTube Channel Links Scraper</h1>
                <p class="lead">High-performance scraper with concurrent processing and smart rate limiting</p>
            </div>
            
            <div class="row">
                <div class="col-md-8">
                    <div class="card feature-card">
                        <div class="card-body">
                            <h5 class="card-title">📁 Upload Your File</h5>
                            <form action="/upload" method="post" enctype="multipart/form-data">
                                <div class="mb-3">
                                    <label for="file" class="form-label">Select CSV or Excel file with YouTube channel URLs</label>
                                    <input type="file" class="form-control" id="file" name="file" accept=".csv,.xlsx,.xls" required>
                                </div>
                                <div class="mb-3">
                                    <label for="url_column" class="form-label">URL Column Name (optional)</label>
                                    <input type="text" class="form-control" id="url_column" name="url_column" placeholder="Leave empty for auto-detection">
                                    <small class="text-muted">Common names: url, channel_url, youtube_url, link</small>
                                </div>
                                <div class="mb-3">
                                    <label for="max_rows" class="form-label">Maximum rows to process</label>
                                    <input type="number" class="form-control" id="max_rows" name="max_rows" min="1" max="100" placeholder="e.g., 20">
                                    <small class="text-muted">Recommended: 10-30 rows for optimal performance</small>
                                </div>
                                <button type="submit" class="btn btn-primary btn-lg">
                                    <i class="bi bi-upload"></i> Upload and Process
                                </button>
                            </form>
                        </div>
                    </div>
                </div>
                
                <div class="col-md-4">
                    <div class="card feature-card">
                        <div class="card-body">
                            <h5 class="card-title">📊 Processing Status</h5>
                            <div class="alert alert-info">
                                <strong>Queue Status:</strong> Ready<br>
                                <strong>Active Workers:</strong> 0/3<br>
                                <strong>Cache Size:</strong> 0 entries
                            </div>
                        </div>
                    </div>
                </div>
            </div>
            
            <div class="card">
                <div class="card-body">
                    <h5 class="card-title">⚡ Performance Improvements</h5>
                    <div class="row">
                        <div class="col-md-6">
                            <ul class="list-unstyled">
                                <li><span class="badge bg-success">NEW</span> <strong>Concurrent Processing:</strong> Up to 3 URLs simultaneously</li>
                                <li><span class="badge bg-success">NEW</span> <strong>Smart Rate Limiting:</strong> Adaptive delays (2-30s)</li>
                                <li><span class="badge bg-success">NEW</span> <strong>WebDriver Pooling:</strong> Reuse instances for better performance</li>
                                <li><span class="badge bg-success">NEW</span> <strong>Circuit Breaker:</strong> Automatic failure recovery</li>
                                <li><span class="badge bg-success">NEW</span> <strong>Memory Optimization:</strong> Efficient resource management</li>
                            </ul>
                        </div>
                        <div class="col-md-6">
                            <ul class="list-unstyled">
                                <li><span class="badge bg-info">IMPROVED</span> <strong>Caching System:</strong> 1 hour result caching</li>
                                <li><span class="badge bg-info">IMPROVED</span> <strong>Error Handling:</strong> Better exception management</li>
                                <li><span class="badge bg-info">IMPROVED</span> <strong>JSON Parsing:</strong> Enhanced link extraction</li>
                                <li><span class="badge bg-info">IMPROVED</span> <strong>DOM Extraction:</strong> Multiple fallback methods</li>
                            </ul>
                        </div>
                    </div>
                </div>
            </div>
        </div>
        
        <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0-alpha1/dist/js/bootstrap.bundle.min.js"></script>
    </body>
    </html>
    '''

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    try:
        # Get parameters
        url_column = request.form.get('url_column', '').strip()
        max_rows = request.form.get('max_rows', '')
        max_rows = int(max_rows) if max_rows.isdigit() else None
        
        # Read file based on extension
        if file.filename.endswith('.csv'):
            df = pd.read_csv(file)
        elif file.filename.endswith(('.xlsx', '.xls')):
            df = pd.read_excel(file)
        else:
            return jsonify({'error': 'Unsupported file format. Please use CSV or Excel files.'}), 400
        
        if df.empty:
            return jsonify({'error': 'The uploaded file is empty'}), 400
        
        # Detect URL column if not specified
        if not url_column:
            url_column = detect_url_column(df)
            if not url_column:
                return jsonify({
                    'error': 'Could not detect URL column automatically. Please specify the column name.',
                    'available_columns': list(df.columns)
                }), 400
        
        logger.info(f"Processing file: {file.filename}, URL column: {url_column}, Max rows: {max_rows}")
        
        # Process the dataframe
        processed_df, error_message = process_dataframe_concurrent(df, url_column, max_rows)
        
        if processed_df is None:
            return jsonify({'error': error_message}), 400
        
        # Save to BytesIO for download
        output = BytesIO()
        processed_df.to_excel(output, index=False, engine='openpyxl')
        output.seek(0)
        
        # Create response
        response_data = {
            'success': True,
            'message': 'File processed successfully',
            'processed_rows': len(processed_df),
            'total_columns': len(processed_df.columns),
            'download_ready': True
        }
        
        if error_message:
            response_data['warnings'] = error_message
        
        # Store the file for download (in production, use proper storage)
        import tempfile
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx')
        processed_df.to_excel(temp_file.name, index=False, engine='openpyxl')
        
        # Return success response with download link
        response_data['download_url'] = f'/download/{os.path.basename(temp_file.name)}'
        
        return jsonify(response_data)
        
    except Exception as e:
        logger.error(f"Error processing file: {str(e)}")
        return jsonify({'error': f'Error processing file: {str(e)}'}), 500

@app.route('/download/<filename>')
def download_file(filename):
    """Download processed file"""
    try:
        temp_path = os.path.join(tempfile.gettempdir(), filename)
        if os.path.exists(temp_path):
            return send_file(
                temp_path,
                as_attachment=True,
                download_name=f'processed_youtube_links_{int(time.time())}.xlsx',
                mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            )
        else:
            return jsonify({'error': 'File not found'}), 404
    except Exception as e:
        return jsonify({'error': f'Download error: {str(e)}'}), 500

@app.route('/status')
def get_status():
    """Get current processing status"""
    with rate_limiter_lock:
        current_delay = rate_limiter_state['current_delay']
        consecutive_failures = rate_limiter_state['consecutive_failures']
        blocked_until = rate_limiter_state['blocked_until']
    
    with circuit_breaker_lock:
        circuit_open = circuit_breaker_state['is_open']
        circuit_failures = circuit_breaker_state['failures']
    
    with cache_lock:
        cache_size = len(url_cache)
    
    with driver_lock:
        active_drivers = len(driver_pool)
    
    return jsonify({
        'rate_limiter': {
            'current_delay': current_delay,
            'consecutive_failures': consecutive_failures,
            'is_blocked': time.time() < blocked_until,
            'blocked_until': blocked_until
        },
        'circuit_breaker': {
            'is_open': circuit_open,
            'failures': circuit_failures
        },
        'cache': {
            'size': cache_size,
            'max_age': CACHE_EXPIRY
        },
        'drivers': {
            'pool_size': active_drivers,
            'max_workers': MAX_WORKERS
        }
    })

@app.route('/clear_cache', methods=['POST'])
def clear_cache():
    """Clear the URL cache"""
    with cache_lock:
        cache_size = len(url_cache)
        url_cache.clear()
    
    return jsonify({
        'message': f'Cache cleared. Removed {cache_size} entries.',
        'success': True
    })

@app.route('/test_url', methods=['POST'])
def test_single_url():
    """Test a single YouTube channel URL"""
    data = request.get_json()
    if not data or 'url' not in data:
        return jsonify({'error': 'URL is required'}), 400
    
    channel_url = data['url']
    
    try:
        start_time = time.time()
        links, message = get_links_from_channel_url_optimized(channel_url)
        processing_time = time.time() - start_time
        
        categorized_links, _ = categorize_links(links) if links else ({}, [])
        
        return jsonify({
            'success': True,
            'url': channel_url,
            'links_found': len(links),
            'processing_time': round(processing_time, 2),
            'message': message,
            'links': links,
            'categorized': categorized_links
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'url': channel_url
        }), 500

# --- Error Handlers ---
@app.errorhandler(413)
def too_large(e):
    return jsonify({'error': 'File too large. Maximum size is 16MB.'}), 413

@app.errorhandler(500)
def internal_error(e):
    return jsonify({'error': 'Internal server error. Please try again.'}), 500

# --- Cleanup on App Shutdown ---
import atexit

def cleanup_on_exit():
    """Clean up resources on application shutdown"""
    logger.info("Cleaning up resources...")
    cleanup_driver_pool()
    
    # Clear cache
    with cache_lock:
        url_cache.clear()
    
    logger.info("Cleanup complete")

atexit.register(cleanup_on_exit)

# --- Main Application ---
if __name__ == '__main__':
    import tempfile
    
    # Ensure temp directory exists
    os.makedirs(tempfile.gettempdir(), exist_ok=True)
    
    # Configure Flask
    app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size
    
    # Log startup information
    logger.info("=" * 50)
    logger.info("🚀 Optimized YouTube Channel Links Scraper")
    logger.info("=" * 50)
    logger.info(f"Max concurrent workers: {MAX_WORKERS}")
    logger.info(f"Driver pool size: {DRIVER_POOL_SIZE}")
    logger.info(f"Rate limiting: {INITIAL_DELAY}s - {MAX_DELAY}s")
    logger.info(f"Cache expiry: {CACHE_EXPIRY}s")
    logger.info(f"Circuit breaker threshold: {CIRCUIT_BREAKER_THRESHOLD}")
    logger.info("=" * 50)
    
    # Start the Flask application
    app.run(debug=True, host='0.0.0.0', port=5000, threaded=True)
