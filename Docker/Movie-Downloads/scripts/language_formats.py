#!/usr/bin/env python3
"""The native language custom formats Recyclarr cannot express and their scores (README "Release scoring"), used by language-policy.sh.
  language_formats.py formats < languages.json          the custom formats for an app's GET /language list
  language_formats.py profile '<name:id JSON>' < p.json  that quality profile with their scores set (problems on stderr, exit 1)"""
import json
import sys

ORIGINAL_ENGLISH = "Language: Original + English"
EXTRA_PREFIX = "Extra audio: "
ORIGINAL_ENGLISH_SCORE = 5_000_000
# Each extra dub: a tie-breaker, below the minimum upgrade step (README "Which version gets downloaded"); none in the Swiss German and anime profiles (step 1 there).
EXTRA_SCORE = 1
SWISS = "Swiss German (Mundart) only - any quality"
ANIME_PREFIX = "Anime - "
SWISS_ORIGINAL_ENGLISH_SCORE = 20_000
ENGLISH_ORIGINAL_SUFFIX = " - English original"
NOT_EXTRA = {"Any", "Original", "Unknown", "English", "German"}
ORIGINAL = -2
ENGLISH = 1


def spec(name, value, except_language=False):
    return {"name": name, "implementation": "LanguageSpecification", "negate": False, "required": True,
            "fields": [{"name": "value", "value": value}, {"name": "exceptLanguage", "value": except_language}]}


def formats(languages):
    # "Not Only The Original Language" on an extra: the title's original language counts only when another language is there too.
    out = [{"name": ORIGINAL_ENGLISH, "includeCustomFormatWhenRenaming": False,
            "specifications": [spec("English", ENGLISH), spec("Original Language", ORIGINAL), spec("Not Only The Original Language", ORIGINAL, True)]}]
    for lang in sorted(languages, key=lambda l: l["name"]):
        if lang["name"] not in NOT_EXTRA:
            out.append({"name": EXTRA_PREFIX + lang["name"], "includeCustomFormatWhenRenaming": False,
                        "specifications": [spec(lang["name"], lang["id"]), spec("Not Only The Original Language", ORIGINAL, True)]})
    return out


def score(profile_name, format_name):
    if format_name == ORIGINAL_ENGLISH:
        if profile_name == SWISS:
            return SWISS_ORIGINAL_ENGLISH_SCORE
        return 0 if profile_name.endswith(ENGLISH_ORIGINAL_SUFFIX) else ORIGINAL_ENGLISH_SCORE
    if format_name.startswith(EXTRA_PREFIX):
        return 0 if profile_name == SWISS or profile_name.startswith(ANIME_PREFIX) else EXTRA_SCORE
    raise KeyError(format_name)


def apply(profile, ids):
    """Sets the scores; returns the problems (none left since extras may upgrade below the cutoff)."""
    names = {i: n for n, i in ids.items()}
    for item in profile["formatItems"]:
        if item["format"] in names:
            item["score"] = score(profile["name"], names[item["format"]])
    return []


def main():
    if sys.argv[1:2] == ["formats"]:
        print(json.dumps(formats(json.load(sys.stdin))))
        return 0
    if sys.argv[1:2] == ["profile"] and len(sys.argv) == 3:
        profile = json.load(sys.stdin)
        problems = apply(profile, json.loads(sys.argv[2]))
        print(json.dumps(profile))
        for p in problems:
            print(p, file=sys.stderr)
        return 1 if problems else 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
