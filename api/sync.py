import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler

VIDEO_EXTS = {".mp4", ".mkv", ".ts", ".avi", ".mov", ".m2ts", ".flv", ".wmv", ".iso"}
SUB_EXTS = {".srt", ".ass", ".ssa", ".vtt", ".sub", ".idx"}
ALLOWED_EXTS = VIDEO_EXTS | SUB_EXTS

QUARK_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://pan.quark.cn/",
    "Origin": "https://pan.quark.cn",
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
}


def http_json(url: str, method: str = "GET", headers: dict = None, body: dict = None, timeout: int = 15) -> dict:
    hdrs = headers.copy() if headers else {}
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="ignore")
        if e.code in (401, 403):
            raise PermissionError(f"HTTP {e.code}: {raw[:200]}")
        raise RuntimeError(f"HTTP {e.code}: {raw[:200]}")


def call_llm(prompt: str) -> dict:
    base_url = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    api_key = os.environ.get("LLM_API_KEY", "")
    model = os.environ.get("LLM_MODEL", "gpt-4o-mini")
    if not api_key:
        raise RuntimeError("未配置 LLM_API_KEY")

    res = http_json(
        f"{base_url}/chat/completions",
        method="POST",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        body={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
        },
        timeout=20,
    )
    content = res["choices"][0]["message"].get("content") or "{}"
    content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.IGNORECASE)
    return json.loads(content)


def parse_intent_with_llm(text: str) -> dict:
    prompt = (
        f"从用户输入中提取影视搜索参数，仅返回 JSON："
        f'{{"query": "纯片名", "type": "movie或tv或auto", "season": 1}}。'
        f"用户输入：{text}"
    )
    return call_llm(prompt)


def select_best_with_llm(user_text: str, tmdb: dict, alive_candidates: list) -> int:
    if len(alive_candidates) == 1:
        return 0
    compact = [
        {
            "index": i,
            "title": c["title"],
            "files": c["file_count"],
            "sample": c["sample_file"],
        }
        for i, c in enumerate(alive_candidates)
    ]
    prompt = (
        f"用户需求：{user_text}\n"
        f"TMDB目标：{tmdb['title']} ({tmdb['year']}) 类型:{tmdb['type']}\n"
        f"候选资源：{json.dumps(compact, ensure_ascii=False)}\n"
        f"选出最匹配且适合 MacBook/iPhone 302 直链播放的一个（排除合集、同名错片、40GB+原盘/REMUX；优先 4K WEB-DL 或高码率 1080p，优先 .mp4 封装），仅返回 JSON：{{\"index\": 0}}"
    )
    try:
        res = call_llm(prompt)
        idx = int(res.get("index", 0))
        return idx if 0 <= idx < len(alive_candidates) else 0
    except Exception:
        return 0


# ---------------- TMDB & Pansou ----------------
def fetch_tmdb_info(query: str, media_type: str, api_key: str) -> dict:
    if not api_key:
        return {"title": query, "year": "", "type": "movie" if media_type == "movie" else "tv"}
    endpoint = "search/multi" if media_type == "auto" else f"search/{media_type}"
    params = urllib.parse.urlencode({"query": query, "language": "zh-CN", "include_adult": "false"})
    headers = {"Accept": "application/json"}
    if len(api_key) > 40:
        headers["Authorization"] = f"Bearer {api_key}"
        url = f"https://api.themoviedb.org/3/{endpoint}?{params}"
    else:
        url = f"https://api.themoviedb.org/3/{endpoint}?{params}&api_key={urllib.parse.quote(api_key)}"
    try:
        res = http_json(url, headers=headers)
        results = [r for r in res.get("results", []) if r.get("media_type", media_type) in ("movie", "tv")]
        if not results:
            return {"title": query, "year": "", "type": "movie" if media_type == "movie" else "tv"}
        top = results[0]
        mtype = top.get("media_type", media_type if media_type != "auto" else "movie")
        title = top.get("title") or top.get("name") or query
        date = top.get("release_date") or top.get("first_air_date") or ""
        return {"title": title, "year": date[:4] if len(date) >= 4 else "", "type": mtype}
    except Exception:
        return {"title": query, "year": "", "type": "movie" if media_type == "movie" else "tv"}


def search_pansou(pansou_url: str, query: str, prefer: list, exclude: list) -> list:
    url = f"{pansou_url.rstrip('/')}/api/search?kw={urllib.parse.quote(query)}"
    res = http_json(url)
    items = []
    data = res.get("data", res)
    if isinstance(data, dict):
        items = data.get("merged_by_type", {}).get("quark", [])
        if not items:
            for v in data.values():
                if isinstance(v, list):
                    items.extend(v)
    elif isinstance(data, list):
        items = data

    candidates, seen = [], set()
    for item in items:
        link = item.get("url") or item.get("link") or ""
        title = item.get("note") or item.get("title") or item.get("name") or ""
        pwd = item.get("password") or item.get("pwd") or ""
        m = re.search(r"pan\.quark\.cn/s/([a-zA-Z0-9]+)", link)
        if not m:
            continue
        pwd_id = m.group(1)
        if pwd_id in seen:
            continue
        seen.add(pwd_id)
        tl = title.lower()
        if any(ex.lower() in tl for ex in exclude):
            continue
        score = sum(10 for p in prefer if p.lower() in tl) + (5 if ("4k" in tl or "2160p" in tl) else 0)
        candidates.append({"title": title.strip() or link, "url": link, "pwd_id": pwd_id, "passcode": pwd, "score": score})

    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates


# ---------------- Quark API ----------------
def quark_req(path: str, cookie: str, method: str = "GET", params: dict = None, body: dict = None) -> dict:
    hdrs = QUARK_HEADERS.copy()
    hdrs["Cookie"] = cookie.replace("\\r", "").replace("\\n", "").replace("\r", "").replace("\n", "").strip()
    url = f"https://drive-pc.quark.cn/1/clouddrive/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    res = http_json(url, method=method, headers=hdrs, body=body)
    if res.get("status") == 401 or res.get("code") in (31001, 31002):
        raise PermissionError("Quark Cookie 已失效")
    return res


def get_stoken(pwd_id: str, passcode: str, cookie: str) -> str:
    res = quark_req("share/sharepage/token", cookie, "POST", {"pr": "ucpro", "fr": "pc"}, {"pwd_id": pwd_id, "passcode": passcode or ""})
    return res.get("data", {}).get("stoken", "")


def collect_media_files(pwd_id: str, stoken: str, cookie: str, pdir_fid: str = "0", depth: int = 0) -> list:
    if depth > 3:
        return []
    res = quark_req("share/sharepage/detail", cookie, "GET", {"pr": "ucpro", "fr": "pc", "pwd_id": pwd_id, "stoken": stoken, "pdir_fid": pdir_fid, "_page": 1, "_size": 100, "_fetch_total": 1})
    media = []
    for it in res.get("data", {}).get("list", []):
        if it.get("dir"):
            media.extend(collect_media_files(pwd_id, stoken, cookie, it["fid"], depth + 1))
        elif os.path.splitext(it.get("file_name", ""))[1].lower() in ALLOWED_EXTS:
            media.append(it)
    return media


def ensure_quark_dir(path_str: str, cookie: str) -> str:
    curr_fid = "0"
    for part in [p for p in path_str.strip("/").split("/") if p]:
        res = quark_req("file/sort", cookie, "GET", {"pr": "ucpro", "fr": "pc", "pdir_fid": curr_fid, "_page": 1, "_size": 100})
        found = next((it["fid"] for it in res.get("data", {}).get("list", []) if it.get("dir") and it.get("file_name") == part), None)
        if found:
            curr_fid = found
        else:
            mk = quark_req("file", cookie, "POST", {"pr": "ucpro", "fr": "pc"}, {"pdir_fid": curr_fid, "file_name": part, "dir_path": "", "dir_init_lock": False})
            curr_fid = mk.get("data", {}).get("fid")
    return curr_fid


def save_and_rename(chosen: dict, tmdb: dict, season: int, cookie: str) -> dict:
    year_str = f" ({tmdb['year']})" if tmdb["year"] else ""
    if tmdb["type"] == "movie":
        target_dir = f"{os.environ.get('MOVIE_DIR', '/影视库/电影').rstrip('/')}/{tmdb['title']}{year_str}"
    else:
        target_dir = f"{os.environ.get('TV_DIR', '/影视库/剧集').rstrip('/')}/{tmdb['title']}{year_str}/Season {str(season).zfill(2)}"

    to_fid = ensure_quark_dir(target_dir, cookie)
    files = chosen["media_files"]
    res = quark_req(
        "share/sharepage/save",
        cookie,
        "POST",
        {"pr": "ucpro", "fr": "pc"},
        {
            "fid_list": [f["fid"] for f in files],
            "fid_token_list": [f["share_fid_token"] for f in files],
            "to_pdir_fid": to_fid,
            "pwd_id": chosen["pwd_id"],
            "stoken": chosen["stoken"],
            "pdir_fid": "0",
            "scene": "link",
        },
    )
    task_id = res.get("data", {}).get("task_id")
    if task_id:
        for _ in range(30):
            t = quark_req("task", cookie, "GET", {"pr": "ucpro", "fr": "pc", "task_id": task_id, "retry_index": 0})
            if t.get("data", {}).get("status") == 2:
                break
            time.sleep(0.4)

    items = quark_req("file/sort", cookie, "GET", {"pr": "ucpro", "fr": "pc", "pdir_fid": to_fid, "_page": 1, "_size": 200}).get("data", {}).get("list", [])
    from concurrent.futures import ThreadPoolExecutor

    tasks = []
    for it in items:
        if it.get("dir"):
            continue
        old_name = it.get("file_name", "")
        ext = os.path.splitext(old_name)[1].lower()
        if ext not in ALLOWED_EXTS:
            continue
        if tmdb["type"] == "movie":
            new_name = f"{tmdb['title']}{year_str}{ext}"
        else:
            m = re.search(r"[Ss]\d{1,2}[Ee](\d{1,3})|[Ee][Pp]?(\d{1,3})|第\s*(\d{1,3})\s*[集话期]|[\[\s\-_](\d{2,3})[\]\s\-_\.]", old_name)
            ep = next((g for g in m.groups() if g), "") if m else ""
            if not ep:
                continue
            new_name = f"{tmdb['title']} - S{str(season).zfill(2)}E{ep.zfill(2)}{ext}"
        if new_name != old_name:
            tasks.append((it["fid"], new_name))

    def _do_rename(pair):
        fid, name = pair
        try:
            quark_req("file/rename", cookie, "POST", {"pr": "ucpro", "fr": "pc"}, {"fid": fid, "file_name": name})
            return 1
        except Exception:
            return 0

    renamed = 0
    if tasks:
        with ThreadPoolExecutor(max_workers=10) as pool:
            renamed = sum(pool.map(_do_rename, tasks))

    return {"status": "ok", "target_dir": target_dir, "saved_count": len(files), "renamed_count": renamed, "source_title": chosen["title"]}


class handler(BaseHTTPRequestHandler):
    def _send(self, code: int, data: dict):
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def do_POST(self):
        secret = os.environ.get("API_SECRET", "")
        if secret and self.headers.get("Authorization") != f"Bearer {secret}":
            return self._send(401, {"status": "error", "message": "Unauthorized"})

        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            user_text = body.get("text") or body.get("query") or ""
            if not user_text:
                return self._send(400, {"status": "error", "message": "缺少 text 或 query 参数"})

            cookie = os.environ.get("QUARK_COOKIE", "")
            pansou_url = os.environ.get("PANSOU_URL", "")
            prefer = [x.strip() for x in os.environ.get("PREFER_KEYWORDS", "").split(",") if x.strip()]
            exclude = [x.strip() for x in os.environ.get("EXCLUDE_KEYWORDS", "").split(",") if x.strip()]

            intent = parse_intent_with_llm(user_text)
            query = intent.get("query", user_text)
            mtype = intent.get("type", "auto")
            season = int(intent.get("season", 1))

            tmdb = fetch_tmdb_info(query, mtype, os.environ.get("TMDB_API_KEY", ""))
            raw_candidates = search_pansou(pansou_url, query, prefer, exclude)

            # 对 Top 8 进行夸克验活，保留前 5 个有效资源
            alive = []
            for c in raw_candidates[:8]:
                try:
                    stoken = get_stoken(c["pwd_id"], c["passcode"], cookie)
                    if not stoken:
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
                except PermissionError:
                    raise
                except Exception:
                    continue

            if not alive:
                return self._send(200, {"status": "error", "message": f"未找到 '{query}' 的有效夸克资源"})

            best_idx = select_best_with_llm(user_text, tmdb, alive)
            result = save_and_rename(alive[best_idx], tmdb, season, cookie)
            return self._send(200, result)

        except PermissionError as e:
            return self._send(401, {"status": "auth_error", "message": str(e)})
        except Exception as e:
            return self._send(500, {"status": "error", "message": str(e)})
