"""Test defaults: per-IP rate limits off (guards.py reads this per request; tests that check limits unset it)."""
import os

os.environ.setdefault("RATE_LIMITS_OFF", "1")
