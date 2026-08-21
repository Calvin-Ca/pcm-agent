"""Live, read-only performance probe for the current SSE chat protocol.

The probe intentionally uses only general chat and knowledge-base questions.  It
can bind the client socket to a physical address, which is useful on developer
machines where a Clash TUN interface otherwise captures private-network traffic.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import time
import uuid
from dataclasses import dataclass

import aiohttp


SCENARIOS = {
    "chat": "你好，请用一句话介绍你自己。",
    "rag": "工时填报规则是什么？",
}

HEADERS = {
    "Content-Type": "application/json",
    "X-User-ID": "perf-readonly-user",
    "X-Entity-Type": "employee",
    "X-Department-ID": "perf-readonly-dept",
}


@dataclass
class Result:
    scenario: str
    success: bool
    ttft: float
    total: float
    response_events: int
    response_chars: int
    error: str = ""


def percentile(values: list[float], percentile_value: int) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(percentile_value / 100 * len(ordered)) - 1)
    return ordered[index]


async def request_once(
    session: aiohttp.ClientSession,
    base_url: str,
    scenario: str,
    trace: bool = False,
) -> Result:
    started = time.perf_counter()
    first_response_at: float | None = None
    current_event = ""
    response_events = 0
    response_chars = 0
    error = ""
    saw_done = False
    reasoning_leaked = False
    payload = {
        "message": SCENARIOS[scenario],
        "session_id": f"perf-{scenario}-{uuid.uuid4().hex}",
        "stream": True,
    }

    try:
        async with session.post(
            f"{base_url.rstrip('/')}/api/ai/chat/stream",
            json=payload,
            headers=HEADERS,
        ) as response:
            if response.status != 200:
                detail = (await response.text())[:200]
                total = time.perf_counter() - started
                return Result(scenario, False, total, total, 0, 0, f"HTTP {response.status}: {detail}")

            async for raw_line in response.content:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                if line.startswith("event:"):
                    current_event = line[6:].strip()
                    continue
                if not line.startswith("data:"):
                    continue

                try:
                    data = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue

                event_type = data.get("type") or current_event
                current_event = ""
                if trace:
                    elapsed = time.perf_counter() - started
                    detail = data.get("message") or data.get("chunk") or ""
                    print(
                        f"    trace +{elapsed:.3f}s event={event_type or '-'} "
                        f"detail={str(detail)[:120]!r}",
                        flush=True,
                    )
                if event_type == "response":
                    text = (
                        data.get("chunk")
                        or data.get("message")
                        or data.get("content")
                        or data.get("result", {}).get("response", "")
                    )
                    if text:
                        if "<think" in str(text).lower():
                            reasoning_leaked = True
                        if first_response_at is None:
                            first_response_at = time.perf_counter()
                        response_events += 1
                        response_chars += len(str(text))
                elif event_type == "error":
                    error = str(data.get("message") or "SSE error")
                elif event_type == "done":
                    saw_done = True

        total = time.perf_counter() - started
        ttft = (first_response_at - started) if first_response_at else total
        if reasoning_leaked:
            error = "reasoning tag leaked"
        success = bool(first_response_at and saw_done and not error)
        if not success and not error:
            error = "missing response or done event"
        return Result(scenario, success, ttft, total, response_events, response_chars, error)
    except asyncio.TimeoutError:
        total = time.perf_counter() - started
        return Result(scenario, False, total, total, response_events, response_chars, "timeout")
    except Exception as exc:
        total = time.perf_counter() - started
        return Result(scenario, False, total, total, response_events, response_chars, str(exc)[:200])


def print_summary(label: str, results: list[Result], wall: float | None = None) -> None:
    successful = [result for result in results if result.success]
    errors = [result.error for result in results if not result.success]
    ttfts = [result.ttft for result in successful]
    totals = [result.total for result in successful]
    rps = len(successful) / wall if wall and wall > 0 else 0.0
    print(
        f"{label:<16} ok={len(successful)}/{len(results)} "
        f"ttft_p50={percentile(ttfts, 50):.3f}s "
        f"ttft_p95={percentile(ttfts, 95):.3f}s "
        f"e2e_p50={percentile(totals, 50):.3f}s "
        f"e2e_p95={percentile(totals, 95):.3f}s "
        f"rps={rps:.3f}"
    )
    if successful:
        print(
            f"{'':16} ttft_mean={statistics.mean(ttfts):.3f}s "
            f"e2e_mean={statistics.mean(totals):.3f}s"
        )
    if errors:
        print(f"{'':16} errors={errors}")


async def run(args: argparse.Namespace) -> int:
    local_addr = (args.bind, 0) if args.bind else None
    connector = aiohttp.TCPConnector(
        limit=max(args.concurrency, default=1) + 4,
        local_addr=local_addr,
    )
    timeout = aiohttp.ClientTimeout(total=args.timeout)
    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        print(f"base_url={args.base_url} bind={args.bind or 'default'} timeout={args.timeout}s")
        for scenario in args.scenarios:
            results = []
            for _ in range(args.repeats):
                result = await request_once(
                    session,
                    args.base_url,
                    scenario,
                    trace=args.trace,
                )
                results.append(result)
                print(
                    f"  {scenario:<5} ok={result.success} ttft={result.ttft:.3f}s "
                    f"e2e={result.total:.3f}s events={result.response_events} "
                    f"chars={result.response_chars} error={result.error or '-'}"
                )
            print_summary(f"baseline:{scenario}", results)

        for concurrency in args.concurrency:
            started = time.perf_counter()
            results = await asyncio.gather(
                *[
                    request_once(session, args.base_url, args.concurrency_scenario)
                    for _ in range(concurrency)
                ]
            )
            wall = time.perf_counter() - started
            print_summary(
                f"{args.concurrency_scenario}:{concurrency}", results, wall
            )

    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--bind", default="")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--scenarios", default="chat,rag")
    parser.add_argument("--concurrency", default="1,2,4,8")
    parser.add_argument(
        "--concurrency-scenario",
        choices=sorted(SCENARIOS),
        default="chat",
    )
    parser.add_argument("--skip-concurrency", action="store_true")
    parser.add_argument("--trace", action="store_true")
    args = parser.parse_args()
    args.scenarios = [item.strip() for item in args.scenarios.split(",") if item.strip()]
    unknown = sorted(set(args.scenarios) - set(SCENARIOS))
    if unknown:
        parser.error(f"unknown scenarios: {unknown}")
    args.concurrency = (
        []
        if args.skip_concurrency
        else [int(item) for item in args.concurrency.split(",") if item.strip()]
    )
    return args


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(parse_args())))
