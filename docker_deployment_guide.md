# Docker Deployment Guide for Selenium YouTube Scraper

This guide walks you through deploying your Selenium-based YouTube scraper using Docker, which can be used with multiple hosting platforms.

## Step 1: Create a Dockerfile

Create a file named `Dockerfile` in your project root:

```dockerfile
FROM python:3.9-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# Install Chrome
RUN wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | apt-key add - \
    && echo "deb http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google.list \
    && apt-get update \
    && apt-get install -y google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

# Install ChromeDriver
RUN CHROME_VERSION=$(google-chrome --version | awk '{print $3}' | cut -d '.' -f 1) \
    && CHROMEDRIVER_VERSION=$(wget -qO- "https://chromedriver.storage.googleapis.com/LATEST_RELEASE_$CHROME_VERSION") \
    && wget -q "https://chromedriver.storage.googleapis.com/$CHROMEDRIVER_VERSION/chromedriver_linux64.zip" -O /tmp/chromedriver.zip \
    && unzip /tmp/chromedriver.zip -d /usr/local/bin/ \
    && rm /tmp/chromedriver.zip \
    && chmod +x /usr/local/bin/chromedriver

# Set up app directory
WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Expose port for the app
EXPOSE 8501

# Set environment variables for Chrome
ENV PYTHONUNBUFFERED=1 \
    DISPLAY=:99 \
    CHROME_BIN=/usr/bin/google-chrome \
    CHROMEDRIVER_PATH=/usr/local/bin/chromedriver

# Run the application
CMD ["streamlit", "run", "youtube_links_scraper.py", "--server.port", "8501", "--server.address", "0.0.0.0"]
```

## Step 2: Update your requirements.txt

Ensure your `requirements.txt` includes all necessary dependencies:

```
streamlit>=1.28.0
pandas>=1.5.0
selenium>=4.10.0
webdriver-manager>=3.8.6
openpyxl>=3.1.0
urllib3>=2.0.0
```

## Step 3: Modify your Selenium code

Update your `setup_selenium_driver()` function to work in a containerized environment:

```python
def setup_selenium_driver():
    """Setup Chrome WebDriver with anti-detection options for containerized environment"""
    chrome_options = Options()
    
    # Required for Docker
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
        st.error(f"Failed to setup Chrome WebDriver: {str(e)}")
        return None
```

## Step 4: Create a docker-compose.yml file (optional)

For local testing, create a `docker-compose.yml` file:

```yaml
version: '3'

services:
  youtube-scraper:
    build: .
    ports:
      - "8501:8501"
    volumes:
      - .:/app
```

## Step 5: Build and test locally

```bash
# Build the Docker image
docker build -t youtube-scraper .

# Run the container
docker run -p 8501:8501 youtube-scraper
```

## Step 6: Deploy to a hosting platform

### Option A: Heroku

1. Install Heroku CLI
2. Login and create app:
```bash
heroku login
heroku create your-app-name
```

3. Add Heroku.yml file:
```yaml
build:
  docker:
    web: Dockerfile
```

4. Deploy:
```bash
heroku stack:set container
git add .
git commit -m "Docker deployment"
git push heroku main
```

### Option B: DigitalOcean App Platform

1. Push your code to GitHub
2. Connect your GitHub repo to DigitalOcean App Platform
3. Select "Dockerfile" as the deployment method
4. Configure environment variables if needed
5. Deploy

### Option C: Google Cloud Run

```bash
# Build and push to Google Container Registry
gcloud builds submit --tag gcr.io/YOUR_PROJECT_ID/youtube-scraper

# Deploy to Cloud Run
gcloud run deploy youtube-scraper \
  --image gcr.io/YOUR_PROJECT_ID/youtube-scraper \
  --platform managed \
  --allow-unauthenticated
```

## Troubleshooting

If you encounter issues:

1. Check container logs:
```bash
docker logs <container_id>
```

2. For Heroku:
```bash
heroku logs --tail
```

3. Common issues:
   - Chrome crashes: Increase memory allocation
   - Network issues: Check firewall/proxy settings
   - Timeouts: Adjust wait times in your code
