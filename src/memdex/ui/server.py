"""A small read-only HTTP server for the dashboard.

Bound to 127.0.0.1 and read-only by design: it shows what Memdex has already
recorded and cannot change anything. Written on the standard library so that
installing Memdex never pulls in a web framework.
"""

from __future__ import annotations

import json
import webbrowser
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from memdex.config import MemdexConfig
from memdex.errors import MemdexError
from memdex.models import CATEGORIES

DASHBOARD = Path(__file__).with_name("dashboard.html")


def collect_summary(cfg: MemdexConfig) -> dict:
    from memdex.pipeline import read_last_run
    from memdex.store import VectorStore

    last_run = read_last_run(cfg) or {}
    store = VectorStore(cfg)
    vectors = 0
    if store.exists():
        try:
            vectors = store.count()
        except MemdexError:
            vectors = 0

    files = sorted(cfg.abs_output_dir.rglob("*.md")) if cfg.abs_output_dir.is_dir() else []
    categories = last_run.get("categories") or {}
    if not categories:
        counts: dict[str, int] = {}
        for path in files:
            parent = path.parent.name
            if parent in CATEGORIES:
                counts[parent] = counts.get(parent, 0) + 1
        categories = counts

    return {
        "project": cfg.root.name,
        "project_path": str(cfg.root),
        "index": cfg.index_path,
        "memory_dir": cfg.output_dir,
        "files": len(files),
        "vectors": vectors,
        "memories": last_run.get("units", len(files)),
        "embedding": f"{cfg.embedding_provider} ({cfg.embedding_model})",
        "optimizer": cfg.optimizer_mode,
        "llm": cfg.llm.label if cfg.llm.is_usable else "disabled",
        "tokens_estimated": last_run.get("tokens_estimated", True),
        "last_run": last_run,
        "categories": categories,
    }


def collect_events(cfg: MemdexConfig) -> list[dict]:
    from memdex import audit as audit_log
    from memdex.store import VectorStore

    store = VectorStore(cfg)
    if not store.exists():
        return []
    events = audit_log.history(cfg, store)
    return [
        {
            "id": event.id,
            "event": event.event,
            "at": event.at,
            "summary": event.summary,
            "tokens_before": event.tokens_before,
            "tokens_after": event.tokens_after,
            "tokens_saved": event.tokens_saved,
            "pct_saved": round(event.pct_saved, 2),
            "files_written": event.files_written,
            "units": event.units,
            "vectors": event.vectors,
            "mode_label": event.mode_label,
        }
        for event in events
    ]


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "Memdex"

    def __init__(self, *args, cfg: MemdexConfig, **kwargs) -> None:
        self.cfg = cfg
        super().__init__(*args, **kwargs)

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003 - stdlib signature
        pass  # the CLI owns the terminal; request logs would only be noise

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload) -> None:
        self._send(200, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self) -> None:  # noqa: N802 - stdlib signature
        route = self.path.split("?", 1)[0].rstrip("/") or "/"
        if route == "/":
            try:
                html = DASHBOARD.read_bytes()
            except OSError:
                self._send(500, b"dashboard.html is missing", "text/plain; charset=utf-8")
                return
            self._send(200, html, "text/html; charset=utf-8")
        elif route == "/api/summary":
            self._json(collect_summary(self.cfg))
        elif route in ("/api/history", "/api/audit"):
            events = collect_events(self.cfg)
            if route == "/api/history":
                events = [e for e in events if e["tokens_before"]]
            self._json(events)
        else:
            self._send(404, b"not found", "text/plain; charset=utf-8")


def build_server(
    cfg: MemdexConfig, port: int, host: str = "127.0.0.1"
) -> ThreadingHTTPServer:
    handler = partial(DashboardHandler, cfg=cfg)
    try:
        return ThreadingHTTPServer((host, port), handler)
    except OSError as exc:
        raise MemdexError(
            f"Could not start the dashboard on {host}:{port} ({exc.strerror or exc}).",
            hint="Pass --port with a free port, or use `memdex history` in the terminal.",
        ) from exc


def serve(
    cfg: MemdexConfig, port: int, open_browser: bool = False, host: str = "127.0.0.1"
) -> None:
    from memdex import output

    server = build_server(cfg, port, host=host)
    shown_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    url = f"http://{shown_host}:{server.server_address[1]}"
    output.info(f"\n[head]Memdex dashboard[/head]  {url}")
    if host not in ("127.0.0.1", "localhost", "::1"):
        output.warn(
            f"Listening on {host} — the dashboard (read-only) is reachable from your network."
        )
    output.note("Read-only. Press Ctrl+C to stop.\n")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        output.note("\nDashboard stopped.\n")
    finally:
        server.server_close()
