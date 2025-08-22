import os
import time
import requests
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

# 🔧 Load environment variables from .env file if running locally
load_dotenv()

# 🌐 Get webhook URL from environment
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")

if not WEBHOOK_URL:
    print("❌ ERROR: WEBHOOK_URL not set in environment variables.")
    exit(1)

# 🧠 Initialize MCP
mcp = FastMCP("xiaozhi_forwarder")

# 🛠️ Register tool
@mcp.tool(name="forward_tool")
def forward_tool(**kwargs) -> dict:
    """
    Forward tool payload to a webhook (e.g. n8n).
    """
    try:
        print(f"📨 Forwarding to: {WEBHOOK_URL}")
        print(f"🔧 Payload: {kwargs}")

        response = requests.post(WEBHOOK_URL, json=kwargs, timeout=9)

        if response.status_code == 200:
            content_type = response.headers.get("content-type", "")
            result = (
                response.json() if "application/json" in content_type
                else response.text
            )
            return {"success": True, "result": result}
        else:
            return {
                "success": False,
                "error": f"Received {response.status_code} from webhook",
                "body": response.text
            }
    except Exception as e:
        return {"success": False, "error": str(e)}

# 🚀 Start MCP server
if __name__ == "__main__":
    print("🚀 Starting MCP server...")
    mcp.run(transport="stdio")

    # 🧯 Keep container alive in Railway or Docker if XiaoZhi isn't polling actively
    while True:
        time.sleep(60)
