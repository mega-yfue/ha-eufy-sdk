# ruff: noqa: ANN201, ANN202, D100, D101, D102, INP001, PT009, SLF001

import unittest
from unittest.mock import AsyncMock, Mock

from custom_components.eufy_sdk import config_flow


class CaptchaStepTests(unittest.IsolatedAsyncioTestCase):
    def _flow(self, after_submit: str):
        flow = config_flow.EufySdkFlowHandler()
        flow.hass = Mock()
        flow.context = {}
        client = Mock()
        client.submit_captcha = AsyncMock(return_value={"state": after_submit})
        client.auth_status = AsyncMock(
            return_value={"state": after_submit, "image": "data:image/png;base64,AA"}
        )
        flow._client = client
        flow.async_show_form = Mock(side_effect=lambda **kw: kw)
        return flow

    async def test_a_correct_captcha_that_leads_to_2fa_asks_for_the_code(self):
        # ha-eufy-sdk#49: eufy accepts the captcha, then emails a 2FA code.
        flow = self._flow("require_2fa")
        result = await flow.async_step_captcha({"answer": "AB12"})
        self.assertEqual(result["step_id"], "twofa")
        self.assertEqual(result["errors"], {})

    async def test_another_captcha_means_the_answer_was_wrong(self):
        flow = self._flow("require_captcha")
        result = await flow.async_step_captcha({"answer": "nope"})
        self.assertEqual(result["step_id"], "captcha")
        self.assertEqual(result["errors"], {"base": "invalid_captcha"})


if __name__ == "__main__":
    unittest.main()
