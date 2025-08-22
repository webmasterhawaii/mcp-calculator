import os
import time
import requests
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

# Load .env variables
load_dotenv()

# Your webhook endpoint (from env or hardcoded)
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")
if not WEBHOOK_URL:
    print("❌ ERROR: WEBHOOK_URL not set.")
    exit(1)

# Start MCP server instance
mcp = FastMCP("n8n_webhook_forwarder")

@mcp.tool(
    name="send_to_webhook",
    description="Send a message to an external system like n8n webhook. Use this when you see 'webhook', 'trigger', or 'send message'.",
    parameters={
        "message": {
            "type": "string",
            "description": "The message or data to forward to the webhook"
        }
    }
)
def send_to_webhook(message: str) -> dict:
    try:
        print(f"📨 Forwarding to: {WEBHOOK_URL}")
        print(f"🔧 Payload: {{'message': '{message}'}}")
        response = requests.post(WEBHOOK_URL, json={"message": message}, timeout=9)

        if response.status_code == 200:
            content_type = response.headers.get("content-type", "")
            result = response.json() if "application/json" in content_type else response.text
            return {"success": True, "response": result}
        else:
            return {
                "success": False,
                "error": f"HTTP {response.status_code}",
                "body": response.text
            }
    except Exception as e:
        return {"success": False, "error": str(e)}

# Start the MCP server on STDIO
if __name__ == "__main__":
    print("🚀 Starting MCP server...")
    mcp.run(transport="stdio")
    while True:
        time.sleep(60)
