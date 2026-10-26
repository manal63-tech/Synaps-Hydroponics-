"""WSGI entrypoint for production servers (gunicorn)."""
from hydromon import create_app

app = create_app()
