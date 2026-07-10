$ErrorActionPreference = "Stop"

if (-not (Test-Path ".\local.settings.json")) {

    $output = azd env get-values

    # Parse the output to get the values provisioned by azd
    foreach ($line in $output) {
        if ($line -match "^SQL_MCP_SERVER_URL="){
            $SqlMcpServerUrl = ($line -split "=", 2)[1] -replace '"',''
        }
        if ($line -match "^AZURE_OPENAI_ENDPOINT="){
            $AzureOpenAIEndpoint = ($line -split "=", 2)[1] -replace '"',''
        }
        if ($line -match "^AZURE_OPENAI_DEPLOYMENT_NAME="){
            $AzureOpenAIDeploymentName = ($line -split "=", 2)[1] -replace '"',''
        }
    }

    @{
        "IsEncrypted" = "false";
        "Values" = @{
            "AzureWebJobsStorage" = "UseDevelopmentStorage=true";
            "FUNCTIONS_WORKER_RUNTIME" = "python";
            "SQL_MCP_SERVER_URL" = "$SqlMcpServerUrl";
            "AZURE_OPENAI_ENDPOINT" = "$AzureOpenAIEndpoint";
            "AZURE_OPENAI_DEPLOYMENT_NAME" = "$AzureOpenAIDeploymentName";
        }
    } | ConvertTo-Json | Out-File -FilePath ".\local.settings.json" -Encoding ascii
}
