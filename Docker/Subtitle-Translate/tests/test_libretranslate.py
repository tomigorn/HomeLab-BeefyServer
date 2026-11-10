#!/usr/bin/env python3
"""Functional test of LibreTranslate: a fixed German sentence to English and back, exact outputs (same models, same version = same text).
Live (no argument): the running subtitle-libretranslate, plus the subtitle path the nightly job uses (SRT -> scripts/subtitle-translate.py -> Lingarr -> LibreTranslate), plus "the running image is the local build".
--image IMAGE: a throwaway container of IMAGE (no network, models read-only, en+de only) before it replaces the running one (rebuild-local.conf test).
Usage: python3 tests/test_libretranslate.py [--image IMAGE]"""
import importlib.util
import json
import pathlib
import subprocess
import sys
import time
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("st", ROOT / "scripts" / "subtitle-translate.py")
st = importlib.util.module_from_spec(spec)
spec.loader.exec_module(st)

CONTAINER = "subtitle-libretranslate"
LOCAL_IMAGE = "libretranslate-local:v1.9.6"
VERSION = "1.9.6"
MODELS = "/srv/appdata/libretranslate"
DE = "Ich habe heute keine Zeit, weil ich arbeiten muss."
EN = "I don’t have time today because I have to work."
SRT_DE = "1\n00:00:01,000 --> 00:00:03,000\n" + DE + "\n\n2\n00:00:04,000 --> 00:00:05,500\nBis morgen!\n\n3\n00:00:06,000 --> 00:00:07,000\n♪ ♪\n\n"
SRT_EN = "1\n00:00:01,000 --> 00:00:03,000\n" + EN + "\n\n2\n00:00:04,000 --> 00:00:05,500\nUntil tomorrow!\n\n3\n00:00:06,000 --> 00:00:07,000\n♪ ♪\n\n"
TRANSLATE = ("import json,sys,urllib.request as u; q,s,t=sys.argv[1:4]; "
             "print(json.load(u.urlopen(u.Request('http://127.0.0.1:5000/translate', json.dumps({'q':q,'source':s,'target':t}).encode(), "
             "{'Content-Type':'application/json'}), timeout=120))['translatedText'])")


def docker_cmd():
    return ["docker"] if subprocess.run(["docker", "info"], capture_output=True).returncode == 0 else ["sudo", "-n", "docker"]


DOCKER = docker_cmd()


def docker(*args, timeout=180):
    return subprocess.run([*DOCKER, *args], capture_output=True, text=True, timeout=timeout)


def translate(container, q, src, tgt):
    r = docker("exec", container, "./venv/bin/python", "-c", TRANSLATE, q, src, tgt)
    if r.returncode != 0:
        raise AssertionError(f"translate {src}->{tgt} failed: {r.stderr.strip()[-300:]}")
    return r.stdout.rstrip("\n")


def lt_version(container):
    return docker("exec", container, "./venv/bin/python", "-c", "import importlib.metadata as m; print(m.version('libretranslate'))").stdout.strip()


class Api:
    container = CONTAINER

    def test_german_to_english_and_back(self):
        en = translate(self.container, DE, "de", "en")
        self.assertEqual(en, EN)
        self.assertEqual(translate(self.container, en, "en", "de"), DE)

    def test_same_libretranslate_version(self):
        self.assertEqual(lt_version(self.container), VERSION)


class Live(Api, unittest.TestCase):
    def test_running_image_is_the_local_build(self):
        self.assertEqual(docker("inspect", "-f", "{{.Config.Image}}", CONTAINER).stdout.strip(), LOCAL_IMAGE)

    def test_subtitle_path_through_lingarr(self):
        key = (ROOT / "secrets" / "lingarr.apikey").read_text().strip()
        port = st.env_file().get("LINGARR_PORT", "9876")
        apis = types.SimpleNamespace(lingarr=st.Http(f"http://127.0.0.1:{port}/api", {"X-Api-Key": key}, timeout=300))
        apis.translate = lambda src, tgt, lines, title: st.RealApis.translate(apis, src, tgt, lines, title)
        cues = st.parse_srt(SRT_DE)
        done = st.translate_cues(st.Config(), apis, cues, "de", "en", "functional test")
        self.assertEqual(st.render_srt(cues, done), SRT_EN)


class Image(Api, unittest.TestCase):
    image = ""

    @classmethod
    def setUpClass(cls):
        cls.container = f"lt-test-{int(time.time())}"
        r = docker("run", "-d", "--rm", "--name", cls.container, "--network", "none", "--user", "65130:65130",
                   "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--memory", "3g",
                   "-e", "LT_LOAD_ONLY=en,de", "-e", "LT_UPDATE_MODELS=false", "-e", "LT_THREADS=1", "-e", "LT_DISABLE_WEB_UI=true",
                   "-e", "HOME=/home/libretranslate", "--tmpfs", "/app/db:uid=65130,gid=65130", "--tmpfs", "/tmp:uid=65130,gid=65130",
                   "-v", f"{MODELS}:/home/libretranslate:ro", cls.image)
        if r.returncode != 0:
            raise RuntimeError(f"start {cls.image}: {r.stderr.strip()[-300:]}")
        deadline = time.time() + 300
        while time.time() < deadline:
            ok = docker("exec", cls.container, "./venv/bin/python", "-c", TRANSLATE, "Hallo", "de", "en", timeout=60)
            if ok.returncode == 0 and ok.stdout.strip():
                return
            if docker("inspect", "-f", "{{.State.Running}}", cls.container).stdout.strip() != "true":
                break
            time.sleep(5)
        logs = docker("logs", "--tail", "20", cls.container)
        docker("rm", "-f", cls.container)
        raise RuntimeError(f"{cls.image} did not answer: {(logs.stdout + logs.stderr).strip()[-500:]}")

    @classmethod
    def tearDownClass(cls):
        docker("rm", "-f", cls.container)


if __name__ == "__main__":
    if "--image" in sys.argv:
        i = sys.argv.index("--image")
        Image.image = sys.argv[i + 1]
        del sys.argv[i:i + 2]
        suite = unittest.TestLoader().loadTestsFromTestCase(Image)
    else:
        suite = unittest.TestLoader().loadTestsFromTestCase(Live)
    sys.exit(0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1)
