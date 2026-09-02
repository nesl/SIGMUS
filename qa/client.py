"""LLM question-answering client backed exclusively by SIGMUS MCP tools."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from openai import OpenAI

from utilities.util import get_config


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
include pm2.5_atm, humidity, temperature, wind_speed, and wind_direction; aliases are accepted."""


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
    if openai_client is None:
        openai_config = get_config()["openai"]
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
                {"role": "system", "content": SYSTEM_PROMPT},
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
