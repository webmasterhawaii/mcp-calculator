import os
import requests
from mcp.server.fastmcp import FastMCP

# Get webhook URL from env
WEBHOOK_URL = os.environ.get("https://n8n-808-u36625.vm.elestio.app/webhook/xiaozhi", "")

mcp = FastMCP("n8n_tool")

@mcp.tool()
def forward_tool(**kwargs) -> dict:
    try:
        print(f"📨 Forwarding tool call to {WEBHOOK_URL}")
        print(f"🔧 Payload: {kwargs}")
        response = requests.post(WEBHOOK_URL, json=kwargs, timeout=9)

        if response.status_code == 200:
            return {
                "success": True,
                "result": response.json() if response.headers.get("content-type", "").startswith("application/json") else response.text
            }
        else:
            return {
                "success": False,
                "error": f"Webhook responded with {response.status_code}",
                "body": response.text
            }
    except Exception as e:
        return {"success": False, "error": str(e)}

if __name__ == "__main__":
    mcp.run(transport="stdio")

