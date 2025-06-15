import os

# Gunicorn configuration file
# https://docs.gunicorn.org/en/stable/configure.html

# Server socket
bind = f"0.0.0.0:{os.environ.get('PORT', '5000')}"

# Worker processes
workers = 1
threads = 2
worker_class = 'sync'

# Timeout - increased to accommodate longer processing times
timeout = 300  # 5 minutes

# Maximum requests a worker will process before restarting
max_requests = 5
max_requests_jitter = 2

# Process naming
proc_name = 'youtube-links-scraper'

# Server mechanics
daemon = False
pidfile = None
umask = 0
user = None
group = None
tmp_upload_dir = None

# Logging
errorlog = '-'
loglevel = 'info'
accesslog = '-'
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s"'

# Server hooks
def on_starting(server):
    server.log.info("Starting YouTube Links Scraper")

def on_exit(server):
    server.log.info("Shutting down YouTube Links Scraper")
