#!/usr/bin/env python3
"""The audio-<code> release-name patterns in scripts/languages.tsv against real and typical release names.
Usage: python3 tests/test_audio_terms.py"""
import pathlib
import re
import unittest

TSV = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "languages.tsv"

MATCH = {
    "gsw": ["Bon.Schuur.Ticino.2023.SwissGerman.AC3.1080p.WEB.H264-ZeroTwo",
            "Achtung.fertig.WK.Swissgerman.2013.BDRiP.x264-EXQUiSiTE",
            "Der.Bachelor.CH.S08E01.SWISSGERMAN.720p.WEB.x264-BTGTV",
            "Ein.Schweinchen.namens.Babe.1995.German.Oesterreichisch.Schweizerduetsch.AC3.ML.1080p.BluRay.x265-FuN",
            "Wolkenbruch.2018.Swiss.German.1080p.WEB-DL.AAC2.0.H.264",
            "Film.2019.Schweizerdeutsch.DL.1080p.WEB.x264",
            "Film.2019.Schwiizerdütsch.1080p.WEB.x264"],
    "de": ["Knives.Out.2019.German.DL.2160p.UHD.BluRay.HDR.x265-GROUP",
           "The.Ring.Thing.Das.Ding.mit.dem.Ring.German.2004.AC3.DVDRiP.x264-IND",
           "Money.Heist.S01E01.GERMAN.ML.1080p.NF.WEB.x264-GROUP",
           "Film.2019.GER.DUB.1080p.WEB.x264"],
    "en": ["Money.Heist.S01E01.SPANISH.ENGLISH.1080p.NF.WEB-DL.DDP5.1.x264-GROUP",
           "Frieren.S01E01.1080p.BluRay.Dual.Audio.FLAC.x265-GROUP",
           "Amelie.2001.FRENCH.ENG.1080p.BluRay.x264-GROUP",
           "[Group] Frieren - 01 (1080p) [Dual-Audio]"],
    "fr": ["Amelie.2001.MULTi.VFF.1080p.BluRay.x264-GROUP", "Film.2010.TRUEFRENCH.720p.BluRay.x264",
           "Film.2010.FRENCH.1080p.WEB.H264", "Film.2010.MULTi.VF2.2160p.UHD.BluRay.x265"],
    "it": ["Film.2010.iTALiAN.1080p.BluRay.x264", "Film.2010.ITA.ENG.1080p.BluRay.x264"],
    "es": ["La.Casa.de.Papel.S01E01.SPANISH.1080p.WEB", "Film.2010.Castellano.1080p", "Film.2010.LATINO.1080p",
           "Film.2010.ESP.ENG.1080p"],
    "ja": ["Film.2010.JPN.1080p.BluRay", "Film.2010.Japanese.1080p.BluRay"],
    "ko": ["Film.2010.KOR.1080p.BluRay", "Parasite.2019.KOREAN.1080p.BluRay.x264"],
    "pt": ["Film.2010.PT-BR.1080p.WEB", "Film.2010.Dublado.1080p.WEB", "Film.2010.Portuguese.1080p"],
    "nl": ["Film.2010.DUTCH.1080p.WEB", "Film.2010.Flemish.1080p.WEB"],
    "ru": ["Film.2010.RUS.1080p.BluRay", "Film.2010.Russian.1080p.BluRay"],
    "sv": ["Film.2010.SWEDiSH.1080p.BluRay", "Film.2010.SWE.1080p.BluRay"],
    "da": ["Film.2010.DANiSH.1080p.BluRay"],
    "nb": ["Film.2010.NORWEGiAN.1080p.BluRay", "Film.2010.NORSK.1080p.WEB"],
    "zh": ["Film.2010.CHINESE.1080p.BluRay", "Film.2010.Mandarin.1080p.WEB", "Film.2010.CHS.1080p.WEB"],
    "pl": ["Film.2010.PLDUB.1080p.WEB", "Film.2010.POLISH.1080p.BluRay"],
    "tr": ["Film.2010.TURKISH.1080p.WEB"],
}
NO_MATCH = {
    "gsw": ["Archer.S02E01.Swiss.Miss.GERMAN.DL.EAC3.1080p.WEB-DL.x264-TvR",
            "100.Wolf.S02E08.Swiss.kehrt.zuruck.German.1080p.RTLP.WEB-DL.AAC2.0.H.264-GLOTZE",
            "Knives.Out.2019.German.DL.2160p.UHD.BluRay.HDR.x265-GROUP"],
    "de": ["Knives.Out.2019.2160p.UHD.BluRay.x265-GROUP", "The.Man.From.Germany.2019.1080p.WEB-DL.x264",
           "Film.2019.1080p.WEB-DL.DDP5.1.H.264-GROUP"],
    "en": ["Amelie.2001.FRENCH.1080p.BluRay.x264-GROUP", "The.Englishman.Who.Went.Up.a.Hill.1995.1080p.WEB"],
    "fr": ["Film.2010.VOSTFR.1080p.WEB", "Film.2010.1080p.BluRay.x264-FrenchFry"],
    "es": ["Esperanza.2010.1080p.WEB", "Spa.Night.2016.1080p.WEB"],
    "ja": ["Lost.in.Japan.2010.1080p.WEB"],
    "ko": ["Korea.2010.1080p.WEB"],
    "pt": ["Portugal.2010.1080p.WEB", "Por.un.punado.de.dolares.1964.1080p.BluRay"],
    "da": ["Dan.in.Real.Life.2007.1080p.BluRay"],
    "zh": ["Chi-Raq.2015.1080p.WEB"],
    "tr": ["Tur.2010.1080p.WEB"],
}


def load():
    rows = {}
    for line in TSV.read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or not line.strip():
            continue
        code, name, audio, subs = line.split("\t")
        rows[code] = (name, audio, subs)
    return rows


class AudioTerms(unittest.TestCase):
    rows = load()

    def test_every_row_is_well_formed(self):
        for code, (name, audio, subs) in self.rows.items():
            self.assertRegex(code, r"^[a-z]{2,3}$")
            self.assertTrue(name)
            self.assertIn(subs, ("yes", "no"))
            if audio != "-":
                re.compile(audio, re.I)

    def test_required_codes_have_audio_tags(self):
        for code in ("gsw", "de", "en", "fr", "it", "es", "ja", "ko"):
            self.assertNotEqual(self.rows[code][1], "-", code)

    def test_matches(self):
        for code, names in MATCH.items():
            for n in names:
                with self.subTest(code=code, name=n):
                    self.assertRegex(n, re.compile(self.rows[code][1], re.I))

    def test_no_matches(self):
        for code, names in NO_MATCH.items():
            for n in names:
                with self.subTest(code=code, name=n):
                    self.assertNotRegex(n, re.compile(self.rows[code][1], re.I))


if __name__ == "__main__":
    unittest.main()
