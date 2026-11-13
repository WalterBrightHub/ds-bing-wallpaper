"""Local web server for DS Bing Wallpaper (Python standard library only).

Binds to 127.0.0.1 and requires a per-process token on every mutating call, so
no other page or process can drive the app by accident.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import secrets
import socket
import sys
import threading
import urllib.parse
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from server import bing, config, scheduler, service, store, wallpaper  # type: ignore
else:
    from . import bing, config, scheduler, service, store, wallpaper

TOKEN = secrets.token_urlsafe(24)
MAX_BODY = 64 * 1024

STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".json": "application/json; charset=utf-8",
}


def _json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = f"DSBingWallpaper/{config.VERSION}"
    protocol_version = "HTTP/1.1"

    # -- helpers -------------------------------------------------

    def log_message(self, fmt: str, *args: Any) -> None:  # keep stdout calm
        if os.environ.get("DSBW_VERBOSE"):
            super().log_message(fmt, *args)

    def _send(self, status: int, body: bytes, content_type: str, extra: Optional[Dict[str, str]] = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, payload: Any, status: int = 200) -> None:
        self._send(status, _json_bytes(payload), "application/json; charset=utf-8")

    def _error(self, status: int, message: str) -> None:
        self._json({"ok": False, "error": message}, status)

    def _authorized(self, query: Dict[str, str]) -> bool:
        supplied = self.headers.get("X-DSBW-Token") or query.get("t", "")
        return bool(supplied) and secrets.compare_digest(supplied, TOKEN)

    def _read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > MAX_BODY:
            raise ValueError("请求体过大")
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except ValueError as exc:
            raise ValueError("请求体不是合法 JSON") from exc
        return data if isinstance(data, dict) else {}

    # -- routing -------------------------------------------------

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = dict(urllib.parse.parse_qsl(parsed.query))

        if path == "/api/health":
            self._json({"ok": True, "app": config.APP_NAME, "version": config.VERSION})
            return

        if path == "/" or path == "/index.html":
            self._serve_index()
            return

        if path == "/api/state":
            if not self._authorized(query):
                self._error(HTTPStatus.FORBIDDEN, "无效的访问令牌")
                return
            payload = service.status()
            payload["scheduler"] = _SCHEDULER.snapshot() if _SCHEDULER else None
            self._json(payload)
            return

        if path.startswith("/api/image/"):
            self._serve_image(path.rsplit("/", 1)[-1], query)
            return

        if path.startswith("/api/thumb/"):
            self._serve_image(path.rsplit("/", 1)[-1], {"size": "thumb", "t": query.get("t", "")})
            return

        if path.startswith("/api/preview/"):
            self._serve_image(path.rsplit("/", 1)[-1], {"size": "preview", "t": query.get("t", "")})
            return

        if path in ("/favicon.svg", "/styles.css", "/app.js", "/favicon.ico"):
            self._serve_static(path.lstrip("/"))
            return

        self._error(HTTPStatus.NOT_FOUND, "未找到")

    def do_POST(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = dict(urllib.parse.parse_qsl(parsed.query))

        if not self._authorized(query):
            self._error(HTTPStatus.FORBIDDEN, "无效的访问令牌")
            return

        try:
            body = self._read_json()
        except ValueError as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
            return

        try:
            if path == "/api/switch":
                image_id = str(body.get("id") or "")
                result = service.switch_to(image_id)
                self._json({"ok": True, **result, "status": service.status()})
                return

            if path == "/api/favorite":
                image_id = str(body.get("id") or "")
                updated = service.toggle_favorite(image_id)
                self._json({"ok": True, "image": updated, "status": service.status()})
                return

            if path in ("/api/auto-update", "/api/auto"):
                enabled = bool(body.get("enabled"))
                state = service.set_auto_update(enabled)
                result: Dict[str, Any] = {"ok": True, "autoUpdate": state["settings"]["autoUpdate"]}
                if enabled:
                    result["sync"] = service.sync(trigger="auto-enabled")
                self._json({**result, "status": service.status()})
                return

            if path == "/api/refresh":
                result = service.refresh_now()
                self._json({"ok": bool(result.get("ok")), "sync": result, "status": service.status()})
                return

            if path == "/api/apply-current":
                image = store.get_image(str(body.get("id") or ""))
                if not image:
                    raise KeyError("no such image")
                ok, message = wallpaper.set_wallpaper(store.file_path(image))
                self._json({"ok": ok, "message": message, "status": service.status()})
                return
        except KeyError:
            self._error(HTTPStatus.NOT_FOUND, "壁纸不存在")
            return
        except FileNotFoundError:
            self._error(HTTPStatus.CONFLICT, "壁纸文件缺失，请刷新图库")
            return
        except Exception as exc:  # defensive: keep the server up
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, f"{exc}")
            return

        self._error(HTTPStatus.NOT_FOUND, "未找到")

    # -- static --------------------------------------------------

    def _serve_index(self) -> None:
        index = config.WEB_DIR / "index.html"
        try:
            html = index.read_text(encoding="utf-8")
        except OSError:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "缺少 web/index.html")
            return
        html = html.replace("__DSBW_TOKEN__", TOKEN).replace("__DSBW_VERSION__", config.VERSION)
        self._send(HTTPStatus.OK, html.encode("utf-8"), STATIC_TYPES[".html"])

    def _serve_static(self, name: str) -> None:
        target = (config.WEB_DIR / name).resolve()
        if config.WEB_DIR.resolve() not in target.parents and target.parent != config.WEB_DIR.resolve():
            self._error(HTTPStatus.FORBIDDEN, "非法路径")
            return
        if not target.is_file():
            self._error(HTTPStatus.NOT_FOUND, "未找到")
            return
        suffix = target.suffix.lower()
        ctype = STATIC_TYPES.get(suffix) or mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        body = target.read_bytes()
        self._send(HTTPStatus.OK, body, ctype, {"Cache-Control": "no-cache"})

    def _serve_image(self, image_id: str, query: Dict[str, str]) -> None:
        if not self._authorized(query):
            self._error(HTTPStatus.FORBIDDEN, "无效的访问令牌")
            return
        record = store.get_image(image_id)
        if not record:
            self._error(HTTPStatus.NOT_FOUND, "壁纸不存在")
            return
        size = query.get("size")
        if size == "thumb":
            path = store.thumb_path(record)
        elif size == "preview":
            path = store.preview_path(record)
        else:
            path = store.file_path(record)
        if not path.is_file() and size:
            path = store.file_path(record)
        if not path.is_file():
            self._error(HTTPStatus.NOT_FOUND, "文件缺失")
            return

        try:
            stat = path.stat()
            size = stat.st_size
            etag = f'"{int(stat.st_mtime)}-{size}"'
            if self.headers.get("If-None-Match") == etag:
                self.send_response(HTTPStatus.NOT_MODIFIED)
                self.send_header("ETag", etag)
                self.end_headers()
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(size))
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            if self.command == "HEAD":
                return
            with path.open("rb") as handle:
                while True:
                    chunk = handle.read(256 * 1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except (OSError, BrokenPipeError):
            pass


_SCHEDULER: Optional[scheduler.Scheduler] = None


def pick_port(preferred: int, tries: int = 12) -> int:
    for port in range(preferred, preferred + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise SystemExit(f"端口 {preferred}-{preferred + tries - 1} 都被占用")


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="DS Bing Wallpaper local server")
    parser.add_argument("--port", type=int, default=config.DEFAULT_PORT)
    parser.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--no-scheduler", action="store_true", help="只提供 Web 界面，不启动每小时任务")
    args = parser.parse_args(argv)

    config.ensure_dirs()
    config.DATA_DIR.joinpath("server-token").write_text(TOKEN, encoding="utf-8")

    global _SCHEDULER
    if not args.no_scheduler:
        _SCHEDULER = scheduler.Scheduler()
        _SCHEDULER.start_soon(1.5)

    port = pick_port(args.port)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    httpd.daemon_threads = True
    url = f"http://127.0.0.1:{port}/"
    config.DATA_DIR.joinpath("server-url").write_text(url, encoding="utf-8")
    scheduler.log(f"server listening on {url} (pid={os.getpid()})")

    if not args.no_open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if _SCHEDULER:
            _SCHEDULER.stop()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
