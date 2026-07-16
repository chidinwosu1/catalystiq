"""Deployment entrypoint. The application now lives in the `catalystiq`
package (see catalystiq/main.py); this re-export keeps `app.py:app`
working for existing deployment configs.
"""
from catalystiq.main import app

__all__ = ["app"]
