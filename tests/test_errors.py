# ruff: noqa: ANN201, D100, D101, D102, INP001, PT009

import unittest

from homeassistant.exceptions import HomeAssistantError

from custom_components.eufy_sdk.api import (
    EufySdkApiClientAuthenticationError,
    EufySdkApiClientCommunicationError,
    EufySdkApiClientError,
)


class ErrorTypeTests(unittest.TestCase):
    def test_bridge_errors_are_home_assistant_errors(self):
        # continue_on_error steps past a HomeAssistantError only.
        for err in (
            EufySdkApiClientError("x"),
            EufySdkApiClientCommunicationError("x"),
            EufySdkApiClientAuthenticationError("x"),
        ):
            self.assertIsInstance(err, HomeAssistantError)


if __name__ == "__main__":
    unittest.main()
