"""
fo-intel-pipeline — entry point for FastAPI Cloud deployment.

FastAPI Cloud auto-discovers the `app` object from this file.
The actual app lives in rag/server.py.
"""

from rag.server import app  # noqa: F401
