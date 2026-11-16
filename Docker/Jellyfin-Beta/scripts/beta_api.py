"""Prod's Jellyfin API helpers (../Jellyfin/scripts, read-only) pointed at the beta: its port and its own API key."""
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "Jellyfin" / "scripts"))
import jellyfin_api  # noqa: E402

jellyfin_api.BASE = "http://localhost:8196"
jellyfin_api.KEY_FILE = HERE.parent / "secrets" / "api-key"
api = jellyfin_api.api
api_raw = jellyfin_api.api_raw
