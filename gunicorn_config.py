"""
Gunicorn config for Stock Web app.

CRITICAL: Use workers=1 with SQLite.
SQLite uses file-level locking. Multiple Gunicorn workers = multiple processes
accessing the same .db file = "database is locked" / "database is busy" errors.
"""
import os

bind = "0.0.0.0:5000"
workers = 1   # MUST be 1 for SQLite - multi-worker causes DB locks in production
worker_class = "sync"
threads = 4  # Threads per worker (all share same process/DB connections)
timeout = 300
keepalive = 5

# Ensure we run from app directory so DB paths resolve correctly
chdir = os.path.dirname(os.path.abspath(__file__))
