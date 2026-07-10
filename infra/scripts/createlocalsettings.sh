#!/bin/bash

set -e

if [ ! -f "./local.settings.json" ]; then

    output=$(azd env get-values)

    # Initialize variables
    SqlMcpServerUrl=""
    AzureOpenAIEndpoint=""
    AzureOpenAIDeploymentName=""

    # Parse the output to get the values provisioned by azd
    while IFS= read -r line; do
        if [[ $line == "SQL_MCP_SERVER_URL="* ]]; then
            SqlMcpServerUrl=$(echo "$line" | cut -d '=' -f 2- | tr -d '"')
        fi
        if [[ $line == "AZURE_OPENAI_ENDPOINT="* ]]; then
            AzureOpenAIEndpoint=$(echo "$line" | cut -d '=' -f 2- | tr -d '"')
        fi
        if [[ $line == "AZURE_OPENAI_DEPLOYMENT_NAME="* ]]; then
            AzureOpenAIDeploymentName=$(echo "$line" | cut -d '=' -f 2- | tr -d '"')
        fi
    done <<< "$output"

    cat <<EOF > ./local.settings.json
{
    "IsEncrypted": "false",
    "Values": {
        "AzureWebJobsStorage": "UseDevelopmentStorage=true",
        "FUNCTIONS_WORKER_RUNTIME": "python",
        "SQL_MCP_SERVER_URL": "$SqlMcpServerUrl",
        "AZURE_OPENAI_ENDPOINT": "$AzureOpenAIEndpoint",
        "AZURE_OPENAI_DEPLOYMENT_NAME": "$AzureOpenAIDeploymentName"
    }
}
EOF

fi
