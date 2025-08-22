import os
import requests
from mcp.server.fastmcp import FastMCP

# ✅ Get webhook URL from ENV — DON'T hardcode the full URL as the variable name!
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")

if not WEBHOOK_URL:
    print("❌ ERROR: WEBHOOK_URL not set in environment variables!")
    exit(1)

# ✅ Initialize MCP tool
mcp = FastMCP("n8n_tool")

# ✅ Register tool
@mcp.tool()
def forward_tool(**kwargs) -> dict:
    try:
        print(f"📨 Forwarding tool call to {WEBHOOK_URL}")
        print(f"🔧 Payload: {kwargs}")
        
        response = requests.post(WEBHOOK_URL, json=kwargs, timeout=9)

        if response.status_code == 200:
            return {
                "success": True,
                "result": (
                    response.json()
                    if response.headers.get("content-type", "").startswith("application/json")
                    else response.text
                )
            }
        else:
            return {
                "success": False,
                "error": f"Webhook responded with {response.status_code}",
                "body": response.text
            }
    except Exception as e:
        return {"success": False, "error": str(e)}

# ✅ Run MCP server (stdio transport for XiaoZhi)
if __name__ == "__main__":
    mcp.run(transport="stdio")
