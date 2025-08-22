"""
Simple MCP stdio <-> WebSocket pipe with optional unified config.
Version: 0.2.3

Usage (env):
    export MCP_ENDPOINT=<ws_endpoint>
    # Windows (PowerShell): $env:MCP_ENDPOINT = "<ws_endpoint>"

Start server process(es) from config:
    python mcp_pipe.py           # Run all configured servers
    python mcp_pipe.py script.py # Run a local script

Config discovery order:
    $MCP_CONFIG, then ./mcp_config.json
"""

import asyncio
import websockets
import subprocess
import logging
import os
import signal
import sys
import json
import re
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('MCP_PIPE')

INITIAL_BACKOFF = 1
MAX_BACKOFF = 600


async def connect_with_retry(uri, target):
    reconnect_attempt = 0
    backoff = INITIAL_BACKOFF
    while True:
        try:
            if reconnect_attempt > 0:
                logger.info(f"[{target}] Waiting {backoff}s before reconnection attempt {reconnect_attempt}...")
                await asyncio.sleep(backoff)
            await connect_to_server(uri, target)
        except Exception as e:
            reconnect_attempt += 1
            logger.warning(f"[{target}] Connection closed (attempt {reconnect_attempt}): {e}")
            backoff = min(backoff * 2, MAX_BACKOFF)


async def connect_to_server(uri, target):
    try:
        logger.info(f"[{target}] Connecting to WebSocket server...")
        async with websockets.connect(uri) as websocket:
            logger.info(f"[{target}] Successfully connected to WebSocket server")

            # Load initMessage from config (if any)
            cfg = load_config()
            entry = (cfg.get("mcpServers") or {}).get(target, {})
            init_msg = entry.get("initMessage")
            if init_msg:
                try:
                    await websocket.send(json.dumps(init_msg))
                    logger.info(f"[{target}] Sent initMessage: {init_msg}")
                except Exception as e:
                    logger.warning(f"[{target}] Failed to send initMessage: {e}")

            # Get command or run noop if ws type
            cmd, env = build_server_command(target)
            if cmd == ["noop"]:
                logger.info(f"[{target}] No-op bridge active, holding connection...")
                await asyncio.Future()
                return

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
            await asyncio.gather(
                pipe_websocket_to_process(websocket, process, target),
                pipe_process_to_websocket(process, websocket, target),
                pipe_process_stderr_to_terminal(process, target)
            )
    except websockets.exceptions.ConnectionClosed as e:
        logger.error(f"[{target}] WebSocket connection closed: {e}")
        raise
    except Exception as e:
        logger.error(f"[{target}] Connection error: {e}")
        raise
    finally:
        if 'process' in locals():
            logger.info(f"[{target}] Terminating server process")
            try:
                process.terminate()
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
            logger.info(f"[{target}] Server process terminated")


async def pipe_websocket_to_process(websocket, process, target):
    try:
        while True:
            message = await websocket.recv()
            logger.debug(f"[{target}] << {message[:120]}...")
            if isinstance(message, bytes):
                message = message.decode('utf-8')
            process.stdin.write(message + '\n')
            process.stdin.flush()
    except Exception as e:
        logger.error(f"[{target}] Error in WebSocket to process pipe: {e}")
        raise
    finally:
        if not process.stdin.closed:
            process.stdin.close()


async def pipe_process_to_websocket(process, websocket, target):
    try:
        while True:
            data = await asyncio.to_thread(process.stdout.readline)
            if not data:
                logger.info(f"[{target}] Process has ended output")
                break
            logger.debug(f"[{target}] >> {data[:120]}...")
            await websocket.send(data)
    except Exception as e:
        logger.error(f"[{target}] Error in process to WebSocket pipe: {e}")
        raise


async def pipe_process_stderr_to_terminal(process, target):
    try:
        while True:
            data = await asyncio.to_thread(process.stderr.readline)
            if not data:
                logger.info(f"[{target}] Process has ended stderr output")
                break
            sys.stderr.write(data)
            sys.stderr.flush()
    except Exception as e:
        logger.error(f"[{target}] Error in process stderr pipe: {e}")
        raise


def signal_handler(sig, frame):
    logger.info("Received interrupt signal, shutting down...")
    sys.exit(0)


def substitute_env_vars(obj):
    if isinstance(obj, dict):
        return {k: substitute_env_vars(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [substitute_env_vars(i) for i in obj]
    elif isinstance(obj, str):
        return re.sub(r"\$\{([^}]+)\}", lambda m: os.environ.get(m.group(1), ""), obj)
    else:
        return obj


def load_config():
    path = os.environ.get("MCP_CONFIG") or os.path.join(os.getcwd(), "mcp_config.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return substitute_env_vars(json.load(f))
    except Exception as e:
        logger.warning(f"Failed to load config {path}: {e}")
        return {}


def build_server_command(target=None):
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
            cmd = [sys.executable, "-m", "mcp_proxy"]
            if typ in ("http", "streamablehttp"):
                cmd += ["--transport", "streamablehttp"]
            for hk, hv in (entry.get("headers") or {}).items():
                cmd += ["-H", hk, str(hv)]
            cmd.append(url)
            return cmd, child_env

        if typ == "ws":
            return ["noop"], child_env

        raise RuntimeError(f"Unsupported server type: {typ}")

    if os.path.exists(target):
        return [sys.executable, target], os.environ.copy()
    else:
        raise RuntimeError(f"'{target}' is neither a configured server nor an existing script")


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler)
    endpoint_url = os.environ.get('MCP_ENDPOINT')
    target_arg = sys.argv[1] if len(sys.argv) >= 2 else None

    async def _main():
        if not target_arg:
            cfg = load_config()
            servers_cfg = cfg.get("mcpServers", {})
            all_servers = list(servers_cfg.keys())
            enabled = [name for name, entry in servers_cfg.items() if not (entry or {}).get("disabled")]
            skipped = [name for name in all_servers if name not in enabled]
            if skipped:
                logger.info(f"Skipping disabled servers: {', '.join(skipped)}")
            if not enabled:
                raise RuntimeError("No enabled mcpServers found in config")

            logger.info(f"Starting servers: {', '.join(enabled)}")
            tasks = []
            for t in enabled:
                entry = servers_cfg[t]
                typ = (entry.get("type") or entry.get("transportType") or "stdio").lower()
                uri = entry.get("url") if typ == "ws" else endpoint_url
                if not uri:
                    logger.warning(f"[{t}] Missing connection URL for type '{typ}'")
                    continue
                tasks.append(asyncio.create_task(connect_with_retry(uri, t)))
            await asyncio.gather(*tasks)
        else:
            if os.path.exists(target_arg):
                if not endpoint_url:
                    logger.error("Please set the `MCP_ENDPOINT` environment variable")
                    sys.exit(1)
                await connect_with_retry(endpoint_url, target_arg)
            else:
                logger.error("Invalid target. Use script path or run with no arguments.")
                sys.exit(1)

    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        logger.info("Program interrupted by user")
    except Exception as e:
        logger.error(f"Program execution error: {e}")
