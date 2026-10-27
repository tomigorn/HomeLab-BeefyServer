# Runs inside floppy (manage.py shell) for scripts/abs-push.py: the payload on stdin, Floppy's own Audiobookshelf importer on pushed data.
# The account row holds a placeholder instead of a key, so Floppy can neither pull from ABS nor fetch covers there; covers arrive in the payload.
import base64
import json
import sys

from django.db import transaction
from django_celery_beat.models import PeriodicTask

from app import image_cache
from app.models import Book
from integrations.imports import audiobookshelf as abs_import
from integrations.imports import helpers
from integrations.models import AudiobookshelfAccount
from users.models import User

BASE = "https://audiobookshelf.holy-grail.ch"
NO_KEY = "pushed-by-abs-push"
COVER_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}


class PushedClient:
    def __init__(self, me, items):
        self.me, self.items = me, items

    def get_me(self):
        return self.me

    def get_library_item(self, library_item_id, *, expanded=False):
        key = f"{library_item_id}?expanded=1" if expanded else str(library_item_id)
        if key not in self.items:
            raise abs_import.AudiobookshelfClientError(f"{library_item_id} not pushed")
        return self.items[key]


class DryRun(Exception):
    pass


def push(payload, user):
    defaults = {"base_url": BASE, "api_token": NO_KEY}
    if payload.get("dry_run"):
        defaults["last_sync_ms"] = 0
    account, _ = AudiobookshelfAccount.objects.update_or_create(user=user, defaults=defaults)
    abs_import.decrypt_or_raise = lambda _token: ""
    importer = abs_import.AudiobookshelfImporter(user)
    importer.client = PushedClient(payload["me"], payload["items"])
    counts, warnings = importer.import_data()
    if payload.get("dry_run"):
        book = Book.objects.get(user=user, item__source="audiobookshelf", item__title="Selftest Book")
        if (book.progress, book.item.format) != (10, "audiobook"):
            return f"failed dry-run progress={book.progress} format={book.item.format}"
        return "ok dry-run"
    stored = 0
    for item_id, cover in payload.get("covers", {}).items():
        body = base64.b64decode(cover["data"])
        if cover["type"] in COVER_TYPES and len(body) <= image_cache.MAX_IMAGE_BYTES:
            image_cache.store_cover(f"abs-cover:{account.id}:{item_id}", body, cover["type"])
            stored += 1
    warn = len(warnings.splitlines()) if warnings else 0
    return f"ok {user.username} rows={len(payload['me']['mediaProgress'])} imported={dict(counts)} covers={stored} warnings={warn}"


payload = json.load(sys.stdin)
if payload.get("dry_run"):
    user = User.objects.filter(is_active=True).order_by("id").first()
else:
    user = User.objects.get(username=payload["user"], is_active=True)
result = None
try:
    with transaction.atomic():
        PeriodicTask.objects.filter(task="Import from Audiobookshelf (Recurring)", **helpers.periodic_task_user_kwargs(user.id)).delete()
        result = push(payload, user)
        if payload.get("dry_run"):
            raise DryRun
except DryRun:
    pass
print(f"RESULT {result}")
