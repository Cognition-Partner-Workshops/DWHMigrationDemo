from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

import migraai.kb.refresh as refresh_module
from migraai.kb.embed import HashEmbedder
from migraai.kb.refresh import RefreshJob
from migraai.kb.store import KBStore, connect


class CountingEmbedder:
    dim = 384

    def __init__(self) -> None:
        self.calls = 0
        self.delegate = HashEmbedder()

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        return self.delegate.embed(texts)


@pytest.fixture
def fixture_site():
    requests: list[tuple[str, str | None]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.path, self.headers.get("If-None-Match")))
            if self.path == "/robots.txt":
                body = b"User-agent: *\nDisallow: /docs/blocked\n"
                content_type = "text/plain"
                status = 200
            elif self.path == "/docs/start":
                body = (
                    b"<html><nav>REMOVE NAV</nav><main>start content "
                    b'<a href="/docs/child?q=one#frag">child</a>'
                    b'<a href="/docs/child">duplicate</a>'
                    b'<a href="/outside/page">outside</a>'
                    b'<a href="/docs/blocked/page">blocked</a>'
                    b'<a href="/docs/nonhtml">pdf</a>'
                    b'<a href="/docs/fail">failure</a>'
                    b"</main></html>"
                )
                content_type = "text/html"
                status = 200
            elif self.path == "/docs/child":
                if self.headers.get("If-None-Match") == '"child-v1"':
                    self.send_response(304)
                    self.end_headers()
                    return
                body = (
                    b'<html><main>child content <a href="/docs/grandchild">deep</a></main></html>'
                )
                content_type = "text/html"
                status = 200
            elif self.path == "/docs/nonhtml":
                body, content_type, status = b"pdf", "application/pdf", 200
            elif self.path == "/docs/fail":
                body, content_type, status = b"failed", "text/plain", 500
            else:
                body, content_type, status = b"not found", "text/plain", 404
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            if self.path == "/docs/child":
                self.send_header("ETag", '"child-v1"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def prepare_job(
    tmp_path: Path, root: str, monkeypatch, embedder=None
) -> tuple[KBStore, Path, RefreshJob]:
    monkeypatch.setattr(refresh_module, "validate_seed_dir", lambda *args, **kwargs: ([], set()))
    monkeypatch.setattr(refresh_module, "REQUESTS_PER_HOST_PER_SECOND", 0)
    seed_dir = tmp_path / "seed"
    sources = seed_dir / "sources"
    sources.mkdir(parents=True)
    (sources / "teradata.yaml").write_text(
        f"platform: teradata\nallowed_hosts: [127.0.0.1]\nseeds:\n"
        f"  - url: {root}/docs/start\n    path_prefix: /docs/\n    topic: fixture\n",
        encoding="utf-8",
    )
    store = KBStore(tmp_path / "data")
    store.create_empty()
    return store, seed_dir, RefreshJob(store, seed_dir, embedder=embedder or HashEmbedder())


def test_refresh_honors_robots_depth_prefix_dedupe_and_content_type(
    fixture_site, tmp_path: Path, monkeypatch
) -> None:
    root, requests = fixture_site
    monkeypatch.setattr(refresh_module, "MAX_LINKS_PER_SEED", 20)
    store, _, job = prepare_job(tmp_path, root, monkeypatch)

    job.run()
    assert job.error is None, job.error
    progress = job.snapshot()

    with connect(store.current_path) as connection:
        urls = {row["url"] for row in connection.execute("SELECT url FROM documents")}
        texts = [row["text"] for row in connection.execute("SELECT text FROM chunks")]
    paths = [path for path, _ in requests]
    assert store.current_version == 2
    assert f"{root}/docs/start" in urls
    assert f"{root}/docs/child" in urls
    assert not any("grandchild" in url for url in urls)
    assert not any(path.startswith("/outside") or path.startswith("/blocked") for path in paths)
    assert paths.count("/docs/child") == 1
    assert "REMOVE NAV" not in " ".join(texts)
    host = next(iter(progress["hosts"].values()))
    assert host == {"fetched": 2, "skipped": 2, "failed": 1}


def test_refresh_link_cap_and_etag_reuse_avoid_reembedding(
    fixture_site, tmp_path: Path, monkeypatch
) -> None:
    root, requests = fixture_site
    monkeypatch.setattr(refresh_module, "MAX_LINKS_PER_SEED", 1)
    embedder = CountingEmbedder()
    store, seed_dir, job = prepare_job(tmp_path, root, monkeypatch, embedder)

    job.run()
    assert job.error is None, job.error
    with connect(store.current_path) as connection:
        original_ids = [row["id"] for row in connection.execute("SELECT id FROM chunks")]
    calls_after_first = embedder.calls
    second = RefreshJob(store, seed_dir, embedder=embedder)
    second.run()
    assert second.error is None, second.error

    assert embedder.calls == calls_after_first
    assert any(path == "/docs/child" and etag == '"child-v1"' for path, etag in requests)
    with connect(store.current_path) as connection:
        assert [row["id"] for row in connection.execute("SELECT id FROM chunks")] == original_ids


def test_cancelled_refresh_preserves_active_version_and_removes_partial(
    fixture_site, tmp_path: Path, monkeypatch
) -> None:
    root, _ = fixture_site
    store, _, job = prepare_job(tmp_path, root, monkeypatch)
    job.cancel()

    job.run()

    assert store.current_version == 1
    assert not store.version_path(2).exists()
    assert not list(store.root.glob("*.partial.db"))
