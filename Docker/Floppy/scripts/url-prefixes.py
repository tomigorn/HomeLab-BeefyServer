# First path segments of Floppy's URL map, to compare with the Traefik allow-list (README "Edge allow-list").
# sudo docker exec -i -u abc -w /floppy floppy python manage.py shell < scripts/url-prefixes.py
import re
from django.urls import URLResolver, get_resolver


def walk(patterns, prefix=""):
    for p in patterns:
        if isinstance(p, URLResolver):
            yield from walk(p.url_patterns, prefix + str(p.pattern))
        else:
            yield prefix + str(p.pattern)


segments = {re.split(r"[/<(]", re.sub(r"^\^", "", u), maxsplit=1)[0] for u in walk(get_resolver().url_patterns)}
print("PREFIXES", " ".join(sorted(s for s in segments if s)))
