# Browser tests (tests/test_bar_layout.py): the Playwright image ships Chromium 1148 but not the Python package; same version as the browsers.
FROM mcr.microsoft.com/playwright/python:v1.49.0-noble
RUN pip install --no-cache-dir --break-system-packages playwright==1.49.0
