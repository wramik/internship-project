import sys

PROJECT_DIR = "/app"
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

from web_service import app as application, init_db

init_db()
