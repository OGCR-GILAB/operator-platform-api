import os

bind = "0.0.0.0:8000"
workers = int(os.environ.get("GUNICORN_WORKERS", "3"))
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "60"))
accesslog = "-"
errorlog = "-"
forwarded_allow_ips = "*"
