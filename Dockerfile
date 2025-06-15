FROM python:3.9-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    unzip \
    curl \
    jq \
    && rm -rf /var/lib/apt/lists/*

# Install Chrome
RUN wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | apt-key add - \
    && echo "deb http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google.list \
    && apt-get update \
    && apt-get install -y google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

# Install ChromeDriver with support for both old and new distribution methods
RUN CHROME_VERSION=$(google-chrome --version | awk '{print $3}' | cut -d '.' -f 1) \
    && if [ "$CHROME_VERSION" -lt 115 ]; then \
        # Old method for Chrome versions before 115
        CHROMEDRIVER_VERSION=$(wget -qO- "https://chromedriver.storage.googleapis.com/LATEST_RELEASE_$CHROME_VERSION") \
        && wget -q "https://chromedriver.storage.googleapis.com/$CHROMEDRIVER_VERSION/chromedriver_linux64.zip" -O /tmp/chromedriver.zip; \
    else \
        # New method for Chrome 115+ (including 136+)
        LATEST_RELEASE=$(curl -s "https://googlechromelabs.github.io/chrome-for-testing/last-known-good-versions-with-downloads.json") \
        && CHROMEDRIVER_URL=$(echo "$LATEST_RELEASE" | jq -r ".channels.Stable.downloads.chromedriver[] | select(.platform == \"linux64\") | .url") \
        && wget -q "$CHROMEDRIVER_URL" -O /tmp/chromedriver.zip; \
    fi \
    && unzip /tmp/chromedriver.zip -d /tmp/ \
    && if [ "$CHROME_VERSION" -lt 115 ]; then \
        mv /tmp/chromedriver /usr/local/bin/chromedriver; \
    else \
        mv /tmp/chromedriver-*/chromedriver /usr/local/bin/chromedriver; \
    fi \
    && rm -rf /tmp/chromedriver* \
    && chmod +x /usr/local/bin/chromedriver

# Set up app directory
WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Expose port for the app
EXPOSE 5000

# Set environment variables for Chrome
ENV PYTHONUNBUFFERED=1 \
    DISPLAY=:99 \
    CHROME_BIN=/usr/bin/google-chrome \
    CHROMEDRIVER_PATH=/usr/local/bin/chromedriver

# Run the application with gunicorn
CMD gunicorn --bind 0.0.0.0:$PORT app:app
