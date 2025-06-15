import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
import re
import time
import random
from urllib.parse import urljoin, urlparse
import io
import logging
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Configure Streamlit page
st.set_page_config(
    page_title="YouTube Channel Data Enhancer - Streamlit Cloud Compatible",
    page_icon="🎬",
    layout="wide"
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def get_random_user_agent():
    """Return a random realistic user agent"""
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15"
    ]
    return random.choice(user_agents)

def create_session():
    """Create a requests session with retry strategy and stealth headers"""
    session = requests.Session()
    
    # Retry strategy
    retry_strategy = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    
    # Default headers to mimic real browser
    session.headers.update({
        'User-Agent': get_random_user_agent(),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'DNT': '1',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Sec-Fetch-User': '?1',
        'Cache-Control': 'max-age=0'
    })
    
    return session

def extract_social_links_safer(soup, page_url):
    """Extract social media links with better parsing"""
    links = {
        'Website': None,
        'Instagram': None,
        'Twitter': None,
        'TikTok': None,
        'Facebook': None,
        'LinkedIn': None,
        'Other_Links': []
    }
    
    try:
        # Multiple strategies for finding links
        
        # Strategy 1: Look for link elements
        link_elements = soup.find_all(['a'], href=True)
        
        # Strategy 2: Search in text content for URLs
        text_content = soup.get_text()
        
        # Strategy 3: Look in specific YouTube channel page elements
        # YouTube often puts links in specific divs or spans
        potential_link_containers = soup.find_all(['div', 'span', 'p'], class_=re.compile(r'(link|social|contact|about)', re.I))
        
        # More comprehensive URL patterns
        url_patterns = [
            r'https?://(?:www\.)?instagram\.com/[^\s<>"\')\]]+',
            r'https?://(?:www\.)?(?:twitter|x)\.com/[^\s<>"\')\]]+',
            r'https?://(?:www\.)?tiktok\.com/[^\s<>"\')\]]+',
            r'https?://(?:www\.)?facebook\.com/[^\s<>"\')\]]+',
            r'https?://(?:www\.)?linkedin\.com/[^\s<>"\')\]]+',
            r'https?://[^\s<>"\')\]]+\.[a-zA-Z]{2,}[^\s<>"\')\]]*'
        ]
        
        all_urls = set()
        
        # Extract from href attributes
        for element in link_elements:
            href = element.get('href', '')
            if href and href.startswith('http'):
                all_urls.add(href)
            elif href and href.startswith('/'):
                # Handle relative URLs
                try:
                    full_url = urljoin(page_url, href)
                    if full_url.startswith('http'):
                        all_urls.add(full_url)
                except:
                    continue
        
        # Extract from text using patterns
        all_text = text_content + ' '.join([elem.get_text() for elem in potential_link_containers])
        for pattern in url_patterns:
            matches = re.findall(pattern, all_text, re.IGNORECASE)
            all_urls.update(matches)
        
        # Look for URLs in data attributes and other attributes
        for element in soup.find_all(attrs={'data-href': True}):
            data_href = element.get('data-href', '')
            if data_href and data_href.startswith('http'):
                all_urls.add(data_href)
        
        # Clean and categorize URLs
        for url in all_urls:
            try:
                # Clean URL
                url = url.strip().rstrip('.,;)')
                if not url.startswith('http'):
                    continue
                    
                domain = urlparse(url).netloc.lower()
                
                # Skip YouTube URLs and common false positives
                if any(skip in domain for skip in [
                    'youtube.com', 'youtu.be', 'google.com', 'googleusercontent.com',
                    'ggpht.com', 'ytimg.com'
                ]):
                    continue
                
                # Categorize by platform
                if 'instagram.com' in domain and not links['Instagram']:
                    links['Instagram'] = url
                elif ('twitter.com' in domain or 'x.com' in domain) and not links['Twitter']:
                    links['Twitter'] = url
                elif 'tiktok.com' in domain and not links['TikTok']:
                    links['TikTok'] = url
                elif ('facebook.com' in domain or 'fb.com' in domain) and not links['Facebook']:
                    links['Facebook'] = url
                elif 'linkedin.com' in domain and not links['LinkedIn']:
                    links['LinkedIn'] = url
                elif '.' in domain and len(domain) > 4:
                    # Potential website
                    if not links['Website'] and not any(platform in domain for platform in [
                        'instagram.com', 'twitter.com', 'x.com', 'tiktok.com', 
                        'facebook.com', 'fb.com', 'linkedin.com'
                    ]):
                        links['Website'] = url
                    else:
                        links['Other_Links'].append(url)
                        
            except Exception as e:
                logger.warning(f"Error parsing URL {url}: {e}")
                continue
        
        # Convert Other_Links to string and limit
        links['Other_Links'] = ', '.join(links['Other_Links'][:3]) if links['Other_Links'] else None
        
        return links
        
    except Exception as e:
        logger.error(f"Error extracting social links: {e}")
        return {key: None for key in links.keys()}

def scrape_with_fallback(channel_url, session, max_retries=2):
    """Scrape with fallback strategies and retry logic"""
    
    for attempt in range(max_retries + 1):
        try:
            # Normalize URL - try both /about and /channels/[ID]/about
            base_url = channel_url.rstrip('/')
            about_urls = [
                f"{base_url}/about",
                f"{base_url}/channels/about" if '/channel/' in base_url else f"{base_url}/about"
            ]
            
            for about_url in about_urls:
                logger.info(f"Attempt {attempt + 1}: Scraping {about_url}")
                
                # Random delay before request
                time.sleep(random.uniform(2, 6))
                
                # Update user agent for this request
                session.headers.update({'User-Agent': get_random_user_agent()})
                
                try:
                    response = session.get(about_url, timeout=15)
                    response.raise_for_status()
                    
                    # Check if we got a valid YouTube page
                    if 'youtube' not in response.url.lower():
                        logger.warning(f"Redirected away from YouTube: {response.url}")
                        continue
                    
                    # Check for blocking indicators
                    page_content = response.text.lower()
                    if any(indicator in page_content for indicator in [
                        'captcha', 'blocked', 'unusual traffic', 'robot', 'automated'
                    ]):
                        logger.warning(f"Possible blocking detected on attempt {attempt + 1}")
                        if attempt < max_retries:
                            time.sleep(random.uniform(30, 60))
                            break  # Try next attempt
                        else:
                            return {key: None for key in ['Website', 'Instagram', 'Twitter', 'TikTok', 'Facebook', 'LinkedIn', 'Other_Links']}
                    
                    # Parse the page
                    soup = BeautifulSoup(response.text, 'html.parser')
                    social_links = extract_social_links_safer(soup, about_url)
                    
                    # If we found something, return it
                    if any(social_links.values()):
                        logger.info(f"Successfully extracted links on attempt {attempt + 1}")
                        return social_links
                    
                except requests.exceptions.RequestException as e:
                    logger.warning(f"Request failed for {about_url}: {e}")
                    continue
            
            # If no URLs worked and we have retries left, try again
            if attempt < max_retries:
                logger.info(f"No links found on attempt {attempt + 1}, retrying...")
                time.sleep(random.uniform(10, 20))
                continue
            
            return {key: None for key in ['Website', 'Instagram', 'Twitter', 'TikTok', 'Facebook', 'LinkedIn', 'Other_Links']}
            
        except Exception as e:
            logger.error(f"Error on attempt {attempt + 1} for {channel_url}: {str(e)}")
            if attempt < max_retries:
                time.sleep(random.uniform(10, 20))
                continue
    
    # All attempts failed
    return {key: None for key in ['Website', 'Instagram', 'Twitter', 'TikTok', 'Facebook', 'LinkedIn', 'Other_Links']}

def process_dataframe_safer(df, url_column, delay_range=(10, 30), batch_size=5):
    """Process dataframe with advanced safety measures using requests"""
    session = create_session()
    
    try:
        # Add new columns
        new_columns = ['Website', 'Instagram', 'Twitter', 'TikTok', 'Facebook', 'LinkedIn', 'Other_Links', 'Processing_Status']
        for col in new_columns:
            if col not in df.columns:
                df[col] = None
        
        # Progress tracking
        progress_bar = st.progress(0)
        status_text = st.empty()
        results_container = st.container()
        
        total_rows = len(df)
        processed_count = 0
        success_count = 0
        blocked_count = 0
        
        for idx, row in df.iterrows():
            channel_url = row[url_column]
            
            if pd.isna(channel_url) or not channel_url:
                df.at[idx, 'Processing_Status'] = 'Skipped (No URL)'
                processed_count += 1
                continue
            
            status_text.text(f"Processing {idx + 1}/{total_rows}: {str(channel_url)[:50]}...")
            
            # Scrape with retries and fallbacks
            social_links = scrape_with_fallback(channel_url, session)
            
            # Update dataframe
            links_found = 0
            for key, value in social_links.items():
                if key != 'Processing_Status':
                    df.at[idx, key] = value
                    if value:
                        links_found += 1
            
            # Set processing status
            if links_found > 0:
                success_count += 1
                df.at[idx, 'Processing_Status'] = f'Success ({links_found} links found)'
            else:
                df.at[idx, 'Processing_Status'] = 'No links found'
            
            processed_count += 1
            progress_bar.progress(processed_count / total_rows)
            
            # Show live results every 5 processed
            if processed_count % 5 == 0:
                with results_container:
                    st.write(f"**Progress Update:** {processed_count}/{total_rows} processed")
                    col1, col2, col3 = st.columns(3)
                    with col1:
                        st.metric("✅ Success", success_count)
                    with col2:
                        st.metric("❌ No Links", processed_count - success_count - blocked_count)
                    with col3:
                        st.metric("🚫 Possibly Blocked", blocked_count)
            
            # Dynamic delay based on success rate
            if blocked_count > 2:  # If we're getting blocked frequently
                delay_multiplier = 2
                status_text.text("⚠️ Detected possible blocking, using longer delays...")
            else:
                delay_multiplier = 1
            
            # Delays between requests
            if processed_count % batch_size == 0:
                delay = random.uniform(delay_range[0] * 2 * delay_multiplier, delay_range[1] * 2 * delay_multiplier)
                status_text.text(f"Taking a longer break... ({delay:.1f}s)")
                time.sleep(delay)
            else:
                time.sleep(random.uniform(delay_range[0] * delay_multiplier, delay_range[1] * delay_multiplier))
        
        # Final status
        final_status = f"Complete! {success_count}/{processed_count} channels had links found."
        if blocked_count > 0:
            final_status += f" ({blocked_count} possibly blocked)"
        status_text.text(final_status)
        
        return df
        
    except KeyboardInterrupt:
        st.warning("⚠️ Processing stopped by user. Partial results saved.")
        return df
    except Exception as e:
        st.error(f"Error during processing: {str(e)}")
        return df
    finally:
        session.close()

def main():
    st.title("🎬 YouTube Channel Data Enhancer - Streamlit Cloud Compatible")
    st.markdown("""
    **✅ Now compatible with Streamlit Cloud!** This version uses requests instead of Selenium.
    
    **Features:**
    - No browser dependencies - works on Streamlit Cloud
    - Advanced anti-detection measures
    - Human-like behavior simulation  
    - Retry logic with exponential backoff
    - Randomized delays and user agents
    """)
    
    # Risk warning
    with st.expander("⚠️ Usage Guidelines - Please Read"):
        st.info("""
        **Best Practices:**
        - Use reasonable delays between requests (10-30 seconds recommended)
        - Process small batches (20-50 channels at a time)
        - Respect rate limits and be considerate of server resources
        - Consider the ethical implications of web scraping
        
        **Note:** This tool extracts publicly available information from YouTube channel about pages.
        Success rates may vary based on YouTube's current page structure and anti-bot measures.
        """)
    
    # Configuration
    st.subheader("⚙️ Configuration")
    
    col1, col2 = st.columns(2)
    with col1:
        min_delay = st.slider("Minimum delay between requests (seconds)", 5, 30, 12)
        batch_size = st.slider("Batch size before longer break", 3, 10, 5)
    
    with col2:
        max_delay = st.slider("Maximum delay between requests (seconds)", 15, 60, 25)
        max_channels = st.number_input("Max channels to process (0 = all)", 0, 100, 0)
    
    # Tips for better results
    st.success("💡 **Streamlit Cloud Optimized:**\n"
               "• No browser installation needed\n"
               "• Faster and more reliable than Selenium\n" 
               "• Better resource efficiency\n"
               "• Improved stealth capabilities")
    
    # File upload
    uploaded_file = st.file_uploader(
        "Choose your CSV or Excel file",
        type=['csv', 'xlsx', 'xls'],
        help="File should contain YouTube channel URLs"
    )
    
    if uploaded_file is not None:
        try:
            # Read file
            if uploaded_file.name.endswith('.csv'):
                df = pd.read_csv(uploaded_file)
            else:
                df = pd.read_excel(uploaded_file)
            
            if max_channels > 0:
                df = df.head(max_channels)
            
            st.success(f"File loaded! Processing {len(df)} rows.")
            
            # Column selection
            url_columns = [col for col in df.columns if any(keyword in col.lower() 
                          for keyword in ['url', 'link', 'channel', 'youtube'])]
            
            if not url_columns:
                url_columns = list(df.columns)
            
            selected_url_column = st.selectbox(
                "Select YouTube URL column:",
                options=url_columns
            )
            
            # Estimate time
            youtube_urls = df[selected_url_column].dropna()
            estimated_time = len(youtube_urls) * (min_delay + max_delay) / 2 / 60
            st.info(f"📊 **Processing Estimate:**\n"
                   f"• {len(youtube_urls)} YouTube URLs found\n"
                   f"• Estimated time: {estimated_time:.1f} minutes\n"
                   f"• Compatible with Streamlit Cloud")
            
            # Show sample URLs
            if len(youtube_urls) > 0:
                with st.expander("Preview URLs to be processed"):
                    for i, url in enumerate(youtube_urls.head(5), 1):
                        st.write(f"{i}. {url}")
                    if len(youtube_urls) > 5:
                        st.write(f"... and {len(youtube_urls) - 5} more")
            
            # Processing
            if st.button("🚀 Start Processing (Streamlit Cloud Ready)", type="primary"):
                if st.session_state.get('processing', False):
                    st.warning("Already processing...")
                else:
                    st.session_state.processing = True
                    
                    try:
                        st.info("🌐 Starting HTTP requests with stealth headers...")
                        
                        enhanced_df = process_dataframe_safer(
                            df.copy(), 
                            selected_url_column,
                            delay_range=(min_delay, max_delay),
                            batch_size=batch_size
                        )
                        
                        if enhanced_df is not None:
                            st.success("✅ Processing completed!")
                            
                            # Enhanced results display
                            st.subheader("📊 Results Summary")
                            
                            # Status breakdown
                            if 'Processing_Status' in enhanced_df.columns:
                                status_counts = enhanced_df['Processing_Status'].value_counts()
                                
                                col1, col2, col3, col4 = st.columns(4)
                                with col1:
                                    success_count = len([s for s in status_counts.index if 'Success' in str(s)])
                                    if success_count > 0:
                                        total_success = sum([status_counts[s] for s in status_counts.index if 'Success' in str(s)])
                                        st.metric("✅ Successful", total_success)
                                    else:
                                        st.metric("✅ Successful", 0)
                                
                                with col2:
                                    no_links = status_counts.get('No links found', 0)
                                    st.metric("❌ No Links", no_links)
                                
                                with col3:
                                    blocked = status_counts.get('Possibly blocked', 0)
                                    st.metric("🚫 Blocked", blocked)
                                
                                with col4:
                                    skipped = status_counts.get('Skipped (No URL)', 0)
                                    st.metric("⏭️ Skipped", skipped)
                                
                                # Detailed status breakdown
                                with st.expander("Detailed Status Breakdown"):
                                    st.dataframe(status_counts.to_frame('Count'), use_container_width=True)
                            
                            # Platform breakdown
                            st.subheader("🌐 Platform Breakdown")
                            social_columns = ['Website', 'Instagram', 'Twitter', 'TikTok', 'Facebook', 'LinkedIn']
                            platform_data = []
                            
                            for platform in social_columns:
                                if platform in enhanced_df.columns:
                                    found = enhanced_df[platform].notna().sum()
                                    percentage = (found / len(enhanced_df) * 100) if len(enhanced_df) > 0 else 0
                                    platform_data.append({
                                        'Platform': platform,
                                        'Links Found': found,
                                        'Percentage': f"{percentage:.1f}%"
                                    })
                            
                            platform_df = pd.DataFrame(platform_data)
                            st.dataframe(platform_df, use_container_width=True, hide_index=True)
                            
                            # Show enhanced data
                            st.subheader("📈 Enhanced Dataset")
                            st.dataframe(enhanced_df, use_container_width=True)
                            
                            # Create download
                            timestamp = time.strftime("%Y%m%d_%H%M%S")
                            if uploaded_file.name.endswith('.csv'):
                                output = enhanced_df.to_csv(index=False)
                                filename = f"enhanced_youtube_data_{timestamp}.csv"
                                mime = 'text/csv'
                            else:
                                output = io.BytesIO()
                                enhanced_df.to_excel(output, index=False, engine='openpyxl')
                                output.seek(0)
                                output = output.getvalue()
                                filename = f"enhanced_youtube_data_{timestamp}.xlsx"
                                mime = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                            
                            st.download_button(
                                "💾 Download Enhanced Data",
                                data=output,
                                file_name=filename,
                                mime=mime,
                                help="Download your enhanced dataset with social media links"
                            )
                            
                            # Success feedback
                            success_rate = len([s for s in enhanced_df.get('Processing_Status', []) if 'Success' in str(s)]) / len(enhanced_df) * 100
                            if success_rate < 50:
                                st.warning(f"📊 Success rate was {success_rate:.1f}%. Consider:\n"
                                          "• Using longer delays between requests\n"
                                          "• Processing during different hours\n"
                                          "• Splitting into smaller batches")
                            else:
                                st.success(f"🎉 Great success rate: {success_rate:.1f}%! 🎉")
                    
                    finally:
                        st.session_state.processing = False
                        
        except Exception as e:
            st.error(f"Error: {str(e)}")

if __name__ == "__main__":
    if 'processing' not in st.session_state:
        st.session_state.processing = False
    main()
