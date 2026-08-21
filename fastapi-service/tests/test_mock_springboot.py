"""Contract tests for the manual SpringBoot mock server."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.tools import query_timesheet


MODULE_PATH = Path(__file__).parent / "manual" / "mock_springboot.py"
SPEC = importlib.util.spec_from_file_location("manual_mock_springboot", MODULE_PATH)
assert SPEC and SPEC.loader
mock_backend = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = mock_backend
SPEC.loader.exec_module(mock_backend)


@pytest.fixture(autouse=True)
def reset_mock_state():
    mock_backend.state.reset()
    mock_backend.state.scenario = "normal"
    yield
    mock_backend.state.reset()
    mock_backend.state.scenario = "normal"


@pytest.fixture
def client():
    with TestClient(mock_backend.app) as value:
        yield value


def test_health_identifies_mock(client: TestClient):
    response = client.get("/__mock__/health")

    assert response.status_code == 200
    assert response.json()["service"] == "workhour-springboot-mock"


def test_workhours_support_date_and_member_filters(client: TestClient):
    monday = date.today() - timedelta(days=date.today().weekday())
    response = client.get(
        "/api/workhour/by-date-range",
        params={
            "startDate": monday.isoformat(),
            "endDate": (monday + timedelta(days=6)).isoformat(),
            "memberId": mock_backend.EMPLOYEE_ID,
        },
    )

    assert response.status_code == 200
    rows = response.json()
    assert rows
    assert {row["memberId"] for row in rows} == {mock_backend.EMPLOYEE_ID}
    assert all(monday.isoformat() <= row["workhourDate"][:10] for row in rows)


def test_project_list_and_detail_match_tool_contracts(client: TestClient):
    listing = client.get(
        "/api/project-infos", params={"projectName.contains": "AI平台"}
    )
    detail = client.get("/api/project-infos/1001")

    assert listing.json()[0]["projectName"] == "AI平台"
    assert detail.json()["success"] is True
    assert detail.json()["data"]["name"] == "AI平台"
    assert detail.json()["data"]["members"]


def test_saved_workhour_is_visible_to_followup_query(client: TestClient):
    payload = {
        "memberId": mock_backend.EMPLOYEE_ID,
        "projectId": "1002",
        "workhourDate": f"{date.today().isoformat()}T00:00:00Z",
        "workhour": 2.5,
        "workContent": "Mock 写入验证",
    }
    saved = client.post("/api/workhour", json=payload)
    queried = client.get(
        "/api/workhour/by-date-range",
        params={
            "startDate": date.today().isoformat(),
            "endDate": date.today().isoformat(),
            "memberId": mock_backend.EMPLOYEE_ID,
            "projectId": "1002",
        },
    )

    assert saved.status_code == 200
    assert saved.json()["data"]["id"].startswith("mock-wh-")
    assert any(row["description"] == "Mock 写入验证" for row in queried.json())


def test_error_scenario_is_switchable_at_runtime(client: TestClient):
    selected = client.post("/__mock__/scenario/forbidden")
    denied = client.get("/api/project-infos")
    health = client.get("/__mock__/health")

    assert selected.status_code == 200
    assert denied.status_code == 403
    assert health.json()["scenario"] == "forbidden"


def test_real_timesheet_tool_can_consume_mock_contract(monkeypatch):
    real_async_client = httpx.AsyncClient
    transport = httpx.ASGITransport(app=mock_backend.app)

    def mock_client(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monday = date.today() - timedelta(days=date.today().weekday())
    monkeypatch.setattr(query_timesheet.httpx, "AsyncClient", mock_client)
    monkeypatch.setenv("SPRINGBOOT_BASE_URL", "http://mock")

    result = asyncio.run(
        query_timesheet.query_timesheet_handler(
            start_date=monday.isoformat(),
            end_date=(monday + timedelta(days=6)).isoformat(),
            user_id=mock_backend.EMPLOYEE_ID,
            auth_token="mock-token",
        )
    )

    assert result["success"] is True
    assert result["record_count"] == 3
    assert result["total_hours"] == 21.5
