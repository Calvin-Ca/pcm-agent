"""Local SpringBoot-compatible backend for manual Agent development.

The mock deliberately implements only the HTTP contracts consumed by the
FastAPI tools.  It never connects to production services or databases.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any

import uvicorn
from fastapi import Body, FastAPI, Query, Request
from fastapi.responses import JSONResponse, Response


EMPLOYEE_ID = "d1e88d66-cc87-40c7-bbe3-2dff2d093b41"
ADMIN_ID = "4cbabf4b-6ba2-4b12-aacc-15077187f47a"
SUPER_ID = "5565b9e2-1348-4c4b-b7f2-386e67a3c02b"

USERS = [
    {
        "id": EMPLOYEE_ID,
        "entityId": "0103163734221037995",
        "entityName": "罗欢",
        "entityType": "employee",
        "login": "mock.employee",
    },
    {
        "id": ADMIN_ID,
        "entityId": "020832615020860355",
        "entityName": "刘会超",
        "entityType": "deptAdmin",
        "login": "mock.dept-admin",
    },
    {
        "id": SUPER_ID,
        "entityId": "123",
        "entityName": "管理员",
        "entityType": "superAdmin",
        "login": "mock.super-admin",
    },
]

PROJECTS = [
    {
        "id": "1001",
        "projectName": "AI平台",
        "name": "AI平台",
        "description": "AI 助手与工时 Agent 开发",
        "status": "进行中",
        "startDate": "2026-01-01",
        "endDate": None,
        "progress": 68,
        "managerId": ADMIN_ID,
        "managerName": "刘会超",
    },
    {
        "id": "1002",
        "projectName": "智慧园区",
        "name": "智慧园区",
        "description": "园区数字化平台",
        "status": "进行中",
        "startDate": "2026-02-01",
        "endDate": None,
        "progress": 42,
        "managerId": ADMIN_ID,
        "managerName": "刘会超",
    },
    {
        "id": "1003",
        "projectName": "内部培训",
        "name": "内部培训",
        "description": "团队培训与技术分享",
        "status": "进行中",
        "startDate": "2026-01-01",
        "endDate": None,
        "progress": 25,
        "managerId": SUPER_ID,
        "managerName": "管理员",
    },
]

VALID_SCENARIOS = {"normal", "empty", "unauthorized", "forbidden", "server_error", "slow"}


def _iso(day: date) -> str:
    return f"{day.isoformat()}T00:00:00Z"


def _initial_workhours() -> list[dict[str, Any]]:
    """Build useful records around the current week so natural-date probes work."""
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    previous_monday = monday - timedelta(days=7)
    rows = [
        ("wh-001", EMPLOYEE_ID, "1001", monday, 8.0, "Agent 工具联调"),
        ("wh-002", EMPLOYEE_ID, "1001", monday + timedelta(days=1), 7.5, "完善参数解析"),
        ("wh-003", EMPLOYEE_ID, "1002", monday + timedelta(days=2), 6.0, "项目接口联调"),
        ("wh-004", EMPLOYEE_ID, "1003", previous_monday + timedelta(days=1), 4.0, "技术培训"),
        ("wh-005", ADMIN_ID, "1001", monday, 8.0, "方案评审"),
        ("wh-006", ADMIN_ID, "1002", monday + timedelta(days=1), 8.0, "项目管理"),
        ("wh-007", SUPER_ID, "1003", monday, 3.0, "管理例会"),
    ]
    by_project = {project["id"]: project["projectName"] for project in PROJECTS}
    return [
        {
            "id": record_id,
            "memberId": member_id,
            "projectId": project_id,
            "projectName": by_project[project_id],
            "workhourDate": _iso(day),
            "workhour": hours,
            "description": description,
            "workContent": description,
            "workType": "研发",
            "workhourType": "正常工时",
            "createdAt": _iso(day),
            "approved": False,
        }
        for record_id, member_id, project_id, day, hours, description in rows
    ]


def _b64url(value: dict[str, Any]) -> str:
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def make_mock_token(user: dict[str, Any]) -> str:
    now = int(datetime.now(timezone.utc).timestamp())
    header = _b64url({"alg": "none", "typ": "JWT"})
    payload = _b64url(
        {
            "sub": user["id"],
            "entity_type": user["entityType"],
            "iat": now,
            "exp": now + 24 * 60 * 60,
            "iss": "workhour-local-mock",
        }
    )
    return f"{header}.{payload}.mock"


class MockState:
    def __init__(self) -> None:
        self.scenario = os.getenv("WORKHOUR_MOCK_SCENARIO", "normal")
        if self.scenario not in VALID_SCENARIOS:
            self.scenario = "normal"
        self.reset()

    def reset(self) -> None:
        self.workhours = _initial_workhours()


state = MockState()
app = FastAPI(title="Workhour SpringBoot Mock", version="1.0.0")


@app.middleware("http")
async def apply_scenario(request: Request, call_next):
    if request.url.path.startswith("/__mock__") or request.url.path in {
        "/api/auth/mcp-token",
        "/api/authenticate",
    }:
        return await call_next(request)

    if state.scenario == "slow":
        import asyncio

        await asyncio.sleep(2)
    elif state.scenario == "unauthorized":
        return JSONResponse({"title": "Mock token expired"}, status_code=401)
    elif state.scenario == "forbidden":
        return JSONResponse({"title": "Mock permission denied"}, status_code=403)
    elif state.scenario == "server_error":
        return JSONResponse({"title": "Mock backend failure"}, status_code=500)
    return await call_next(request)


@app.get("/__mock__/health")
async def mock_health() -> dict[str, Any]:
    return {
        "ok": True,
        "service": "workhour-springboot-mock",
        "scenario": state.scenario,
        "records": len(state.workhours),
    }


@app.post("/__mock__/reset")
async def mock_reset() -> dict[str, Any]:
    state.reset()
    state.scenario = "normal"
    return {"ok": True, "records": len(state.workhours), "scenario": state.scenario}


@app.post("/__mock__/scenario/{scenario}", response_model=None)
async def mock_scenario(scenario: str) -> dict[str, Any] | JSONResponse:
    if scenario not in VALID_SCENARIOS:
        return JSONResponse(
            {"error": f"unknown scenario: {scenario}", "valid": sorted(VALID_SCENARIOS)},
            status_code=400,
        )
    state.scenario = scenario
    return {"ok": True, "scenario": state.scenario}


def _user_for_entity(entity_id: str | None) -> dict[str, Any]:
    return next((user for user in USERS if user["entityId"] == entity_id), USERS[0])


@app.post("/api/auth/mcp-token")
async def mcp_token(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    user = _user_for_entity(str(payload.get("entity_id", "")))
    return {
        "token": make_mock_token(user),
        "userId": user["id"],
        "entityType": user["entityType"],
    }


@app.post("/api/authenticate")
async def authenticate() -> dict[str, Any]:
    user = USERS[0]
    return {"data": {"token": make_mock_token(user), "userId": user["id"]}}


@app.get("/thsuaa/api/sys-users")
async def query_users(request: Request) -> list[dict[str, Any]]:
    if state.scenario == "empty":
        return []
    name = request.query_params.get("entityName.contains", "").casefold()
    user_id = request.query_params.get("id.equals", "")
    rows = USERS
    if name:
        rows = [user for user in rows if name in user["entityName"].casefold()]
    if user_id:
        rows = [user for user in rows if user["id"] == user_id]
    return rows


@app.get("/api/project-infos")
async def query_projects(request: Request) -> list[dict[str, Any]]:
    if state.scenario == "empty":
        return []
    name = request.query_params.get("projectName.contains", "").casefold()
    rows = PROJECTS
    if name:
        rows = [project for project in rows if name in project["projectName"].casefold()]
    return rows


@app.get("/api/project-infos/{project_id}", response_model=None)
async def get_project(project_id: str) -> dict[str, Any] | JSONResponse:
    project = next((item for item in PROJECTS if item["id"] == project_id), None)
    if project is None or state.scenario == "empty":
        return JSONResponse({"success": False, "message": "项目不存在"}, status_code=404)
    members = [
        {
            "userId": user["id"],
            "userName": user["entityName"],
            "role": "负责人" if user["id"] == project["managerId"] else "成员",
            "joinDate": project["startDate"],
        }
        for user in USERS[:2]
    ]
    detail = {**project, "members": members}
    return {"success": True, "data": detail}


@app.get("/api/workhour/by-date-range")
async def query_workhours(
    start_date: str | None = Query(default=None, alias="startDate"),
    end_date: str | None = Query(default=None, alias="endDate"),
    member_id: str | None = Query(default=None, alias="memberId"),
    project_id: str | None = Query(default=None, alias="projectId"),
) -> list[dict[str, Any]]:
    if state.scenario == "empty":
        return []

    def included(record: dict[str, Any]) -> bool:
        day = str(record["workhourDate"])[:10]
        return not (
            (start_date and day < start_date[:10])
            or (end_date and day > end_date[:10])
            or (member_id and record["memberId"] != member_id)
            or (project_id and record["projectId"] != project_id)
        )

    return [record for record in state.workhours if included(record)]


@app.get("/api/work-calendars/list")
async def work_calendar(request: Request) -> list[dict[str, Any]]:
    raw = request.query_params.get("dateValue.greaterThanOrEqual", date.today().isoformat())
    try:
        day = date.fromisoformat(raw[:10])
    except ValueError:
        day = date.today()
    return [
        {
            "id": f"calendar-{day.isoformat()}",
            "dateValue": _iso(day),
            "isWorkDay": "1" if day.weekday() < 5 else "0",
            "isDeleted": "0",
        }
    ]


@app.post("/api/workhour")
async def save_workhour(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    record_id = f"mock-wh-{len(state.workhours) + 1:04d}"
    project_id = str(payload.get("projectId", ""))
    project = next((item for item in PROJECTS if item["id"] == project_id), None)
    record = {
        "id": record_id,
        "memberId": str(payload.get("memberId") or EMPLOYEE_ID),
        "projectId": project_id,
        "projectName": project["projectName"] if project else "未知项目",
        "workhourDate": str(payload.get("workhourDate", _iso(date.today()))),
        "workhour": float(payload.get("workhour", 0)),
        "description": str(payload.get("workContent", "")),
        "workContent": str(payload.get("workContent", "")),
        "workType": str(payload.get("workType", "研发")),
        "workhourType": str(payload.get("workhourType", "正常工时")),
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "approved": False,
    }
    state.workhours.append(record)
    return {"success": True, "data": record}


@app.post("/api/workhour/batch-approve")
async def batch_approve(workhour_ids: list[str] = Body(...)) -> dict[str, Any]:
    wanted = set(workhour_ids)
    approved = 0
    for record in state.workhours:
        if record["id"] in wanted:
            record["approved"] = True
            approved += 1
    return {"success": True, "approvedCount": approved}


@app.get("/api/workhour/export/project-simple")
async def export_report() -> Response:
    content = "mock workhour export\nproject,hours\nAI平台,21.5\n".encode()
    return Response(
        content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="mock-workhours.xlsx"'},
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local SpringBoot mock")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9900)
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
