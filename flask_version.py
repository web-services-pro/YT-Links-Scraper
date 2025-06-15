"""
Flask version of the YouTube Links Scraper
This version can be deployed to platforms that support Selenium but may not work well with Streamlit
"""

from flask import Flask, request, jsonify, render_template, send_file
import pandas as pd
import os
import time
import random
import re
import json
from io import BytesIO
from urllib.parse import urlparse, parse_qs, unquote
import traceback

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
    
    # Anti-detection options
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option('useAutomationExtension', False)
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--disable-plugins")
    chrome_options.add_argument("--disable-images")  # Speed up loading
    
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

def find_links_in_json(data):
    """
    Recursively searches through a nested dictionary/list structure to find the 'links' array.
    """
    if isinstance(data, dict):
        for key, value in data.items():
            if key == 'aboutChannelViewModel':
                try:
                    return value.get('links', [])
                except KeyError:
                    return []
            
            if isinstance(value, (dict, list)):
                found = find_links_in_json(value)
                if found is not None:
                    return found
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, (dict, list)):
                found = find_links_in_json(item)
                if found is not None:
                    return found
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

def get_links_from_channel_url_selenium(channel_url, driver, retry_count=0):
    """
    Uses Selenium to fetch the 'About' page for a given YouTube channel URL and extract custom links.
    """
    if not isinstance(channel_url, str) or not channel_url.startswith('http'):
        return [], f"Invalid channel URL: {channel_url}"
        
    about_url = channel_url.rstrip('/') + '/about'
    
    try:
        # Navigate to the about page
        driver.get(about_url)
        
        # Wait for page to load and check for common YouTube elements
        try:
            # Wait for either the page content or an error message
            WebDriverWait(driver, 15).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )
            
            # Additional wait for dynamic content
            time.sleep(3)
            
        except TimeoutException:
            return [], "Page load timeout"
        
        # Check if we're being rate limited or blocked
        if "unusual traffic" in driver.page_source.lower():
            if retry_count < 2:
                wait_time = (retry_count + 1) * 120  # 2, 4 minutes
                print(f"Detected unusual traffic message. Waiting {wait_time} seconds...")
                time.sleep(wait_time)
                return get_links_from_channel_url_selenium(channel_url, driver, retry_count + 1)
            else:
                return [], "Blocked due to unusual traffic - max retries exceeded"
        
        # Get the page source after JavaScript execution
        html_content = driver.page_source

        # Find the ytInitialData JSON object
        patterns = [
            r'var ytInitialData = (\{.*?\});</script>',
            r'window\["ytInitialData"\] = (\{.*?\});',
            r'ytInitialData[""] = (\{.*?\});',
            r'ytInitialData = (\{.*?\});'
        ]
        
        data = None
        for pattern in patterns:
            match = re.search(pattern, html_content, re.DOTALL)
            if match:
                try:
                    json_text = match.group(1)
                    data = json.loads(json_text)
                    break
                except json.JSONDecodeError:
                    continue
        
        if not data:
            # Try alternative approach - look for links in the rendered page
            try:
                # Look for external link elements in the about section
                link_elements = driver.find_elements(By.CSS_SELECTOR, 'a[href*="/redirect?"]')
                if link_elements:
                    extracted_links = []
                    for element in link_elements[:10]:  # Limit to first 10 links
                        try:
                            href = element.get_attribute('href')
                            text = element.text.strip() or element.get_attribute('aria-label') or 'Link'
                            if href and '/redirect?' in href:
                                clean_url = extract_clean_url(href)
                                if clean_url:
                                    extracted_links.append({'title': text, 'url': clean_url})
                        except:
                            continue
                    
                    if extracted_links:
                        return extracted_links, "Success (alternative method)"
            except:
                pass
            
            return [], "Could not find ytInitialData or alternative link elements"

        # Find the links array in the JSON data
        links_data = find_links_in_json(data)
        
        if not links_data:
            return [], "No custom links found in JSON data"

        extracted_links = []
        for link_item in links_data:
            try:
                link_info = link_item.get('channelExternalLinkViewModel', {})
                title = link_info.get('title', {}).get('content', 'No Title')
                redirect_url = link_info.get('link', {}).get('commandRuns', [{}])[0].get('onTap', {}).get('innertubeCommand', {}).get('urlEndpoint', {}).get('url')

                if redirect_url:
                    clean_url = extract_clean_url(redirect_url)
                    if clean_url:
                        extracted_links.append({'title': title, 'url': clean_url})

            except (KeyError, IndexError) as e:
                continue
        
        return extracted_links, "Success"

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

def process_dataframe_selenium(df, url_column_name):
    """Process the dataframe using Selenium with enhanced error handling"""
    
    # Validate column exists
    if url_column_name not in df.columns:
        return None, f"Column '{url_column_name}' not found. Available columns: {', '.join(df.columns)}"
    
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
                
                links, message = get_links_from_channel_url_selenium(str(channel_url), driver)
                
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
                    if message != "No custom links found in JSON data":
                        errors.append(f"Row {index + 1}: {message}")
                
                processed += 1
                
                # Anti-detection delay
                delay = random.uniform(8, 15)  # Longer delays for Selenium
                time.sleep(delay)
                
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
    
    print("Processing complete!")
    
    if errors:
        error_message = f"Encountered {len(errors)} errors during processing."
    else:
        error_message = None
    
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
                    <p><strong>Note:</strong> Processing may take some time depending on the number of URLs.</p>
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
                            <div class="mb-3">
                                <label for="column" class="form-label">Select URL Column:</label>
                                <select class="form-select" id="column" name="column">
                                    {% for column in columns %}
                                    <option value="{{ column }}">{{ column }}</option>
                                    {% endfor %}
                                </select>
                            </div>
                            <button type="submit" class="btn btn-primary">Process Data</button>
                        </form>
                    </div>
                </body>
                </html>
            ''', columns=columns, temp_file=temp_file, table_html=df.head().to_html(classes='table table-striped'))
        
        except Exception as e:
            return jsonify({'error': f'Error processing file: {str(e)}'}), 500
    
    return jsonify({'error': 'Invalid file type. Please upload a CSV or Excel file.'}), 400

@app.route('/process', methods=['POST'])
def process_data():
    temp_file = request.form.get('temp_file')
    column = request.form.get('column')
    
    if not temp_file or not column:
        return jsonify({'error': 'Missing required parameters'}), 400
    
    try:
        # Read the temporary file
        df = pd.read_csv(temp_file)
        
        # Process the dataframe
        processed_df, error_message = process_dataframe_selenium(df, column)
        
        if processed_df is None:
            return jsonify({'error': error_message}), 400
        
        # Save the processed dataframe
        result_file = f"result_{int(time.time())}.csv"
        processed_df.to_csv(result_file, index=False)
        
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
            return send_file(
                output,
                as_attachment=True,
                download_name=f"youtube_links_{int(time.time())}.xlsx",
                mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
        else:
            return send_file(
                filename,
                as_attachment=True,
                download_name=f"youtube_links_{int(time.time())}.csv",
                mimetype="text/csv"
            )
    except Exception as e:
        return jsonify({'error': f'Error downloading file: {str(e)}'}), 500

if __name__ == '__main__':
    # Use the PORT environment variable for compatibility with cloud platforms
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
