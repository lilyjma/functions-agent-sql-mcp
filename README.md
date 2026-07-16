# SQL MCP Agent QuickStart (Python Copilot SDK on Azure Functions)

A simple AI agent built with the GitHub Copilot SDK, running as an Azure Function. The agent answers questions about data in a SQL database by calling a **fully managed, remote Azure SQL MCP server** hosted in Azure Connector Namespace (preview). The server is Entra ID OAuth-protected and runs in an isolated compute environment. The Function authenticates to that MCP server using its _managed identity_, so no secrets are stored in the app.

The Azure Connector Namespace is a new offering that allows you to host fully managed, remote MCP servers in minutes. The namespace handles infrastructure provisioning and management, server auth, scaling, and provides built-in integration with App Insights for observability. Learn more about [hosted MCP servers](https://learn.microsoft.com/azure/logic-apps/connector-namespace/connector-namespace-hosted-mcp) in Connector Namespace.

## Prerequisites

- Python 3.13+ via [uv](https://docs.astral.sh/uv/getting-started/installation/)
- [Azure Functions Core Tools](https://learn.microsoft.com/en-us/azure/azure-functions/functions-run-local#install-the-azure-functions-core-tools)
- [Azure Developer CLI (azd)](https://aka.ms/azd-install) (for deploying to Azure)
- Access to an AI model via one of:
  - **GitHub Copilot subscription** - models are available automatically
  - **Bring Your Own Key (BYOK)** - an Azure OpenAI / Microsoft Foundry model deployment (see [BYOK docs](https://github.com/github/copilot-sdk/blob/main/docs/auth/byok.md))

## Quickstart

1. Deploy an Azure SQL MCP server in Connector Namespace following [azure-sql-mcp azd sample](https://github.com/microsoft/sql-server-samples/tree/master/samples/applications/azure-sql-mcp).
2. Clone this repository.
3. Edit `local.settings.json` and set `SQL_MCP_SERVER_URL` to your server's endpoint.
4. Authorize your identity on the SQL MCP server (for local dev). Locally, `DefaultAzureCredential` uses your `az login` identity, so the MCP server must have an access policy for **your** user object ID. Get your object ID and tenant ID:

   ```bash
   az login
   az ad signed-in-user show --query id -o tsv   # your object ID
   az account show --query tenantId -o tsv        # your tenant ID
   ```

   Then [add an access policy](https://learn.microsoft.com/azure/logic-apps/connector-namespace/hosted-mcp-dev-guide#access-policy) on the server:

   1. In the [Connector Namespace portal](https://connectors.azure.com/), open the namespace that has the server deployed.
   2. Select the **MCP Connectors** tab and open your SQL MCP server.
   3. Select the **Access Policies** tab, then **+ Add Access Policy**.
   4. Set **Principal Type** to **User**, enter your **Principal Object ID** and **Tenant ID**, and save.
5. Start the local Azure Storage emulator required by the Function app:

    ```bash
    azurite --skipApiVersionCheck --silent --location ./.azurite
    ```

6. In a new terminal, install dependencies:

   ```bash
   uv sync
   ```

7. Run the function locally:

   ```bash
   uv run func start
   ```

8. Ask the agent something (in a new terminal):

   ```bash
   # Interactive chat client

   uv run chat.py
   ```

   Ask a question like "List tables in the database."

   ```bash
   # Or use curl directly

   curl -X POST http://localhost:7071/api/chat -d "List tables in the database."

   # ...then reuse the returned id:
   curl -i -X POST http://localhost:7071/api/chat \
   -H "x-ms-session-id: <id-from-previous-response>" \
   -d "What's the first table?"
   ```

> [!NOTE]
> **`MCP server 'sql-mcp' failed to connect (status: ServerStatus.FAILED)`**
>
> This is usually **transient** — the remote SQL MCP server occasionally fails its OAuth handshake.

## How it works

```
POST /api/chat  "List tables in the database"
        │
        ▼
  Azure Function ── Copilot SDK agent
        │   session config attaches the sql-mcp server + a managed-identity bearer header
        ▼
  sql-mcp server (Entra-protected)  ← Function's managed identity token
        │   the model calls tools like describe_entities, read_records
        ▼
  SQL database
```

The agent running on Functions has the SQL MCP server attached, and its system instructions tell it to use the `sql-mcp` server whenever the user asks about database data. The model decides which tools to cal.

## Deploy to Azure

```bash
azd auth login
azd up
```

This provisions all resources and configures the app. `azd up` prompts for the SQL MCP server URL and these two regions:

- **AI location** (`AZURE_AI_LOCATION`): the AI Services account and model deployment. There are some regions where Azure Functions is supported but AI services are not. In those case, pick one close to your Function region
- **Location** (`AZURE_LOCATION`): the Function app, storage, and plan. Co-locate this with your SQL MCP server for low-latency MCP calls

### Authorize the Function app's managed identity (post-deployment)

After `azd up`, the deployed Function app calls the SQL MCP server with its **user-assigned managed identity**, so you need to set an access policy for that identity's object ID (the same way you authorized your own identity for local dev). Get the object ID from the azd outputs:

```bash
azd env get-value AZURE_FUNCTION_MI_PRINCIPAL_ID   # object ID to authorize
az account show --query tenantId -o tsv            # tenant ID
```

Then add the access policy on the server like you did previously in the [Quickstart](#quickstart) section.

### Test

To chat with a deployed instance, grab the URL and function key from your `azd` environment (this key is to access the Function hosted agent):

   ```bash
   export AGENT_URL=$(azd env get-value SERVICE_API_URI)
   export FUNCTION_KEY=$(az functionapp keys list \
      -n $(azd env get-value AZURE_FUNCTION_APP_NAME) \
      -g $(azd env get-value RESOURCE_GROUP) \
      --query "functionKeys.default" -o tsv)
   ```

Use curl. The `/api/chat` endpoint returns an `x-ms-session-id` header — send it back on the next request to continue the same conversation:

   ```bash
   export url="$AGENT_URL/api/chat?code=$FUNCTION_KEY"

   # First turn (-i so you can see the x-ms-session-id response header)
   curl -i -X POST $url -d "List tables in the database."

   # Follow-up turn: reuse the returned id to keep context
   curl -i -X POST $url \
      -H "x-ms-session-id: <id-from-previous-response>" \
      -d "What's the first table?"
   ```

Use `/api/ask` instead if you just want a single-turn reply with no memory.

## How Function app connects to the SQL MCP server

The Function app authenticates to an Entra-protected MCP server without any secrets.

When it attaches the `sql-mcp` server to the session, it mints a bearer token with `DefaultAzureCredential` for the server's scope (`https://apihub.azure.com/.default`) and passes it as a static `Authorization` header on the MCP server config. The SDK forwards that header on every request to the server:

   ```python
   token = _get_managed_credential().get_token(SQL_MCP_SCOPE)
   config["mcp_servers"] = {
       "sql-mcp": {
           "type": "http",
           "url": sql_mcp_url,
           "tools": ["*"],
           "headers": {"Authorization": f"Bearer {token.token}"},
       }
   }
   ```

- **Locally**, `DefaultAzureCredential` uses your `az login` identity.
- **In Azure**, it uses the Function's user-assigned managed identity.

The Connector Namespace only accepts callers that have an **access policy** for their object ID — the identity `DefaultAzureCredential` resolves to (your dev identity locally, the Function's managed identity in Azure).

## Source code

The agent logic is in [`function_app.py`](function_app.py). It:

- Builds a session config with the Azure OpenAI model provider and (when `SQL_MCP_SERVER_URL` is set) the `sql-mcp` MCP server plus the managed-identity auth handler.
- Exposes POST `/api/ask` (single-turn) that forwards the request body to the agent and returns the reply.
- Exposes POST `/api/chat` (multi-turn) that resumes or creates a persisted session keyed by the x-ms-session-id header, so follow-up questions keep their context.

[`chat.py`](chat.py) is a lightweight console client that POSTs messages to /api/chat in a loop, round-tripping the x-ms-session-id header so the conversation stays multi-turn. It defaults to `http://localhost:7071` but can be pointed at a deployed instance via the `AGENT_URL` environment variable.

## Session persistence

Multi-turn works because the Copilot SDK persists each conversation's history to `{config_dir}/session-state/{session_id}/` and resumes it on the next turn.

- Locally, sessions are stored under `~/.copilot/session-state/`.
- In Azure, the function app mounts an Azure Files SMB share (`code-assistant-session`) at `/code-assistant-session` (set via the `COPILOT_CONFIG_DIR` app setting), so conversation state is durable and shared across all instances and survives cold starts, scale-out, and instance recycling.

## Using Microsoft Foundry (BYOK)

By default the agent uses GitHub Copilot's models. To use your own Azure OpenAI / Foundry model instead, set:

```bash
export AZURE_OPENAI_ENDPOINT="https://<your-ai-services>.openai.azure.com/"
export AZURE_OPENAI_DEPLOYMENT_NAME="chat"
export AZURE_OPENAI_API_KEY="<your-api-key>"   # omit to use managed identity for the model
```

If you omit `AZURE_OPENAI_API_KEY`, the app requests a managed-identity token for the `https://cognitiveservices.azure.com/.default` scope instead. See the [BYOK docs](https://github.com/github/copilot-sdk/blob/main/docs/auth/byok.md).

## Learn more

- [GitHub Copilot SDK](https://github.com/github/copilot-sdk) · [Python docs](https://github.com/github/copilot-sdk/tree/main/python)
- [Azure Connector Namespace](https://learn.microsoft.com/azure/logic-apps/connector-namespace/connector-namespace-overview)
- [Hosted MCP servers in Connector Namespace](https://learn.microsoft.com/azure/logic-apps/connector-namespace/connector-namespace-hosted-mcp)
- [Managed identities for Azure resources](https://learn.microsoft.com/en-us/entra/identity/managed-identities-azure-resources/overview)
