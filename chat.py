"""Console chat client for the SQL MCP agent function app.

Multi-turn: talks to the /api/chat endpoint and round-trips the x-ms-session-id
header so the agent remembers earlier turns in the conversation.
"""
import json
import os
import urllib.request

BASE_URL = os.environ.get("AGENT_URL", "http://localhost:7071").rstrip("/")
FUNCTION_KEY = os.environ.get("FUNCTION_KEY", "")

print("=== SQL MCP Agent Chat ===")
print(f"Endpoint: {BASE_URL}/api/chat")
print("Ask about data in the database, e.g. \"List the blog posts.\"")
print("Type 'exit' or 'quit' to end.\n")

session_id = None  # captured from the first response, resent on every later turn

while True:
    message = input("You: ").strip()
    if not message or message.lower() in ("exit", "quit"):
        print("Goodbye!")
        break

    url = f"{BASE_URL}/api/chat"
    if FUNCTION_KEY:
        url += f"?code={FUNCTION_KEY}"
    headers = {}
    if session_id:
        headers["x-ms-session-id"] = session_id
    try:
        req = urllib.request.Request(url, data=message.encode(), method="POST", headers=headers)
        with urllib.request.urlopen(req) as resp:
            session_id = resp.headers.get("x-ms-session-id", session_id)
            payload = json.loads(resp.read().decode())
            print(f"\nAgent: {payload.get('response', '')}\n")
    except Exception as e:
        print(f"\nError: {e}\n")
