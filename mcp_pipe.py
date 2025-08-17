"""
Simple MCP stdio <-> WebSocket pipe with optional unified config.
Version: 0.3.0

Usage (env):
    export MCP_ENDPOINT=<ws_endpoint>

Start server process(es):
All configured servers: start one pipe per server concurrently
    python mcp_pipe.py

Config discovery order:
    $MCP_CONFIG, then ./mcp_config.json

Env overrides:
    (none for proxy; uses current Python: python -m mcp_proxy)
"""

import asyncio
import websockets
import subprocess
import logging
import os
import signal
import sys
import random
import json
# (No .env auto-loading; rely on environment being set by caller)
from dotenv import load_dotenv

# Load environment variables from .env file (restores original behavior)
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('MCP_PIPE')

# Reconnection settings
INITIAL_BACKOFF = 1  # Initial wait time in seconds
MAX_BACKOFF = 600  # Maximum wait time in seconds

async def connect_with_retry(uri, target):
    """Connect to WebSocket server with retry mechanism for a given server target."""
    reconnect_attempt = 0
    backoff = INITIAL_BACKOFF
    while True:  # Infinite reconnection
        try:
            if reconnect_attempt > 0:
                wait_time = backoff * (1 + random.random() * 0.1)  # Add some random jitter
                logger.info(f"[{target}] Waiting {wait_time:.2f}s before reconnection attempt {reconnect_attempt}...")
                await asyncio.sleep(wait_time)

            # Attempt to connect
            await connect_to_server(uri, target)

        except Exception as e:
            reconnect_attempt += 1
            logger.warning(f"[{target}] Connection closed (attempt {reconnect_attempt}): {e}")
            # Calculate wait time for next reconnection (exponential backoff)
            backoff = min(backoff * 2, MAX_BACKOFF)

async def connect_to_server(uri, target):
    """Connect to WebSocket server and pipe stdio for the given server target."""
    try:
        logger.info(f"[{target}] Connecting to WebSocket server...")
        async with websockets.connect(uri) as websocket:
            logger.info(f"[{target}] Successfully connected to WebSocket server")

            # Start server process (built from CLI arg or config)
            cmd, env = build_server_command(target)
            process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                encoding='utf-8',
                text=True,
                env=env
            )
            logger.info(f"[{target}] Started server process: {' '.join(cmd)}")
            
            # Create two tasks: read from WebSocket and write to process, read from process and write to WebSocket
            await asyncio.gather(
                pipe_websocket_to_process(websocket, process, target),
                pipe_process_to_websocket(process, websocket, target),
                pipe_process_stderr_to_terminal(process, target)
            )
    except websockets.exceptions.ConnectionClosed as e:
        logger.error(f"[{target}] WebSocket connection closed: {e}")
        raise  # Re-throw exception to trigger reconnection
    except Exception as e:
        logger.error(f"[{target}] Connection error: {e}")
        raise  # Re-throw exception
    finally:
        # Ensure the child process is properly terminated
        if 'process' in locals():
            logger.info(f"[{target}] Terminating server process")
            try:
                process.terminate()
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
            logger.info(f"[{target}] Server process terminated")

async def pipe_websocket_to_process(websocket, process, target):
    """Read data from WebSocket and write to process stdin"""
    try:
        while True:
            # Read message from WebSocket
            message = await websocket.recv()
            logger.debug(f"[{target}] << {message[:120]}...")
            
            # Write to process stdin (in text mode)
            if isinstance(message, bytes):
                message = message.decode('utf-8')
            process.stdin.write(message + '\n')
            process.stdin.flush()
    except Exception as e:
        logger.error(f"[{target}] Error in WebSocket to process pipe: {e}")
        raise  # Re-throw exception to trigger reconnection
    finally:
        # Close process stdin
        if not process.stdin.closed:
            process.stdin.close()

async def pipe_process_to_websocket(process, websocket, target):
    """Read data from process stdout and send to WebSocket"""
    try:
        while True:
            # Read data from process stdout
            data = await asyncio.get_event_loop().run_in_executor(
                None, process.stdout.readline
            )
            
            if not data:  # If no data, the process may have ended
                logger.info(f"[{target}] Process has ended output")
                break
                
            # Send data to WebSocket
            logger.debug(f"[{target}] >> {data[:120]}...")
            # In text mode, data is already a string, no need to decode
            await websocket.send(data)
    except Exception as e:
        logger.error(f"[{target}] Error in process to WebSocket pipe: {e}")
        raise  # Re-throw exception to trigger reconnection

async def pipe_process_stderr_to_terminal(process, target):
    """Read data from process stderr and print to terminal"""
    try:
        while True:
            # Read data from process stderr
            data = await asyncio.get_event_loop().run_in_executor(
                None, process.stderr.readline
            )
            
            if not data:  # If no data, the process may have ended
                logger.info(f"[{target}] Process has ended stderr output")
                break
                
            # Print stderr data to terminal (in text mode, data is already a string)
            sys.stderr.write(data)
            sys.stderr.flush()
    except Exception as e:
        logger.error(f"[{target}] Error in process stderr pipe: {e}")
        raise  # Re-throw exception to trigger reconnection

def signal_handler(sig, frame):
    """Handle interrupt signals"""
    logger.info("Received interrupt signal, shutting down...")
    sys.exit(0)


# ---------- Simple config + command builder ----------

def load_config():
    """Load JSON config from $MCP_CONFIG or ./mcp_config.json. Return dict or {}."""
    path = os.environ.get("MCP_CONFIG") or os.path.join(os.getcwd(), "mcp_config.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Failed to load config {path}: {e}")
        return {}


def build_server_command(target=None):
    """Build [cmd,...] and env for the server process for a given target.

    Priority:
    - target must match a server in config.mcpServers
    If target is None, read from sys.argv[1].
    """
    if target is None:
        assert len(sys.argv) >= 2, "missing server name or script path"
        target = sys.argv[1]
    cfg = load_config()
    servers = cfg.get("mcpServers", {}) if isinstance(cfg, dict) else {}

    if target in servers:
        entry = servers[target] or {}
        if entry.get("disabled"):
            raise RuntimeError(f"Server '{target}' is disabled in config")
        typ = (entry.get("type") or entry.get("transportType") or "stdio").lower()

        # environment for child process
        child_env = os.environ.copy()
        for k, v in (entry.get("env") or {}).items():
            child_env[str(k)] = str(v)

        if typ == "stdio":
            command = entry.get("command")
            args = entry.get("args") or []
            if not command:
                raise RuntimeError(f"Server '{target}' is missing 'command'")
            return [command, *args], child_env

        if typ in ("sse", "http", "streamablehttp"):
            url = entry.get("url")
            if not url:
                raise RuntimeError(f"Server '{target}' (type {typ}) is missing 'url'")
            # Unified approach: always use current Python to run mcp-proxy module
            cmd = [sys.executable, "-m", "mcp_proxy"]
            if typ in ("http", "streamablehttp"):
                cmd += ["--transport", "streamablehttp"]
            # optional headers: {"Authorization": "Bearer xxx"}
            headers = entry.get("headers") or {}
            for hk, hv in headers.items():
                cmd += ["-H", hk, str(hv)]
            cmd.append(url)
            return cmd, child_env

        raise RuntimeError(f"Unsupported server type: {typ}")

    # No fallback: only configured servers are supported
    raise RuntimeError(
        f"'{target}' is not a configured server in mcp_config.json"
    )

if __name__ == "__main__":
    # Register signal handler
    signal.signal(signal.SIGINT, signal_handler)
    
    # No arguments supported; always runs all configured servers
    
    # Get token from environment variable or command line arguments
    endpoint_url = os.environ.get('MCP_ENDPOINT')
    if not endpoint_url:
        logger.error("Please set the `MCP_ENDPOINT` environment variable")
        sys.exit(1)
    
    # Reject any arguments; no CLI args are supported
    if len(sys.argv) >= 2:
        logger.error("This script takes no arguments. Run: python mcp_pipe.py")
        sys.exit(1)

    async def _main():
        cfg = load_config()
        servers = list((cfg.get("mcpServers") or {}).keys())
        if not servers:
            raise RuntimeError("No mcpServers found in config")
        logger.info(f"Starting all servers: {', '.join(servers)}")
        tasks = [asyncio.create_task(connect_with_retry(endpoint_url, t)) for t in servers]
        # Run all forever; if any crashes it will auto-retry inside
        await asyncio.gather(*tasks)

    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        logger.info("Program interrupted by user")
    except Exception as e:
        logger.error(f"Program execution error: {e}")