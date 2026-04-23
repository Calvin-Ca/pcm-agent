"""
Test user identity resolution in chat endpoints.

Covers: body.user_context priority > header fallback > anonymous warning
"""

import pytest
from unittest.mock import MagicMock, patch

from app.api.chat import _resolve_user_identity, ChatRequest


class TestResolveUserIdentity:
    """Test _resolve_user_identity priority logic."""

    def _make_request(self, user_context=None, session_id="test-session"):
        """Helper to build ChatRequest."""
        return ChatRequest(
            message="hello",
            session_id=session_id,
            user_context=user_context,
        )

    def _make_http_request(self, headers=None):
        """Helper to build mock HTTP Request."""
        req = MagicMock()
        req.headers = headers or {}
        return req

    # ── Priority 1: body.user_context ───────────────────────────────────────

    def test_read_user_id_from_body(self):
        chat_req = self._make_request(user_context={"user_id": "alice"})
        http_req = self._make_http_request()

        uid, etype, dept, token, ctx = _resolve_user_identity(chat_req, http_req)

        assert uid == "alice"
        assert ctx["user_id"] == "alice"

    def test_read_all_fields_from_body(self):
        chat_req = self._make_request(user_context={
            "user_id": "alice",
            "entity_type": "employee",
            "department_id": "dept-1",
            "auth_token": "token-abc",
        })
        http_req = self._make_http_request()

        uid, etype, dept, token, ctx = _resolve_user_identity(chat_req, http_req)

        assert uid == "alice"
        assert etype == "employee"
        assert dept == "dept-1"
        assert token == "token-abc"
        assert ctx["user_id"] == "alice"
        assert ctx["entity_type"] == "employee"
        assert ctx["department_id"] == "dept-1"
        assert ctx["auth_token"] == "token-abc"

    # ── Priority 2: header fallback ─────────────────────────────────────────

    def test_fallback_to_header_when_body_missing(self):
        chat_req = self._make_request(user_context={})
        http_req = self._make_http_request(headers={"X-User-ID": "bob"})

        uid, etype, dept, token, ctx = _resolve_user_identity(chat_req, http_req)

        assert uid == "bob"
        assert ctx["user_id"] == "bob"

    def test_header_fallback_for_all_fields(self):
        chat_req = self._make_request(user_context={})
        http_req = self._make_http_request(headers={
            "X-User-ID": "bob",
            "X-Entity-Type": "admin",
            "X-Department-ID": "dept-2",
            "Authorization": "Bearer token-xyz",
        })

        uid, etype, dept, token, ctx = _resolve_user_identity(chat_req, http_req)

        assert uid == "bob"
        assert etype == "admin"
        assert dept == "dept-2"
        assert token == "Bearer token-xyz"

    # ── Priority 3: body wins over header ───────────────────────────────────

    def test_body_wins_over_header(self):
        chat_req = self._make_request(user_context={"user_id": "alice"})
        http_req = self._make_http_request(headers={"X-User-ID": "bob"})

        uid, _, _, _, _ = _resolve_user_identity(chat_req, http_req)

        assert uid == "alice"  # body priority

    # ── Priority 4: anonymous fallback + warning log ────────────────────────

    def test_fallback_to_anonymous(self):
        chat_req = self._make_request(user_context=None)
        http_req = self._make_http_request(headers={})

        uid, etype, dept, token, ctx = _resolve_user_identity(chat_req, http_req)

        assert uid == "anonymous"
        assert ctx["user_id"] == "anonymous"

    def test_fallback_to_anonymous_logs_warning(self):
        chat_req = self._make_request(user_context=None, session_id="sess-123")
        http_req = self._make_http_request(headers={})

        with patch("app.api.chat.logger") as mock_logger:
            _resolve_user_identity(chat_req, http_req)

            mock_logger.warning.assert_called_once()
            log_msg = mock_logger.warning.call_args[0][0]
            assert "anonymous" in log_msg
            assert "sess-123" in log_msg  # session_id in log
            assert "body.user_id=None" in log_msg
            assert "header.X-User-ID=None" in log_msg

    # ── Edge cases ──────────────────────────────────────────────────────────

    def test_empty_string_body_user_id_treated_as_falsey(self):
        """空字符串被视为 falsy，会走 header fallback。"""
        chat_req = self._make_request(user_context={"user_id": ""})
        http_req = self._make_http_request(headers={"X-User-ID": "bob"})

        uid, _, _, _, _ = _resolve_user_identity(chat_req, http_req)

        assert uid == "bob"

    def test_none_body_user_id_treated_as_missing(self):
        chat_req = self._make_request(user_context={"user_id": None})
        http_req = self._make_http_request(headers={"X-User-ID": "bob"})

        uid, _, _, _, _ = _resolve_user_identity(chat_req, http_req)

        assert uid == "bob"

    def test_partial_body_with_header_complement(self):
        """body 有部分字段，剩余从 header 补。"""
        chat_req = self._make_request(user_context={
            "user_id": "alice",
            # entity_type missing
            "department_id": "dept-1",
        })
        http_req = self._make_http_request(headers={
            "X-Entity-Type": "employee",
        })

        uid, etype, dept, _, ctx = _resolve_user_identity(chat_req, http_req)

        assert uid == "alice"
        assert etype == "employee"  # from header
        assert dept == "dept-1"      # from body
        assert ctx["entity_type"] == "employee"
