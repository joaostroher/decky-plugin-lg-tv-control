import json
import unittest
from unittest.mock import AsyncMock
from backend.tv_control import Session, TVError


class TVErrorTests(unittest.IsolatedAsyncioTestCase):
    async def test_hdmi_permission_failure_is_actionable(self):
        socket = AsyncMock()
        socket.recv.return_value = json.dumps({"id": "1", "type": "error", "error": "401 insufficient permissions"})
        with self.assertRaises(TVError) as raised:
            await Session(socket).select_input("HDMI_1")
        self.assertEqual(raised.exception.code, 401)
        self.assertIn("HDMI switching", str(raised.exception))

    async def test_unknown_error_does_not_expose_raw_payload(self):
        socket = AsyncMock()
        socket.recv.return_value = json.dumps({"id": "1", "type": "error", "error": "private-key-and-payload"})
        with self.assertRaises(TVError) as raised:
            await Session(socket).select_input("HDMI_1")
        self.assertNotIn("private-key", str(raised.exception))
