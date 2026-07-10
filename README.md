# SQL MCP Agent QuickStart (Python Copilot SDK on Azure Functions)

A simple AI agent built with the GitHub Copilot SDK, running as an Azure Function. The agent answers questions about data in a SQL database by calling a fully managed, remote Azure SQL MCP server hosted in Azure Connector Namespace (preview). The server is Entra ID OAuth-protected and running in an isolated compute environment. The Function authenticates to that MCP server using its **managed identity**, so no secrets are stored in the app.

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

2. Install dependencies:

   ```bash
   uv sync
   ```

3. Create your local settings file:

   ```bash
   cp local.settings.json.sample local.settings.json
   ```

   Then edit `local.settings.json` and set the values under `"Values"`:

   - **`SQL_MCP_SERVER_URL`** (required) — the hosted MCP endpoint of your SQL MCP server, ending in `/mcp`. Copy it from the server's **Overview** page in the [Connector Namespace portal](https://connectors.azure.com/). This is what the agent connects to.
   - **Model settings** (optional locally) — leave `AZURE_OPENAI_ENDPOINT` blank to use your **GitHub Copilot** subscription's models. To use your own Azure OpenAI / Foundry model, set `AZURE_OPENAI_ENDPOINT` and `AZURE_OPENAI_DEPLOYMENT_NAME` (and `AZURE_OPENAI_API_KEY` for key auth, or leave it blank to use managed identity). See [Using Microsoft Foundry (BYOK)](#using-microsoft-foundry-byok).

   `local.settings.json` is git-ignored and **never deployed** — `func start` loads its `"Values"` into environment variables. In Azure, the same variables come from the Function app's settings (provisioned by `azd up`).

4. Authorize your identity on the SQL MCP server (for local dev). Locally, `DefaultAzureCredential` uses your `az login` identity, so the MCP server must have an access policy for **your** user object ID. Get your object ID and tenant ID:

   ```bash
   az login
   az ad signed-in-user show --query id -o tsv   # your object ID
   az account show --query tenantId -o tsv        # your tenant ID
   ```

   Then add an access policy on the server ([docs](https://learn.microsoft.com/azure/logic-apps/connector-namespace/hosted-mcp-dev-guide#access-policy)):

   1. In the [Connector Namespace portal](https://connectors.azure.com/), open your namespace.
   2. Select the **MCP Connectors** tab and open your SQL MCP server.
   3. Select the **Access Policies** tab, then **+ Add Access Policy**.
   4. Set **Principal Type** to **User**, enter your **Principal Object ID** and **Tenant ID**, and save.

5. Run the function locally:

   ```bash
   uv run func start
   ```

6. Ask the agent something (in a new terminal):

   ```bash
   # Interactive chat client
   uv run chat.py

   # Or use curl directly
   curl -X POST http://localhost:7071/api/ask \
     -d "List the blog posts in the database."
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
        │   session config attaches the sql-mcp server + a managed-identity auth handler
        ▼
  sql-mcp server (Entra-protected)  ← Function's managed identity token
        │   the model calls tools like describe_entities, read_records
        ▼
  SQL database
```

There is one agent that runs on Functions. It has the SQL MCP server attached, and its system instructions tell it to use the `sql-mcp` tools whenever the user asks about database data. The `/api/ask` endpoint forwards the caller's prompt to that agent; the model decides which tools to call.


## Deploy to Azure

```bash
azd auth login
azd up
```

This provisions all resources and configures the app. `azd up` **prompts** for the SQL MCP server URL (and the two regions below). To skip the prompt, preset it in your azd environment first:

```bash
azd env set SQL_MCP_SERVER_URL "https://<gateway-host>/api/connectorGateways/<id>/mcpServerConfigs/sql-mcp/mcp"
```

The URL is stored as the Function app's `SQL_MCP_SERVER_URL` app setting (wired in [`infra/main.bicep`](infra/main.bicep)) — that's how the deployed agent gets it. `local.settings.json` is not involved in Azure.

### Choosing regions

`azd` prompts for **two** regions, wired through [`infra/main.parameters.json`](infra/main.parameters.json):

- **Location** (`AZURE_LOCATION`) → the Function, storage, and plan. Co-locate this with your SQL MCP server for low-latency MCP calls. You can pick any Azure region.
- **AI location** (`AZURE_AI_LOCATION`) → the AI Services account and model deployment. This picker is **pre-filtered to the regions that offer `gpt-5-mini` on the GlobalStandard SKU** (see the `@allowed` list on the `aiLocation` param in [`infra/main.bicep`](infra/main.bicep)), so you can't accidentally choose a region the model isn't available in. Pick one close to your Function region.

### Authorize the Function's managed identity (post-deployment)

After `azd up`, the deployed Function calls the SQL MCP server with its **user-assigned managed identity**, so the server needs an access policy for that identity's object ID (the same way you authorized your own identity for local dev). Get the object ID from the azd outputs:

```bash
azd env get-value AZURE_FUNCTION_MI_PRINCIPAL_ID   # object ID to authorize
az account show --query tenantId -o tsv            # tenant ID
```

Then add the access policy on the server:

1. In the [Connector Namespace portal](https://connectors.azure.com/), open your namespace.
2. Select the **MCP Connectors** tab and open your SQL MCP server.
3. Select the **Access Policies** tab, then **+ Add Access Policy**.
4. Set **Principal Type** to **User**, enter the managed identity's **Principal Object ID** (`AZURE_FUNCTION_MI_PRINCIPAL_ID`) and your **Tenant ID**, and save.

> The server matches callers by object ID, so register the managed identity with **Principal Type = User** even though it's a managed identity.

## Connecting to the SQL MCP server

The Function app authenticates to an Entra-protected MCP server without any secrets.

The Copilot SDK calls the `on_mcp_auth_request` handler when the MCP server returns a `401` OAuth challenge. The handler mints a bearer token with `DefaultAzureCredential` for the server's scope (`https://apihub.azure.com/.default`) and hands it back:

   ```python
   def _on_mcp_auth_request(request, context):
       token = _get_managed_credential().get_token(SQL_MCP_SCOPE)
       return {"kind": "token", "accessToken": token.token,
               "tokenType": "Bearer", "expiresIn": ...}
   ```

- **Locally**, `DefaultAzureCredential` uses your `az login` identity.
- **In Azure**, it uses the Function's user-assigned managed identity.

The Connector Namespace only accepts callers that have an **access policy** for their object ID — the identity `DefaultAzureCredential` resolves to (your dev identity locally, the Function's managed identity in Azure). See [step 4](#quickstart) for authorizing your local identity and [Authorize the Function's managed identity](#authorize-the-functions-managed-identity-post-deployment) for the deployed identity.

## Source code

The agent logic is in [`function_app.py`](function_app.py). It:

- Builds a session config with the Azure OpenAI model provider and (when `SQL_MCP_SERVER_URL` is set) the `sql-mcp` MCP server plus the managed-identity auth handler.
- Exposes an HTTP endpoint at `/api/ask` that forwards the request body to the agent and returns the reply.

[`chat.py`](chat.py) is a lightweight console client that POSTs messages to `/api/ask` in a loop. It defaults to `http://localhost:7071` but can be pointed at a deployed instance via the `AGENT_URL` environment variable.

Local development uses [`pyproject.toml`](pyproject.toml) with `uv sync` and `uv run`. [`requirements.txt`](requirements.txt) is kept for Azure Functions packaging and should stay aligned with the dependencies in `pyproject.toml`.

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
