"""
Flask version of the YouTube Links Scraper
This version can be deployed to platforms that support Selenium
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
import gc  # Garbage collection

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

# --- Link Categorization ---
SOCIAL_MEDIA_KEYWORDS = {
    'Facebook': ['facebook.com'],
    'Instagram': ['instagram.com'],
    'Twitter': ['twitter.com', 'x.com'],
    'LinkedIn': ['linkedin.com'],
    'TikTok': ['tiktok.com'],
}

def setup_selenium_driver():
    """Setup Chrome WebDriver with anti-detection options for containerized environment"""
    chrome_options = Options()
    
    # Required for Docker/containerized environments
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    
    # Memory optimization options
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--disable-software-rasterizer")
    chrome_options.add_argument("--disable-plugins")
    chrome_options.add_argument("--disable-images")
    chrome_options.add_argument("--disable-javascript")  # If possible for your scraping needs
    chrome_options.add_argument("--disable-dev-tools")
    chrome_options.add_argument("--mute-audio")
    chrome_options.add_argument("--window-size=800,600")  # Smaller window size
    chrome_options.add_argument("--blink-settings=imagesEnabled=false")
    
    # Limit memory usage
    chrome_options.add_argument("--js-flags=--max-old-space-size=128")  # Limit JS memory
    
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
        # For Docker deployment
        if os.environ.get('CHROMEDRIVER_PATH'):
            service = Service(os.environ.get('CHROMEDRIVER_PATH'))
        else:
            # Use webdriver-manager as fallback
            service = Service(ChromeDriverManager().install())
            
        driver = webdriver.Chrome(service=service, options=chrome_options)
        
        # Execute script to remove webdriver property
        driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        
        return driver
    except Exception as e:
        print(f"Failed to setup Chrome WebDriver: {str(e)}")
        return None

def extract_clean_url(redirect_url):
    """
    Parses a YouTube redirect URL to extract and decode the actual destination URL
    """
    try:
        parsed_url = urlparse(redirect_url)
        query_params = parse_qs(parsed_url.query)
        if 'q' in query_params:
            return unquote(query_params['q'][0])
    except Exception as e:
        print(f"Error parsing redirect URL: {e}")
    return None

def is_valid_external_url(url):
    """
    Validate that the URL is external and not a YouTube internal link
    """
    if not url or not url.startswith('http'):
        return False
    
    # Exclude YouTube internal URLs
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
    """
    Enhanced recursive search with multiple fallback patterns for different channel types
    """
    if isinstance(data, dict):
        for key, value in data.items():
            current_path = f"{path}.{key}" if path else key
            
            # Multiple patterns for different channel types
            if key in ['aboutChannelViewModel', 'channelMetadataRenderer', 'c4TabbedHeaderRenderer']:
                try:
                    # Try different link storage patterns
                    if 'links' in value:
                        return value.get('links', [])
                    elif 'headerLinks' in value:
                        return value.get('headerLinks', [])
                    elif 'customLinks' in value:
                        return value.get('customLinks', [])
                except (KeyError, TypeError):
                    continue
            
            # Look for nested structures that might contain links
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
    """
    Enhanced JSON link parsing with multiple fallback structures
    """
    extracted_links = []
    
    if not isinstance(links_data, list):
        return extracted_links
    
    for link_item in links_data:
        try:
            # Pattern 1: channelExternalLinkViewModel (current)
            if 'channelExternalLinkViewModel' in link_item:
                link_info = link_item['channelExternalLinkViewModel']
                title = link_info.get('title', {}).get('content', 'No Title')
                redirect_url = (link_info.get('link', {})
                              .get('commandRuns', [{}])[0]
                              .get('onTap', {})
                              .get('innertubeCommand', {})
                              .get('urlEndpoint', {})
                              .get('url'))
            
            # Pattern 2: Direct link structure
            elif 'title' in link_item and 'url' in link_item:
                title = link_item['title']
                redirect_url = link_item['url']
            
            # Pattern 3: navigationEndpoint structure
            elif 'navigationEndpoint' in link_item:
                title = link_item.get('text', {}).get('simpleText', 'Link')
                redirect_url = (link_item['navigationEndpoint']
                              .get('urlEndpoint', {})
                              .get('url'))
            
            # Pattern 4: Alternative nested structures
            else:
                # Try to find title and URL in any nested structure
                title = None
                redirect_url = None
                
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
                    
        except (KeyError, IndexError, TypeError) as e:
            continue
    
    return extracted_links

def extract_links_multiple_methods(driver, channel_url):
    """
    Try multiple methods to extract links with comprehensive fallbacks
    """
    html_content = driver.page_source
    
    # Method 1: Enhanced ytInitialData patterns
    enhanced_patterns = [
        r'var ytInitialData = (\{.*?\});</script>',
        r'window\["ytInitialData"\] = (\{.*?\});',
        r'ytInitialData\s*=\s*(\{.*?\});',
        r'ytInitialData\[""\]\s*=\s*(\{.*?\});',
        r'window\.ytInitialData\s*=\s*(\{.*?\});',
        r'ytInitialData:\s*(\{.*?\}),',
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
    
    # Method 2: Alternative JSON variable names
    alt_patterns = [
        r'window\[\"ytcfg\"\]\.d\(\)\.CLIENT_NAME = \"WEB\".*?ytInitialData["\']?\s*:\s*(\{.*?\})',
        r'ytcfg\.set\s*\(\s*\{\s*[\'"]EXPERIMENT_FLAGS[\'"].*?ytInitialData[\'"]?\s*:\s*(\{.*?\})',
    ]
    
    for pattern in alt_patterns:
        match = re.search(pattern, html_content, re.DOTALL)
        if match:
            try:
                json_text = match.group(1)
                data = json.loads(json_text)
                links_data = find_links_in_json_enhanced(data)
                if links_data:
                    return parse_links_from_json(links_data), "Success (Alternative JSON method)"
            except json.JSONDecodeError:
                continue
    
    # Method 3: Direct DOM element extraction with multiple selectors
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
                    except Exception as e:
                        continue
                
                if extracted_links:
                    return extracted_links, f"Success (DOM method with {selector})"
        except Exception as e:
            continue
    
    # Method 4: Text-based regex extraction as last resort
    try:
        url_patterns = [
            r'https?://(?:www\.)?(?:facebook|instagram|twitter|linkedin|tiktok)\.com/[\w\-\.]+',
            r'https?://(?:www\.)?[\w\-]+\.(?:com|org|net|co|io)/[\w\-\.]*',
        ]
        
        extracted_links = []
        for pattern in url_patterns:
            matches = re.findall(pattern, html_content, re.IGNORECASE)
            for url in matches[:5]:  # Limit to avoid false positives
                if is_valid_external_url(url):
                    extracted_links.append({'title': 'Extracted Link', 'url': url})
        
        if extracted_links:
            return extracted_links, "Success (Regex extraction method)"
            
    except Exception as e:
        pass
    
    return [], "No links found with any method"

def get_links_from_channel_url_selenium_enhanced(channel_url, driver, retry_count=0):
    """
    Enhanced version with better error handling and multiple extraction methods
    """
    if not isinstance(channel_url, str) or not channel_url.startswith('http'):
        return [], f"Invalid channel URL: {channel_url}"
        
    about_url = channel_url.rstrip('/') + '/about'
    
    try:
        # Navigate to the about page
        driver.get(about_url)
        
        # Enhanced wait strategy
        try:
            # Wait for page to load completely
            WebDriverWait(driver, 15).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )
            
            # Wait for YouTube-specific elements
            WebDriverWait(driver, 10).until(
                EC.any_of(
                    EC.presence_of_element_located((By.TAG_NAME, "ytd-channel-about-metadata-renderer")),
                    EC.presence_of_element_located((By.CSS_SELECTOR, "[data-target-new-window]")),
                    EC.presence_of_element_located((By.CSS_SELECTOR, "a[href*='/redirect?']")),
                    EC.text_to_be_present_in_element((By.TAG_NAME, "body"), "about")
                )
            )
            
            # Additional wait for dynamic content
            time.sleep(2)
            
        except TimeoutException:
            return [], "Page load timeout - about page may not be available"
        
        # Check for rate limiting
        page_source_lower = driver.page_source.lower()
        if any(phrase in page_source_lower for phrase in ["unusual traffic", "blocked", "captcha", "robot"]):
            if retry_count < 1:
                wait_time = 120  # 2 minutes
                print(f"Detected blocking. Waiting {wait_time} seconds...")
                time.sleep(wait_time)
                return get_links_from_channel_url_selenium_enhanced(channel_url, driver, retry_count + 1)
            else:
                return [], "Blocked due to anti-bot measures - max retries exceeded"
        
        # Try multiple extraction methods
        links, message = extract_links_multiple_methods(driver, channel_url)
        
        if links:
            return links, message
        else:
            return [], f"No links found - {message}"
            
    except WebDriverException as e:
        return [], f"WebDriver error: {str(e)}"
    except Exception as e:
        return [], f"Unexpected error: {str(e)}"

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

def process_dataframe_selenium(df, url_column_name, max_rows=None):
    """Process the dataframe using Selenium with enhanced error handling and memory optimization"""
    
    # Validate column exists
    if url_column_name not in df.columns:
        return None, f"Column '{url_column_name}' not found. Available columns: {', '.join(df.columns)}"
    
    # Limit number of rows to process if specified
    if max_rows and max_rows > 0 and len(df) > max_rows:
        df = df.head(max_rows)
        processing_message = f"Processing limited to first {max_rows} rows to conserve memory"
    else:
        processing_message = None
    
    # Add new columns
    new_columns = ['Website'] + list(SOCIAL_MEDIA_KEYWORDS.keys()) + ['Other Links']
    for col in new_columns:
        if col not in df.columns:
            df[col] = ''
    
    # Setup Selenium driver
    driver = setup_selenium_driver()
    if not driver:
        return None, "Failed to initialize Chrome WebDriver. Please ensure Chrome is installed."
    
    total_rows = len(df)
    processed = 0
    errors = []
    
    try:
        for index, row in df.iterrows():
            try:
                print(f"Processing row {index + 1} of {total_rows}...")
                channel_url = row[url_column_name]
                
                # Skip if URL is empty or NaN
                if pd.isna(channel_url) or not str(channel_url).strip():
                    errors.append(f"Row {index + 1}: Empty URL")
                    processed += 1
                    continue
                
                # Use the enhanced link extraction method
                links, message = get_links_from_channel_url_selenium_enhanced(str(channel_url), driver)
                
                if links:
                    categorized_links, _ = categorize_links(links)
                    
                    # Assign links to appropriate columns
                    for col in new_columns:
                        link_list = categorized_links.get(col, [])
                        if col == 'Other Links' and not df.at[index, 'Website']:
                            if link_list:
                                df.at[index, 'Website'] = link_list.pop(0)
                        
                        df.at[index, col] = ', '.join(link_list)
                else:
                    if message != "No links found - No links found with any method":
                        errors.append(f"Row {index + 1}: {message}")
                
                processed += 1
                
                # Anti-detection delay - INCREASED to 30-60 seconds as requested
                delay = random.uniform(30, 60)  # Increased to 30-60 seconds
                print(f"Waiting {delay:.1f} seconds before next request...")
                time.sleep(delay)
                
                # Force garbage collection every few rows
                if processed % 5 == 0:
                    gc.collect()
                
            except Exception as e:
                errors.append(f"Row {index + 1}: {str(e)}")
                processed += 1
                continue
    
    finally:
        # Always close the driver
        try:
            driver.quit()
        except:
            pass
        
        # Force garbage collection
        gc.collect()
    
    print("Processing complete!")
    
    if errors:
        error_message = f"Encountered {len(errors)} errors during processing."
        if processing_message:
            error_message = f"{processing_message}. {error_message}"
    else:
        error_message = processing_message
    
    return df, error_message

# HTML templates
@app.route('/')
def index():
    return '''
    <!DOCTYPE html>
    <html>
    <head>
        <title>YouTube Channel Links Scraper</title>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0-alpha1/dist/css/bootstrap.min.css" rel="stylesheet">
        <style>
            body { padding: 20px; }
            .container { max-width: 800px; }
            .header { margin-bottom: 30px; }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header text-center">
                <h1>🔗 YouTube Channel Links Scraper</h1>
                <p class="lead">Extract custom links from YouTube channel About pages</p>
            </div>
            
            <div class="card mb-4">
                <div class="card-body">
                    <h5 class="card-title">Upload your file</h5>
                    <form action="/upload" method="post" enctype="multipart/form-data">
                        <div class="mb-3">
                            <label for="file" class="form-label">Select CSV or Excel file with YouTube channel URLs</label>
                            <input type="file" class="form-control" id="file" name="file" accept=".csv,.xlsx">
                        </div>
                        <div class="mb-3">
                            <label for="max_rows" class="form-label">Maximum rows to process (leave empty for all rows)</label>
                            <input type="number" class="form-control" id="max_rows" name="max_rows" min="1" placeholder="e.g., 5">
                            <small class="text-muted">Limiting rows helps prevent memory issues on free hosting.</small>
                        </div>
                        <button type="submit" class="btn btn-primary">Upload and Process</button>
                    </form>
                </div>
            </div>
            
            <div class="card">
                <div class="card-body">
                    <h5 class="card-title">Instructions</h5>
                    <ol>
                        <li>Prepare a spreadsheet with YouTube channel URLs</li>
                        <li>Upload the file using the form above</li>
                        <li>Select the column containing the YouTube URLs</li>
                        <li>Wait for processing to complete</li>
                        <li>Download your results with extracted links</li>
                    </ol>
                    <div class="alert alert-info">
                        <strong>Note:</strong> This app runs on a free hosting plan with the following limitations:
                        <ul>
                            <li>May take 50+ seconds to start after inactivity</li>
                            <li>Has limited memory (processing large files may fail)</li>
                            <li>Works best with small batches (3-5 URLs at a time)</li>
                            <li>Uses 30-60 second delays between requests to avoid rate limiting</li>
                            <li>Processing may take several minutes due to these safety delays</li>
                        </ul>
                    </div>
                </div>
            </div>
        </div>
    </body>
    </html>
    '''

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
    
    # Get max_rows parameter
    max_rows = request.form.get('max_rows', '')
    max_rows = int(max_rows) if max_rows and max_rows.isdigit() else None
    
    if file and (file.filename.endswith('.csv') or file.filename.endswith('.xlsx')):
        try:
            # Read the file
            if file.filename.endswith('.csv'):
                df = pd.read_csv(file)
            else:
                df = pd.read_excel(file)
            
            # Store the dataframe in a session or temporary file
            temp_file = f"temp_{int(time.time())}.csv"
            df.to_csv(temp_file, index=False)
            
            # Return column selection page
            columns = df.columns.tolist()
            return render_template_string('''
                <!DOCTYPE html>
                <html>
                <head>
                    <title>Select Column - YouTube Links Scraper</title>
                    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0-alpha1/dist/css/bootstrap.min.css" rel="stylesheet">
                    <style>
                        body { padding: 20px; }
                        .container { max-width: 800px; }
                    </style>
                </head>
                <body>
                    <div class="container">
                        <h1>Select URL Column</h1>
                        <p>Your file has been uploaded. Please select the column containing YouTube channel URLs:</p>
                        
                        <div class="card mb-4">
                            <div class="card-body">
                                <h5 class="card-title">Data Preview</h5>
                                <div class="table-responsive">
                                    {{ table_html|safe }}
                                </div>
                            </div>
                        </div>
                        
                        <form action="/process" method="post">
                            <input type="hidden" name="temp_file" value="{{ temp_file }}">
                            <input type="hidden" name="max_rows" value="{{ max_rows }}">
                            <div class="mb-3">
                                <label for="column" class="form-label">Select URL Column:</label>
                                <select class="form-select" id="column" name="column">
                                    {% for column in columns %}
                                    <option value="{{ column }}">{{ column }}</option>
                                    {% endfor %}
                                </select>
                            </div>
                            <div class="alert alert-warning">
                                <strong>Processing Time Notice:</strong> Due to 30-60 second safety delays between requests, 
                                processing will take several minutes even for a small number of URLs. This helps avoid 
                                YouTube's rate limiting and blocking.
                            </div>
                            <button type="submit" class="btn btn-primary">Process Data</button>
                        </form>
                    </div>
                </body>
                </html>
            ''', columns=columns, temp_file=temp_file, max_rows=max_rows or '', table_html=df.head().to_html(classes='table table-striped'))
        
        except Exception as e:
            return jsonify({'error': f'Error processing file: {str(e)}'}), 500
    
    return jsonify({'error': 'Invalid file type. Please upload a CSV or Excel file.'}), 400

@app.route('/process', methods=['POST'])
def process_data():
    temp_file = request.form.get('temp_file')
    column = request.form.get('column')
    max_rows = request.form.get('max_rows', '')
    max_rows = int(max_rows) if max_rows and max_rows.isdigit() else None
    
    if not temp_file or not column:
        return jsonify({'error': 'Missing required parameters'}), 400
    
    try:
        # Read the temporary file
        df = pd.read_csv(temp_file)
        
        # Process the dataframe
        processed_df, error_message = process_dataframe_selenium(df, column, max_rows)
        
        if processed_df is None:
            return jsonify({'error': error_message}), 400
        
        # Save the processed dataframe
        result_file = f"result_{int(time.time())}.csv"
        processed_df.to_csv(result_file, index=False)
        
        # Clean up temporary file
        try:
            os.remove(temp_file)
        except:
            pass
        
        # Force garbage collection
        gc.collect()
        
        # Return results page
        return render_template_string('''
            <!DOCTYPE html>
            <html>
            <head>
                <title>Results - YouTube Links Scraper</title>
                <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0-alpha1/dist/css/bootstrap.min.css" rel="stylesheet">
                <style>
                    body { padding: 20px; }
                    .container { max-width: 1000px; }
                </style>
            </head>
            <body>
                <div class="container">
                    <h1>Processing Complete!</h1>
                    {% if error_message %}
                    <div class="alert alert-warning">
                        {{ error_message }}
                    </div>
                    {% endif %}
                    
                    <div class="card mb-4">
                        <div class="card-body">
                            <h5 class="card-title">Results Preview</h5>
                            <div class="table-responsive">
                                {{ table_html|safe }}
                            </div>
                        </div>
                    </div>
                    
                    <div class="d-flex gap-2">
                        <a href="/download/{{ result_file }}/csv" class="btn btn-primary">Download as CSV</a>
                        <a href="/download/{{ result_file }}/excel" class="btn btn-success">Download as Excel</a>
                    </div>
                </div>
            </body>
            </html>
        ''', result_file=result_file, error_message=error_message, table_html=processed_df.head(10).to_html(classes='table table-striped'))
    
    except Exception as e:
        return jsonify({'error': f'Error processing data: {str(e)}'}), 500

@app.route('/download/<filename>/<format>')
def download_file(filename, format):
    try:
        df = pd.read_csv(filename)
        
        if format == 'excel':
            output = BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                df.to_excel(writer, index=False)
            output.seek(0)
            
            # Clean up CSV file after generating Excel
            try:
                os.remove(filename)
            except:
                pass
                
            return send_file(
                output,
                as_attachment=True,
                download_name=f"youtube_links_{int(time.time())}.xlsx",
                mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
        else:
            response = send_file(
                filename,
                as_attachment=True,
                download_name=f"youtube_links_{int(time.time())}.csv",
                mimetype="text/csv"
            )
            
            # Schedule file for deletion after sending
            @response.call_on_close
            def cleanup():
                try:
                    os.remove(filename)
                except:
                    pass
                    
            return response
    except Exception as e:
        return jsonify({'error': f'Error downloading file: {str(e)}'}), 500

if __name__ == '__main__':
    # Use the PORT environment variable for compatibility with cloud platforms
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
