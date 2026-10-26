# Links or unlinks one Floppy user's Audiobookshelf sync without opening /import/audiobookshelf/ at the edge (README "Integrations").
# Run by scripts/audiobookshelf-link.sh: env FLOPPY_USER, ABS_ACTION (link|unlink), ABS_ALLOW_ADMIN; the ABS API key on stdin.
import os
import sys

from django_celery_beat.models import PeriodicTask

from integrations import views
from integrations.imports import helpers
from integrations.imports.audiobookshelf import AudiobookshelfClient
from integrations.models import AudiobookshelfAccount
from users.models import User

BASE = "https://audiobookshelf.holy-grail.ch"
user = User.objects.get(username=os.environ["FLOPPY_USER"], is_active=True)
if os.environ.get("ABS_ACTION") == "unlink":
    PeriodicTask.objects.filter(task="Import from Audiobookshelf (Recurring)", **helpers.periodic_task_user_kwargs(user.id)).delete()
    AudiobookshelfAccount.objects.filter(user=user).delete()
    print(f"RESULT unlinked {user.username}")
else:
    token = sys.stdin.read().strip()
    me = AudiobookshelfClient(BASE, token).get_me()
    if me.get("type") in ("root", "admin") and os.environ.get("ABS_ALLOW_ADMIN") != "1":
        print(f"RESULT refused: ABS user {me.get('username')} is {me.get('type')}; use a key of a non-admin ABS user")
    else:
        AudiobookshelfAccount.objects.update_or_create(user=user, defaults={"base_url": BASE, "api_token": helpers.encrypt(token), "connection_broken": False, "last_error_message": ""})
        views._ensure_audiobookshelf_schedule(user)
        print(f"RESULT linked {user.username} -> ABS {me.get('username')} ({me.get('type')}), first import from the recurring schedule (every 15 min, first run right away)")
