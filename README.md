# SQL MCP Agent QuickStart (Python Copilot SDK on Azure Functions)

A simple AI agent built with the GitHub Copilot SDK, running as an Azure Function. The agent answers questions about data in a SQL database by calling a fully managed, remote Azure SQL MCP server hosted in Azure Connector Namespace (preview). The server is Entra ID OAuth-protected and running in an isolated compute environment. The Function authenticates to that MCP server using its _managed identity_, so no secrets are stored in the app.

The Azure Connector Namespace is a new offering that allows you to host fully managed, remote MCP servers in minutes. The namespace handles infrastructure provisioning and management, server auth, scaling, and provides built-in integration with App Insights for observability. Learn more about [hosted MCP servers](https://learn.microsoft.com/azure/logic-apps/connector-namespace/connector-namespace-hosted-mcp) in Connector Namespace.

## Prerequisites

- Python 3.13+ via [uv](https://docs.astral.sh/uv/getting-started/installation/)
- [Azure Functions Core Tools](https://learn.microsoft.com/en-us/azure/azure-functions/functions-run-local#install-the-azure-functions-core-tools)
- [Azure Developer CLI (azd)](https://aka.ms/azd-install) (for deploying to Azure)
- Access to an AI model via one of:
  - **GitHub Copilot subscription** - models are available automatically
  - **Bring Your Own Key (BYOK)** - an Azure OpenAI / Microsoft Foundry model deployment (see [BYOK docs](https://github.com/github/copilot-sdk/blob/main/docs/auth/byok.md))
- An [Azure SQL MCP](https://learn.microsoft.com/azure/logic-apps/connector-namespace/hosted-mcp-quickstart?pivots=sql) server hosted in Connector Namespace.

## Quickstart

1. Clone the repository.
1. Edit `local.settings.json` and set `SQL_MCP_SERVER_URL`. Copy it from the server's **Overview** page in the [Connector Namespace portal](https://connectors.azure.com/). This is what the agent connects to.
1. Authorize your identity on the SQL MCP server (for local dev). Locally, `DefaultAzureCredential` uses your `az login` identity, so the MCP server must have an access policy for **your** user object ID. Get your object ID and tenant ID:

   ```bash
   az login
   az ad signed-in-user show --query id -o tsv   # your object ID
   az account show --query tenantId -o tsv        # your tenant ID
   ```

   Then add an access policy on the server ([docs](https://learn.microsoft.com/azure/logic-apps/connector-namespace/hosted-mcp-dev-guide#access-policy)):

   1. In the [Connector Namespace portal](https://connectors.azure.com/), open the namespace that has the server deployed.
   2. Select the **MCP Connectors** tab and open your SQL MCP server.
   3. Select the **Access Policies** tab, then **+ Add Access Policy**.
   4. Set **Principal Type** to **User**, enter your **Principal Object ID** and **Tenant ID**, and save.
1. Start the local Azure Storage emulator required by the Function app:

    ```bash
    azurite --skipApiVersionCheck --silent --location ./.azurite
    ```

1. In a new terminal, install dependencies:

   ```bash
   uv sync
   ```

1. Install the pinned Copilot CLI that the SDK drives. This sample pins the CLI via
   [`package.json`](package.json) so the SDK and CLI stay a matched, tested pair (see
   [Session persistence](#session-persistence)); the SDK does not auto-download it:

   ```bash
   npm install
   ```

1. Run the function locally:

   ```bash
   uv run func start
   ```

1. Ask the agent something (in a new terminal):

   ```bash
   # Interactive chat client (multi-turn — keeps conversation context)
   uv run chat.py

   # Single-turn: POST a prompt to /api/ask and get one reply
   curl -X POST http://localhost:7071/api/ask \
     -d "List tables in the database."

   # Multi-turn: /api/chat returns an x-ms-session-id header; send it back
   # on the next request to continue the same conversation.
   curl -i -X POST http://localhost:7071/api/chat \
     -d "List the blog posts in the database."
   # ...then reuse the returned id:
   curl -i -X POST http://localhost:7071/api/chat \
     -H "x-ms-session-id: <id-from-previous-response>" \
     -d "Which of those has the most comments?"
   ```

   To chat with a deployed instance, grab the URL and function key from your `azd` environment (this key is to access the Function app):

   ```bash
   export AGENT_URL=$(azd env get-value SERVICE_API_URI)
   export FUNCTION_KEY=$(az functionapp keys list \
     -n $(azd env get-value AZURE_FUNCTION_APP_NAME) \
     -g $(azd env get-value RESOURCE_GROUP) \
     --query "functionKeys.default" -o tsv)

   uv run chat.py
   ```

## How it works

```
POST /api/ask  "List the blog posts in the database"
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

The agent running on Functions has the SQL MCP server attached, and its system instructions tell it to use the `sql-mcp` tools whenever the user asks about database data. The model decides which tools to call — a single question usually results in several tool calls (e.g. `describe_entities` then `read_records`).

Two HTTP endpoints are exposed:

- **`POST /api/ask`** — single-turn. Forwards the prompt to the agent and returns one reply. No memory between requests.
- **`POST /api/chat`** — multi-turn. Same agent, but the conversation is persisted so follow-up questions keep their context. The response returns an `x-ms-session-id` header; send it back on the next request to resume the same session (the agent remembers earlier turns). Omit the header to start a fresh conversation. This is the realistic way to work with the SQL MCP server, since answering a question often takes several turns of tool calls and follow-ups. `chat.py` uses this endpoint and round-trips the session id for you.

### Session persistence

Multi-turn works because the Copilot SDK persists each conversation's history to `{config_dir}/session-state/{session_id}/` and resumes it on the next turn.

- **Locally**, sessions are stored under `~/.copilot/session-state/`.
- **In Azure**, the function app mounts an **Azure Files SMB share** (`code-assistant-session`) at `/code-assistant-session` (set via the `COPILOT_CONFIG_DIR` app setting), so conversation state is durable and shared across all instances — it survives cold starts, scale-out, and instance recycling.

**How Azure Files is accessed:** the SMB file-share mount requires the storage account **key**, so the storage account sets `allowSharedKeyAccess: true` (with an `Az.Sec.DisableLocalAuth.Storage::Skip` policy annotation documenting why). This is scoped to the session-state file share only. Everything else stays keyless: the agent authenticates to the **SQL MCP server** with the Function's **managed identity**, and the Function host authenticates to blob/queue storage with managed identity (`AzureWebJobsStorage__credential: managedidentity`). See `infra/main.bicep` (storage account + share) and `infra/app/api.bicep` (the `azurestorageaccounts` mount).

**Pinned SDK + CLI:** the SDK writes session state through the Copilot CLI's own store, and that store must work on the mounted SMB share. This sample pins a matched, tested pair — `github-copilot-sdk==0.2.0` (in `requirements.txt`/`pyproject.toml`) and CLI `@github/copilot@1.0.13` (in [`package.json`](package.json)) — so the SDK doesn't auto-download a newer CLI whose session store fails on the SMB share. The SDK does not download the CLI itself, so the correct binary must be bundled:

- **For deployment**, azd `prepackage` and `predeploy` hooks (in [`azure.yaml`](azure.yaml)) run `rm -rf node_modules && npm install --os=linux --cpu=x64`, which downloads the **Linux** CLI binary into `node_modules/` (even when you deploy from macOS/Windows) and overwrites any local binary. That folder is included in the deployment package, so the binary is present at runtime. Both `azd up`/`azd package` (prepackage) and `azd deploy` (predeploy) trigger it. **This requires Node.js + npm on the machine you run `azd` from.**
- **For local development**, you run `npm install` yourself, which installs the binary for your own OS.

`function_app.py` resolves the bundled binary under `node_modules/@github/copilot-<platform>/copilot` (overridable with `COPILOT_CLI_PATH`) and passes it to the SDK via `SubprocessConfig(cli_path=...)`. Note: after an `azd` deploy your local `node_modules/` holds the Linux binary — re-run `npm install` before running the app locally again.

## Deploy to Azure

```bash
azd auth login
azd up
```

This provisions all resources and configures the app. `azd up` prompts for the SQL MCP server URL and these two regions:

- **AI location** (`AZURE_AI_LOCATION`): the AI Services account and model deployment. There are some regions where Azure Functions is supported but AI services are not. In those case, pick one close to your Function region.
- **Location** (`AZURE_LOCATION`): the Function app, storage, and plan. Co-locate this with your SQL MCP server for low-latency MCP calls. 

### Authorize the Function app's managed identity (post-deployment)

After `azd up`, the deployed Function app calls the SQL MCP server with its **user-assigned managed identity**, so you need to set an access policy for that identity's object ID (the same way you authorized your own identity for local dev). Get the object ID from the azd outputs:

```bash
azd env get-value AZURE_FUNCTION_MI_PRINCIPAL_ID   # object ID to authorize
az account show --query tenantId -o tsv            # tenant ID
```

Then add the access policy on the server like you did previously.

> The server matches callers by object ID, so register the managed identity with **Principal Type = User** even though it's a managed identity.

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

- Builds a session config with the Azure OpenAI model provider and (when `SQL_MCP_SERVER_URL` is set) the `sql-mcp` MCP server plus a managed-identity bearer `Authorization` header.
- Exposes `POST /api/ask` (single-turn) that forwards the request body to the agent and returns the reply.
- Exposes `POST /api/chat` (multi-turn) that resumes or creates a persisted session keyed by the `x-ms-session-id` header, so follow-up questions keep their context.

[`chat.py`](chat.py) is a lightweight console client that POSTs messages to `/api/chat` in a loop, round-tripping the `x-ms-session-id` header so the conversation stays multi-turn. It defaults to `http://localhost:7071` but can be pointed at a deployed instance via the `AGENT_URL` environment variable.

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
