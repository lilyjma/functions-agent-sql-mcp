import os
import json
import asyncio
import logging
import azure.functions as func
from copilot import CopilotClient, PermissionHandler, SubprocessConfig

app = func.FunctionApp()

# A single CopilotClient (and the Copilot CLI subprocess behind it) is shared across
# all requests. It is created lazily on first use and then reused, which is the
# recommended pattern for driving the SDK from inside a Function host.
_client: CopilotClient | None = None
_client_lock = asyncio.Lock()


async def _get_client() -> CopilotClient:
    """Return the shared CopilotClient, starting it (and the CLI) on first use.

    We do not pass ``cli_path``: the ``github-copilot-sdk`` wheel bundles a matching
    Copilot CLI binary (``copilot/bin/copilot``) for the platform it is installed on,
    so on the Linux Function host pip installs the Linux wheel and the SDK drives its
    own bundled CLI. ``COPILOT_CLI_PATH`` can still override this for local testing.
    """
    global _client
    if _client is None:
        async with _client_lock:
            if _client is None:
                sub = {}
                cli_path = os.environ.get("COPILOT_CLI_PATH")
                if cli_path:
                    sub["cli_path"] = cli_path
                github_token = os.environ.get("GITHUB_TOKEN")
                if github_token:
                    sub["github_token"] = github_token
                new_client = CopilotClient(SubprocessConfig(**sub), auto_start=False)
                await new_client.start()
                _client = new_client
    return _client

# OAuth scope (token audience) for the Entra-protected SQL MCP server, which is
# fronted by a Logic Apps connector gateway (API hub).
SQL_MCP_SCOPE = "https://apihub.azure.com/.default"

_managed_credential = None


def _get_managed_credential():
    """Return a lazily-created DefaultAzureCredential.

    In Azure this resolves to the Function's managed identity; locally it uses the
    developer's `az login` identity. Created on first use (not at import time) so
    the import and identity probe are only paid when SQL MCP auth is actually needed.
    """
    global _managed_credential
    if _managed_credential is None:
        from azure.identity import DefaultAzureCredential

        _managed_credential = DefaultAzureCredential()
    return _managed_credential


instructions = """
You are an assistant that answers questions about data in a SQL database.
The database is ONLY reachable through the sql-mcp tools (for example
describe_entities to discover the available entities/tables, and read_records to
fetch rows). Always use the sql-mcp tools to answer data questions. Do not use any
other or built-in SQL tool, and do not answer from memory.
Chain multiple sql-mcp tool calls when needed, and report only the actual data the
tools return; do not invent anything.
"""


def _session_config():
    """Build the Copilot session config: model provider + the SQL MCP server."""
    config = {
        "system_message": {"content": instructions},
        "on_permission_request": PermissionHandler.approve_all,
        # Restrict the agent to the SQL MCP tools. The bundled Copilot CLI ships a
        # built-in `sql` tool that gives each session its own local SQLite database
        # (pre-seeded with `todos`/`todo_deps` tables). Faced with a "SQL database"
        # question the model will happily use that local `sql` tool and report "0
        # tables" instead of calling our sql-mcp server. Excluding `sql` (plus the
        # shell/file built-ins that could reach a local db another way) forces every
        # data question through the SQL MCP server, which is the point of this sample.
        "excluded_tools": [
            "sql", "shell", "bash", "read", "write", "create", "edit",
            "str_replace_editor", "grep", "glob", "view", "fetch",
        ],
    }

    # Model provider (Azure OpenAI). Uses an API key if provided, otherwise a
    # managed-identity bearer token for the Cognitive Services scope.
    base_url = os.environ.get("AZURE_OPENAI_ENDPOINT")
    api_key = os.environ.get("AZURE_OPENAI_API_KEY")
    model = os.environ.get("AZURE_OPENAI_DEPLOYMENT_NAME") or os.environ.get("AZURE_OPENAI_MODEL", "gpt-5-mini")
    if base_url:
        config["model"] = model
        provider = {"type": "azure", "base_url": base_url}
        if api_key:
            provider["api_key"] = api_key
        else:
            token = _get_managed_credential().get_token("https://cognitiveservices.azure.com/.default")
            provider["bearer_token"] = token.token
        config["provider"] = provider

    # Attach the Entra-protected SQL MCP server. The server sits behind a gateway that
    # requires a bearer token; we mint one with the Function's managed identity and pass
    # it as a static Authorization header on the MCP server config (the SDK forwards
    # these headers on every request to the server).
    sql_mcp_url = os.environ.get("SQL_MCP_SERVER_URL")
    if sql_mcp_url:
        token = _get_managed_credential().get_token(SQL_MCP_SCOPE)
        config["mcp_servers"] = {
            "sql-mcp": {
                "type": "http",
                "url": sql_mcp_url,
                "tools": ["*"],
                "headers": {"Authorization": f"Bearer {token.token}"},
            }
        }

    return config


async def _wait_for_mcp(session, server_name: str, timeout: float = 90.0) -> None:
    """Block until an attached MCP server finishes connecting.

    MCP servers connect asynchronously after the session is created (for sql-mcp
    this includes the OAuth handshake against the gateway). If we send the prompt
    before the server is connected, its tools aren't yet available to the model, so
    we poll the server list until it reports a connected state.
    """
    import asyncio
    import time

    start = time.time()
    while time.time() - start < timeout:
        listing = await session.rpc.mcp.list()
        status = next((str(s.status) for s in listing.servers if s.name == server_name), "")
        low = status.lower()
        if "connect" in low and "dis" not in low:
            return
        if "fail" in low or "error" in low:
            raise RuntimeError(f"MCP server '{server_name}' failed to connect (status: {status}).")
        await asyncio.sleep(1)
    raise RuntimeError(f"MCP server '{server_name}' did not connect within {timeout:.0f}s.")


async def _ask_agent(prompt: str) -> str:
    """Run a single agent turn. The agent may call the SQL MCP tools to answer."""
    config = _session_config()
    client = await _get_client()
    session = await client.create_session(**config)
    try:
        if "sql-mcp" in config.get("mcp_servers", {}):
            await _wait_for_mcp(session, "sql-mcp")
        reply = await session.send_and_wait(prompt)
        return (reply.data.content if reply and reply.data else None) or "No response"
    finally:
        await session.disconnect()


def _resolve_config_dir() -> str | None:
    """Directory where the SDK persists session state (conversation history).

    Multi-turn resume reads/writes ``{dir}/session-state/{session_id}/``. Priority:
    an explicit ``COPILOT_CONFIG_DIR`` override; else the Azure Files mount at
    ``/code-assistant-session`` when running in the Functions container (so state is
    durable and shared across instances); else ``None`` to use the SDK default
    (``~/.copilot``) for local development.
    """
    explicit = os.environ.get("COPILOT_CONFIG_DIR")
    if explicit:
        return explicit
    if os.environ.get("CONTAINER_NAME"):
        return "/code-assistant-session"
    return None


def _session_exists(config_dir: str | None, session_id: str) -> bool:
    """Return True if a persisted session directory exists on disk."""
    base = config_dir or os.path.expanduser("~/.copilot")
    return os.path.isdir(os.path.join(base, "session-state", session_id))


async def _chat_agent(prompt: str, session_id: str | None) -> tuple[str, str]:
    """Run one conversational turn, resuming prior context when a session id is given.

    Returns ``(reply_text, session_id)`` so the caller can round-trip the id and keep
    the conversation going across otherwise-stateless HTTP invocations. When the id
    refers to a persisted session it is resumed (the agent remembers earlier turns);
    otherwise a new session is created.
    """
    config = _session_config()
    config_dir = _resolve_config_dir()
    if config_dir:
        config["config_dir"] = config_dir

    client = await _get_client()
    if session_id and _session_exists(config_dir, session_id):
        session = await client.resume_session(session_id, **config)
    else:
        if session_id:
            config["session_id"] = session_id
        session = await client.create_session(**config)

    try:
        if "sql-mcp" in config.get("mcp_servers", {}):
            await _wait_for_mcp(session, "sql-mcp")
        reply = await session.send_and_wait(prompt)
        text = (reply.data.content if reply and reply.data else None) or "No response"
        return text, session.session_id
    finally:
        await session.disconnect()


@app.route(route="ask", methods=["POST"])
async def ask(req: func.HttpRequest) -> func.HttpResponse:
    """HTTP trigger: forward the request body to the SQL MCP-backed agent."""
    prompt = req.get_body().decode("utf-8").strip()
    if not prompt:
        return func.HttpResponse(
            "Provide a prompt in the request body, e.g. \"List the blog posts in the database.\"",
            status_code=400,
            mimetype="text/plain",
        )

    try:
        response_text = await _ask_agent(prompt)
    except Exception as exc:  # noqa: BLE001 - surface the failure to the caller
        logging.exception("Agent request failed.")
        return func.HttpResponse(str(exc), status_code=502, mimetype="text/plain")

    return func.HttpResponse(response_text, mimetype="text/plain")


@app.route(route="chat", methods=["POST"])
async def chat(req: func.HttpRequest) -> func.HttpResponse:
    """Multi-turn variant of /ask that keeps conversation context across turns.

    Send the prompt in the request body. The response carries an ``x-ms-session-id``
    header; echo it back on the next request to continue the same conversation (the
    agent resumes the persisted session and remembers earlier turns). Omit the header
    to start a fresh conversation. The JSON body is ``{"session_id", "response"}``.
    """
    prompt = req.get_body().decode("utf-8").strip()
    if not prompt:
        return func.HttpResponse(
            "Provide a prompt in the request body, e.g. \"List the blog posts in the database.\"",
            status_code=400,
            mimetype="text/plain",
        )

    session_id = req.headers.get("x-ms-session-id")
    try:
        response_text, session_id = await _chat_agent(prompt, session_id)
    except Exception as exc:  # noqa: BLE001 - surface the failure to the caller
        logging.exception("Chat request failed.")
        return func.HttpResponse(str(exc), status_code=502, mimetype="text/plain")

    return func.HttpResponse(
        json.dumps({"session_id": session_id, "response": response_text}),
        mimetype="application/json",
        headers={"x-ms-session-id": session_id},
    )
