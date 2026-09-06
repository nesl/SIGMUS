"""LLM question-answering client backed exclusively by SIGMUS MCP tools."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
import sys
from typing import Any
from zoneinfo import ZoneInfo

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from openai import OpenAI

from database_storage.config import get_config


SYSTEM_PROMPT = """You answer questions using the SIGMUS urban-observation evidence tools.
Use tools before making factual claims about stored observations. Use Neo4j tools for incidents,
actors, reports, and relationships; use measurement tools for numeric time-series questions.
For combined questions, query both. Cite stable observation IDs in the answer. Clearly say when
evidence is absent or incomplete. Never invent observations, measurements, or relationships.
Begin report discovery with broad filters or incident search; do not combine event_type and text
unless necessary. After finding an incident with observation IDs, call find_related_reports for
one representative ID rather than every member unless results are incomplete. Use get_report only
when the summary lacks required detail. A measurement changing near an event establishes temporal
association, not causation; never say an event caused a change unless a tool returns explicit
causal evidence. To describe a measurement change, retrieve at least two relevant points including
an earlier baseline; if only one point is available, report its value without claiming a change.
Measurement query start and end timestamps are inclusive.
Never replace missing report summaries or timestamps with plausible-sounding text. Canonical
sources are air, alertcalifornia, cctv, citizen, gdelt, pems, twitter, and weather. Common fields
include pm2.5_atm, humidity, temperature, wind_speed, and wind_direction; aliases are accepted.
search_reports is ordered newest-first; use it with limit=1 and no filters for the most recent
report. When reporting time, use time_local and state its timezone while retaining time_utc as the
canonical instant. Use anomalous=true to discover reports explicitly marked anomalous. When that
search returns results, you must call get_report for every selected anomalous report (up to 10)
before answering, and use its full Data annotations for diagnostics. For questions about the complete set
or whether multiple sources are represented, call search_reports with limit=100 first; do not
generalize from one latest report. Investigate cross-source connections with find_related_reports
using different_source=true
for relevant representatives returned by that broad search. Treat llm_corroboration as a proposed
evidence link, not a confirmed incident; explain its reason, confidence, distance, and time delta.
Only shared_confirmed_incident means that reports share a confirmed incident, and only news reports
can establish those Incident nodes. Sensor anomaly and incident-candidate labels are screening
signals, not true incidents; report candidate_incidents using that exact qualification. Whenever answering with information
about a report, include its location, configured-local and UTC times, and originating filepath when
those values are available; explicitly say which values are unavailable rather than inventing them."""


def _system_prompt(*, now: datetime | None = None, timezone_name: str) -> str:
    """Attach an authoritative request-time clock to the stable Q/A instructions."""
    instant = now or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    utc_now = instant.astimezone(timezone.utc)
    local_now = utc_now.astimezone(ZoneInfo(timezone_name))
    return SYSTEM_PROMPT + f"""

Authoritative clock for this request:
- Current UTC time: {utc_now.isoformat()}
- Current configured-local time: {local_now.isoformat()}
- Configured local timezone: {timezone_name}

Use this clock whenever the user gives a relative time such as "now", "today", "this morning",
"tonight", "yesterday", or "recently". Derive explicit ISO-8601 start and end timestamps in the
configured local timezone before calling a time-filtered tool; include UTC offsets so the database
receives unambiguous instants. Interpret "this morning" as local midnight through local noon,
capped at the current local time if noon has not yet occurred. Never substitute a remembered,
training-era, or guessed date for the authoritative clock above."""


def _tool_result(result: Any) -> str:
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return json.dumps(structured, default=str)
    values = []
    for block in getattr(result, "content", []):
        text = getattr(block, "text", None)
        if text is not None:
            values.append(text)
    return "\n".join(values)


async def answer(question: str, *, mcp_url: str, model: str | None = None,
                 max_rounds: int = 8, openai_client=None, verbose: bool = False) -> str:
    config = get_config()
    if openai_client is None:
        openai_config = config["openai"]
        client = OpenAI(api_key=openai_config["api"])
        model = model or openai_config.get("qa_model") or openai_config.get("model", "gpt-4o-mini")
    else:
        client = openai_client
        model = model or "test-model"

    async with streamable_http_client(mcp_url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listed = await session.list_tools()
            tools = [{
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description or "",
                    "parameters": tool.inputSchema,
                },
            } for tool in listed.tools]
            messages: list[dict] = [
                {"role": "system", "content": _system_prompt(
                    timezone_name=config.get("timezone", "America/Los_Angeles")
                )},
                {"role": "user", "content": question},
            ]
            for _ in range(max_rounds):
                response = client.chat.completions.create(
                    model=model, messages=messages, tools=tools, tool_choice="auto", temperature=0
                )
                message = response.choices[0].message
                messages.append(message.model_dump(exclude_none=True))
                if not message.tool_calls:
                    return message.content or "No answer was produced."
                for call in message.tool_calls:
                    try:
                        arguments = json.loads(call.function.arguments or "{}")
                        if verbose:
                            print(f"MCP call {call.function.name}: {json.dumps(arguments)}", file=sys.stderr)
                        result = await session.call_tool(call.function.name, arguments)
                        content = _tool_result(result)
                    except Exception as exc:
                        content = json.dumps({"error": str(exc)})
                    if verbose:
                        print(f"MCP result {call.function.name}: {content[:4000]}", file=sys.stderr)
                    messages.append({"role": "tool", "tool_call_id": call.id, "content": content})
    raise RuntimeError(f"Q/A exceeded the {max_rounds}-round tool-call limit")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Ask SIGMUS an evidence-backed question")
    parser.add_argument("question")
    parser.add_argument("--mcp-url", default=os.environ.get("SIGMUS_MCP_URL", "http://127.0.0.1:8006/mcp"))
    parser.add_argument("--model")
    parser.add_argument("--verbose", action="store_true", help="log MCP calls and bounded results")
    args = parser.parse_args(argv)
    print(asyncio.run(answer(args.question, mcp_url=args.mcp_url, model=args.model,
                             verbose=args.verbose)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
