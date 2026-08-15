"""
    web_fetch.py
    ~~~~~~~~~~~~~~~~~~~~~~~
    Web fetch tool that retrieves content from a URL.
"""
from __future__ import annotations

import asyncio
import aiohttp

from langchain.tools import tool

from app.utils.log import logger


@tool
async def web_fetch(url: str, timeout: int = 30) -> str:
    """Fetch content from a web URL. Use this when you need to retrieve the content
    of a specific webpage or resource by its URL.
    
    Args:
        url: The URL to fetch content from
        timeout: Maximum time in seconds to wait for the request (default: 30)
    """
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=timeout) as response:
                if response.status == 200:
                    content = await response.text()
                    # Limit content to prevent excessive output
                    if len(content) > 5000:
                        content = content[:5000] + "... [content truncated]"
                    return f"Successfully fetched {len(content)} characters from {url}:\n\n{content}"
                else:
                    return f"Failed to fetch {url}: HTTP {response.status} {response.reason}"
    except asyncio.TimeoutError:
        return f"Timeout error while fetching {url} (timeout: {timeout}s)"
    except aiohttp.ClientError as e:
        return f"Network error while fetching {url}: {e}"
    except Exception as e:
        logger.error("Web fetch failed for url='%s': %s", url, e, exc_info=True)
        return f"Fetch failed: {e}"
