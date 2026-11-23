# ruff: noqa: ANN201, D100, D101, D102, INP001, PT009

import json
import re
import unittest
import warnings
from pathlib import Path

TRANSLATIONS = (
    Path(__file__).resolve().parent.parent
    / "custom_components"
    / "eufy_sdk"
    / "translations"
)
SOURCE = "en.json"
# Languages that must carry every key of en.json. Others (it, nl) may lag behind
# upstream; their missing keys are only reported, never failed on.
COMPLETE_LANGUAGES = ("pl.json",)
PLACEHOLDER = re.compile(r"\{([^{}]*)\}")


def _flatten(tree: dict, prefix: str = "") -> dict[str, str]:
    flat: dict[str, str] = {}
    for key, value in tree.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(_flatten(value, f"{path}."))
        else:
            flat[path] = value
    return flat


def _load(name: str) -> dict[str, str]:
    with (TRANSLATIONS / name).open(encoding="utf-8") as handle:
        return _flatten(json.load(handle))


class TranslationFileTests(unittest.TestCase):
    def setUp(self):
        self.source = _load(SOURCE)
        self.files = sorted(p.name for p in TRANSLATIONS.glob("*.json"))

    def test_every_file_is_valid_json_of_strings(self):
        for name in self.files:
            with self.subTest(file=name):
                flat = _load(name)
                self.assertTrue(flat)
                for key, value in flat.items():
                    self.assertIsInstance(value, str, key)
                    self.assertTrue(value.strip(), f"{name}: empty {key}")

    def test_no_keys_missing_from_source(self):
        for name in self.files:
            with self.subTest(file=name):
                extra = set(_load(name)) - set(self.source)
                self.assertFalse(extra, f"{name} has keys not in {SOURCE}")

    def test_complete_languages_have_every_key(self):
        for name in COMPLETE_LANGUAGES:
            with self.subTest(file=name):
                missing = set(self.source) - set(_load(name))
                self.assertFalse(sorted(missing), f"{name} misses keys")

    def test_partial_languages_are_reported(self):
        # Informational only: shows how far behind en.json the other files are.
        for name in self.files:
            if name == SOURCE or name in COMPLETE_LANGUAGES:
                continue
            missing = sorted(set(self.source) - set(_load(name)))
            if missing:
                warnings.warn(
                    f"{name}: {len(missing)} key(s) missing: {missing}",
                    stacklevel=1,
                )

    def test_placeholders_match_source(self):
        for name in self.files:
            translated = _load(name)
            for key, text in translated.items():
                with self.subTest(file=name, key=key):
                    self.assertEqual(
                        sorted(PLACEHOLDER.findall(text)),
                        sorted(PLACEHOLDER.findall(self.source[key])),
                    )


if __name__ == "__main__":
    unittest.main()
