import json
import time

from app.browser.actions import BrowserStop


ENDPOINT = 'https://mcp.oxylabs.io/web-api-mcp'


async def process_search(ctx, call_tool, name, arguments):
    if name != 'search':
        raise ValueError('Only website search is available through Oxylabs discovery')
    deps = ctx.deps
    async with deps.lock:
        await deps.active()
        if deps.pending_action:
            return 'Confirm the submitted action before searching for another website.'
        started = time.monotonic()
        status = 'ok'
        try:
            result = await call_tool(name, deps.clean_data(arguments))
            text = deps.clean(json.dumps(result, ensure_ascii=False, default=str))
            output = 'Website discovery only; open a result to inspect the page.\n' + text[:12000]
        except BrowserStop:
            raise
        except Exception:
            status = 'error'
            output = 'Oxylabs website search is unavailable. Ask for the website URL or use an already observed link.'
        deps.store.add_step(deps.owner, deps.run_id, 'search', 'Searched for a website', status,
                            error_code='search_unavailable' if status == 'error' else None)
        deps.step_seconds.append(time.monotonic() - started)
        return output


def discovery_toolsets(settings):
    if settings.backend != 'oxylabs' or not settings.web_api_key:
        return []
    from pydantic_ai.mcp import MCPToolset
    return [MCPToolset(ENDPOINT, id='oxylabs', headers={'Authorization': 'Bearer ' + settings.web_api_key},
                       process_tool_call=process_search).filtered(lambda ctx, tool: tool.name == 'search')]
