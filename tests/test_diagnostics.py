"""Diagnostics must not leak credentials, account ids or network identifiers."""

# ruff: noqa: D102, INP001, PT009

from __future__ import annotations

import base64
import json
import unittest

from custom_components.eufy_sdk.diagnostics import REDACTED, _redact


def _b64_json(obj: object) -> str:
    return base64.b64encode(json.dumps(obj).encode()).decode()


class RedactionTests(unittest.TestCase):
    """The value-level redactor over a HomeBase-shaped record (issue #194)."""

    def test_credentials_blob_is_redacted_whole(self) -> None:
        # nasSendSecurityPasswd decodes to {"mode","passwd","username"} — the key is
        # credential-shaped, so the whole value goes, never decoded.
        creds = _b64_json({"mode": 2, "passwd": "hunter2", "username": "admin"})
        out = _redact({"state": {"nasSendSecurityPasswd": creds}})
        self.assertEqual(out["state"]["nasSendSecurityPasswd"], REDACTED)

    def test_account_id_is_redacted_at_any_depth(self) -> None:
        out = _redact(
            {
                "homeActionTable": {
                    "account_id": "ACC-1",
                    "devices": [{"sn": "T8114"}],
                },
                "awayActionTable": {"account_id": "ACC-1"},
                "hbWifi": {"account_id": "ACC-1"},
            }
        )
        self.assertEqual(out["homeActionTable"]["account_id"], REDACTED)
        self.assertEqual(out["awayActionTable"]["account_id"], REDACTED)
        self.assertEqual(out["hbWifi"]["account_id"], REDACTED)
        self.assertEqual(out["homeActionTable"]["devices"][0]["sn"], REDACTED)

    def test_base64_json_blob_is_redacted_inside_and_rewrapped(self) -> None:
        # unknown_1160 is base64 JSON carrying an account id; the account id must go
        # while the rest of the blob survives.
        blob = _b64_json({"account_id": "ACC-1", "other": "keep"})
        out = _redact({"state": {"unknown_1160": blob}})
        decoded = json.loads(base64.b64decode(out["state"]["unknown_1160"]))
        self.assertEqual(decoded, {"account_id": REDACTED, "other": "keep"})

    def test_network_identifiers_are_redacted(self) -> None:
        out = _redact(
            {
                "hbWifi": {"ssid": "MyWiFi", "bssid": "AA:BB:CC:DD:EE:FF"},
                "state": {"ipAddress": "192.168.1.50"},
            }
        )
        self.assertEqual(out["hbWifi"]["ssid"], REDACTED)
        self.assertEqual(out["hbWifi"]["bssid"], REDACTED)
        self.assertEqual(out["state"]["ipAddress"], REDACTED)

    def test_key_matching_is_case_insensitive(self) -> None:
        out = _redact({"SN": "x", "Name": "y", "Account_Id": "z", "accountName": "w"})
        self.assertEqual(set(out.values()), {REDACTED})

    def test_diagnostic_substance_and_opaque_blobs_survive(self) -> None:
        plain_blob = _b64_json({"brightness": 90, "mode": "auto"})
        out = _redact(
            {
                "model": "T8010",
                "codec": "station",
                "capabilities": ["arming"],
                "state": {
                    "armingMode": 0,
                    "deviceStatus": True,
                    "thumbnail": plain_blob,
                },
            }
        )
        self.assertEqual(out["model"], "T8010")
        self.assertEqual(out["codec"], "station")
        self.assertEqual(out["capabilities"], ["arming"])
        # Live state is kept as-is — including a bool, and a base64 blob with nothing
        # sensitive (left byte-for-byte rather than re-wrapped).
        self.assertEqual(
            out["state"],
            {"armingMode": 0, "deviceStatus": True, "thumbnail": plain_blob},
        )

    def test_non_base64_strings_are_left_alone(self) -> None:
        out = _redact({"note": "just a plain string, not base64 at all!!"})
        self.assertEqual(out["note"], "just a plain string, not base64 at all!!")


if __name__ == "__main__":
    unittest.main()
