import streamlit as st
import re
import json
import time
import random
from urllib.parse import urlparse, parse_qs, unquote
import requests
import pandas as pd
from io import BytesIO
import traceback

# --- Enhanced Anti-Detection Headers ---
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
]

def get_random_headers():
    """Get random headers for anti-detection"""
    return {
        'User-Agent': random.choice(USER_AGENTS),
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
    }

# --- Link Categorization ---
SOCIAL_MEDIA_KEYWORDS = {
    'Facebook': ['facebook.com'],
    'Instagram': ['instagram.com'],
    'Twitter': ['twitter.com', 'x.com'],
    'LinkedIn': ['linkedin.com'],
    'TikTok': ['tiktok.com'],
}

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
        st.error(f"Error parsing redirect URL: {e}")
    return None

def get_links_from_channel_url(channel_url, session, retry_count=0):
    """
    Fetches the 'About' page for a given YouTube channel URL and extracts the custom links.
    """
    if not isinstance(channel_url, str) or not channel_url.startswith('http'):
        return [], f"Invalid channel URL: {channel_url}"
        
    about_url = channel_url.rstrip('/') + '/about'
    
    try:
        # Use random headers for each request
        headers = get_random_headers()
        response = session.get(about_url, headers=headers, timeout=20)
        
        # Handle rate limiting with exponential backoff
        if response.status_code == 429:
            if retry_count < 3:
                wait_time = (2 ** retry_count) * 60  # 1, 2, 4 minutes
                st.warning(f"Rate limited. Waiting {wait_time} seconds before retry...")
                time.sleep(wait_time)
                return get_links_from_channel_url(channel_url, session, retry_count + 1)
            else:
                return [], "Rate limited - max retries exceeded"
        
        response.raise_for_status()
        html_content = response.text

        # Find the ytInitialData JSON object
        match = re.search(r'var ytInitialData = (\{.*?\});</script>', html_content)
        if not match:
            return [], "Could not find ytInitialData - channel may not have an About page"

        json_text = match.group(1)
        data = json.loads(json_text)

        # Find the links array
        links_data = find_links_in_json(data)
        
        if not links_data:
            return [], "No custom links found"

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

    except requests.exceptions.RequestException as e:
        return [], f"Request error: {str(e)}"
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

def process_dataframe(df, url_column_name, progress_bar, status_text):
    """Process the dataframe with enhanced error handling and progress tracking"""
    
    # Validate column exists
    if url_column_name not in df.columns:
        st.error(f"Column '{url_column_name}' not found. Available columns: {', '.join(df.columns)}")
        return None
    
    # Add new columns
    new_columns = ['Website'] + list(SOCIAL_MEDIA_KEYWORDS.keys()) + ['Other Links']
    for col in new_columns:
        if col not in df.columns:
            df[col] = ''
    
    # Create session for connection reuse
    session = requests.Session()
    
    total_rows = len(df)
    processed = 0
    errors = []
    
    for index, row in df.iterrows():
        try:
            status_text.text(f"Processing row {index + 1} of {total_rows}...")
            channel_url = row[url_column_name]
            
            # Skip if URL is empty or NaN
            if pd.isna(channel_url) or not str(channel_url).strip():
                errors.append(f"Row {index + 1}: Empty URL")
                processed += 1
                progress_bar.progress(processed / total_rows)
                continue
            
            links, message = get_links_from_channel_url(str(channel_url), session)
            
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
                if message != "No custom links found":
                    errors.append(f"Row {index + 1}: {message}")
            
            processed += 1
            progress_bar.progress(processed / total_rows)
            
            # Anti-detection delay
            delay = random.uniform(5, 10)  # Increased delay
            time.sleep(delay)
            
        except Exception as e:
            errors.append(f"Row {index + 1}: {str(e)}")
            processed += 1
            progress_bar.progress(processed / total_rows)
            continue
    
    session.close()
    status_text.text("Processing complete!")
    
    if errors:
        st.warning(f"Encountered {len(errors)} errors during processing:")
        for error in errors[:5]:  # Show first 5 errors
            st.text(error)
        if len(errors) > 5:
            st.text(f"... and {len(errors) - 5} more errors")
    
    return df

def main():
    st.set_page_config(
        page_title="YouTube Channel Links Scraper",
        page_icon="🔗",
        layout="wide"
    )
    
    st.title("🔗 YouTube Channel Links Scraper")
    st.markdown("Extract custom links from YouTube channel About pages")
    
    # File upload
    uploaded_file = st.file_uploader(
        "Upload your spreadsheet containing YouTube channel URLs",
        type=['csv', 'xlsx'],
        help="Upload a CSV or Excel file with YouTube channel URLs"
    )
    
    if uploaded_file is not None:
        try:
            # Read the uploaded file
            if uploaded_file.name.endswith('.csv'):
                df = pd.read_csv(uploaded_file)
            else:
                df = pd.read_excel(uploaded_file)
            
            st.success(f"✅ File uploaded successfully! Found {len(df)} rows.")
            
            # Show preview
            st.subheader("📊 Data Preview")
            st.dataframe(df.head())
            
            # Column selection
            url_column = st.selectbox(
                "Select the column containing YouTube channel URLs:",
                options=df.columns.tolist(),
                help="Choose the column that contains the YouTube channel URLs"
            )
            
            # Processing options
            st.subheader("⚙️ Processing Options")
            col1, col2 = st.columns(2)
            
            with col1:
                st.info("**Anti-Detection Features:**\n- Rotating User-Agents\n- 5-10 second delays\n- Exponential backoff on errors")
            
            with col2:
                st.info("**Output Columns Added:**\n- Website\n- Facebook, Instagram, Twitter\n- LinkedIn, TikTok\n- Other Links")
            
            # Process button
            if st.button("🚀 Start Processing", type="primary"):
                if not url_column:
                    st.error("Please select a column containing URLs")
                    return
                
                # Create progress indicators
                progress_bar = st.progress(0)
                status_text = st.empty()
                
                # Process the dataframe
                with st.spinner("Processing channels... This may take several minutes."):
                    processed_df = process_dataframe(df, url_column, progress_bar, status_text)
                
                if processed_df is not None:
                    st.success("🎉 Processing completed!")
                    
                    # Show results
                    st.subheader("📈 Results")
                    st.dataframe(processed_df)
                    
                    # Download buttons
                    col1, col2 = st.columns(2)
                    
                    with col1:
                        # CSV download
                        csv = processed_df.to_csv(index=False)
                        st.download_button(
                            label="📥 Download as CSV",
                            data=csv,
                            file_name=f"youtube_links_{int(time.time())}.csv",
                            mime="text/csv"
                        )
                    
                    with col2:
                        # Excel download
                        buffer = BytesIO()
                        processed_df.to_excel(buffer, index=False)
                        st.download_button(
                            label="📥 Download as Excel",
                            data=buffer.getvalue(),
                            file_name=f"youtube_links_{int(time.time())}.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                        )
        
        except Exception as e:
            st.error(f"Error reading file: {str(e)}")
            st.text(traceback.format_exc())
    
    else:
        st.info("👆 Please upload a CSV or Excel file to get started")
        
        # Instructions
        st.subheader("📝 Instructions")
        st.markdown("""
        1. **Prepare your spreadsheet** with YouTube channel URLs
        2. **Upload the file** using the file uploader above
        3. **Select the column** containing the YouTube URLs
        4. **Click 'Start Processing'** and wait for completion
        5. **Download your results** with extracted links
        
        **Note:** Processing includes anti-detection measures with delays between requests.
        Large files may take considerable time to process.
        """)

if __name__ == "__main__":
    main()
