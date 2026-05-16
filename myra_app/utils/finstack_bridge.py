import asyncio
import json
import logging
import concurrent.futures
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

logger = logging.getLogger(__name__)

_SERVER_PARAMS = StdioServerParameters(
    command="python",
    args=["-m", "finstack.server"],
)

_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)


def _run_mcp_sync(tool_name: str, arguments: dict = None) -> dict:
    async def _call():
        async with stdio_client(_SERVER_PARAMS) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(
                    tool_name, arguments=arguments or {}
                )
                raw = result.content[0].text
                try:
                    return json.loads(raw)
                except json.JSONDecodeError:
                    return {"_raw": raw}
    return asyncio.run(_call())


async def _call_tool_async(tool_name: str, arguments: dict = None) -> dict:
    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(
            _executor, lambda: _run_mcp_sync(tool_name, arguments)
        )
    except Exception as e:
        logger.error(f"FinStack MCP call '{tool_name}' failed: {e}")
        return {"error": str(e)}


async def get_stock_brief(symbol: str) -> dict:
    return await _call_tool_async("get_stock_brief", {"symbol": symbol.upper()})


async def get_morning_brief() -> dict:
    return await _call_tool_async("get_morning_brief")


async def get_social_sentiment(symbol: str) -> dict:
    return await _call_tool_async("get_social_sentiment", {"symbol": symbol.upper()})


async def get_stock_timeline(symbol: str) -> dict:
    return await _call_tool_async("get_stock_timeline", {"symbol": symbol.upper()})


async def get_pledge_alert(symbol: str) -> dict:
    return await _call_tool_async("get_pledge_alert", {"symbol": symbol.upper()})


async def scan_pledge_risks() -> dict:
    return await _call_tool_async("scan_pledge_risks")