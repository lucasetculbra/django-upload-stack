"""Settings module used by the test suite.

Safe defaults are applied *before* the real settings are imported, so `pytest` runs on a
developer machine with no .env file and no database. os.environ.setdefault never
overrides an existing value, so CI can point the very same suite at PostgreSQL simply by
exporting DJANGO_DB=postgres and the POSTGRES_* variables.
"""

import os

os.environ.setdefault("DJANGO_DB", "sqlite")
os.environ.setdefault("SECRET_KEY", "test-only-secret-key-not-used-in-production")
os.environ.setdefault("ALLOWED_HOSTS", "localhost,127.0.0.1,testserver,web")

from config.settings import *  # noqa: E402, F403
