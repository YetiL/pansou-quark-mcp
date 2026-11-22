import json
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sync import (
    collect_media_files,
    fetch_tmdb_info,
    get_stoken,
    save_and_rename,
    search_pansou,
    select_best_with_llm,
)

TOOLS = [
    {
        "name": "save_media_to_quark",
        "description": "Search a movie or TV show via pansou, match TMDB metadata, verify Quark links, pick the best version, save to Quark Netdisk, and auto-rename for media library scraping.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Movie or TV show title"},
                "type": {"type": "string", "enum": ["auto", "movie", "tv"], "default": "auto"},
                "season": {"type": "integer", "default": 1},
            },
            "required": ["query"],
        },
    }
]


def run_save_media(args: dict) -> dict:
    query = args.get("query", "")
    mtype = args.get("type", "auto")
    season = int(args.get("season", 1))
    cookie = os.environ.get("QUARK_COOKIE", "")
    pansou_url = os.environ.get("PANSOU_URL", "")
    prefer = [x.strip() for x in os.environ.get("PREFER_KEYWORDS", "").split(",") if x.strip()]
    exclude = [x.strip() for x in os.environ.get("EXCLUDE_KEYWORDS", "").split(",") if x.strip()]

    tmdb = fetch_tmdb_info(query, mtype, os.environ.get("TMDB_API_KEY", ""))
    raw_candidates = search_pansou(pansou_url, query, prefer, exclude)

    alive = []
    errors = []
    for c in raw_candidates[:8]:
        try:
            stoken = get_stoken(c["pwd_id"], c["passcode"], cookie)
            if not stoken:
                errors.append(f"{c['pwd_id']}: empty stoken")
                continue
            media_files = collect_media_files(c["pwd_id"], stoken, cookie)
            if media_files:
                c["stoken"] = stoken
                c["media_files"] = media_files
                c["file_count"] = len(media_files)
                c["sample_file"] = media_files[0].get("file_name", "")
                alive.append(c)
                if len(alive) >= 5:
                    break
            else:
                errors.append(f"{c['pwd_id']}: no media files")
        except PermissionError:
            raise
        except Exception as e:
            errors.append(f"{c['pwd_id']}: {e}")
            continue

    if not alive:
        return {"status": "error", "message": f"未找到 '{query}' 的有效夸克资源", "raw_count": len(raw_candidates), "errors": errors[:3]}

    best_idx = select_best_with_llm(query, tmdb, alive)
    return save_and_rename(alive[best_idx], tmdb, season, cookie)


class handler(BaseHTTPRequestHandler):
    def _send(self, code: int, data: dict):
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def do_GET(self):
        return self._send(200, {"status": "ok", "service": "pansou-quark-mcp"})

    def do_POST(self):
        secret = os.environ.get("API_SECRET", "")
        if secret and self.headers.get("Authorization") != f"Bearer {secret}":
            return self._send(401, {"error": "Unauthorized"})

        try:
            length = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(length).decode("utf-8"))
            rpc_id = req.get("id")
            method = req.get("method", "")
            params = req.get("params", {})

            if method == "initialize":
                return self._send(200, {
                    "jsonrpc": "2.0",
                    "id": rpc_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "pansou-quark-mcp", "version": "1.0.0"},
                    },
                })
            if method == "tools/list":
                return self._send(200, {"jsonrpc": "2.0", "id": rpc_id, "result": {"tools": TOOLS}})
            if method == "tools/call":
                name = params.get("name")
                if name != "save_media_to_quark":
                    return self._send(200, {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32601, "message": "Unknown tool"}})
                try:
                    res = run_save_media(params.get("arguments", {}))
                    return self._send(200, {
                        "jsonrpc": "2.0",
                        "id": rpc_id,
                        "result": {"content": [{"type": "text", "text": json.dumps(res, ensure_ascii=False)}]},
                    })
                except Exception as tool_err:
                    return self._send(200, {
                        "jsonrpc": "2.0",
                        "id": rpc_id,
                        "result": {"isError": True, "content": [{"type": "text", "text": str(tool_err)}]},
                    })

            return self._send(200, {"jsonrpc": "2.0", "id": rpc_id, "result": {}})
        except Exception as e:
            return self._send(500, {"jsonrpc": "2.0", "id": None, "error": {"code": -32000, "message": str(e)}})
