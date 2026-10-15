from __future__ import annotations

import hashlib
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx
import yaml
from bs4 import BeautifulSoup

from migraai.kb.embed import Embedder, get_embedder
from migraai.kb.store import KBStore, connect, insert_chunk
from migraai.kb.validate import validate_seed_dir

MAX_LINKS_PER_SEED = 40
MAX_PAGES = 3000
MAX_PAGE_BYTES = 2 * 1024 * 1024
REQUEST_TIMEOUT = 20
REQUESTS_PER_HOST_PER_SECOND = 1
MAX_HOSTS_IN_PARALLEL = 4
USER_AGENT = "MigraAI-KB-Refresh/0.2 (+local; contact: admin)"


def normalize_url(url: str) -> str:
    parts = urlsplit(urldefrag(url)[0])
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def chunk_text(text: str, words: int = 375, overlap: int = 40) -> list[str]:
    tokens = text.split()
    if not tokens:
        return []
    result = []
    stride = max(1, words - overlap)
    for start in range(0, len(tokens), stride):
        result.append(" ".join(tokens[start : start + words]))
        if start + words >= len(tokens):
            break
    return result


class RefreshJob:
    def __init__(
        self,
        store: KBStore,
        seed_dir: Path,
        *,
        embedder: Embedder | None = None,
        audit=None,
    ) -> None:
        self.store = store
        self.seed_dir = seed_dir
        self.embedder = embedder
        self.audit = audit
        self.cancel_event = threading.Event()
        self.started = time.monotonic()
        self.progress = {
            "phase": "queued",
            "hosts": {},
            "pages_fetched": 0,
            "pages_skipped": 0,
            "pages_failed": 0,
            "chunks": 0,
            "elapsed": 0.0,
        }
        self.done = False
        self.error: str | None = None
        self.thread = threading.Thread(target=self.run, name="migraai-kb-refresh", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def cancel(self) -> None:
        self.cancel_event.set()

    def snapshot(self) -> dict:
        self.progress["elapsed"] = round(time.monotonic() - self.started, 2)
        hosts = self.progress["hosts"].values()
        for key in ("fetched", "skipped", "failed"):
            self.progress[f"pages_{key}"] = sum(host.get(key, 0) for host in hosts)
        return {**self.progress, "running": not self.done}

    def _audit(self, action: str, details: dict) -> None:
        if self.audit:
            self.audit(action, details)

    def run(self) -> None:
        partial: Path | None = None
        old_version = self.store.current_version
        try:
            self.progress["phase"] = "validating"
            errors, _ = validate_seed_dir(self.seed_dir, allow_incomplete=True)
            if errors:
                raise ValueError("; ".join(errors))
            current = self.store.current_path
            if old_version is None or current is None:
                raise RuntimeError("The KB must be initialized before refresh.")
            next_version = old_version + 1
            partial = self.store.version_path(next_version).with_suffix(".partial.db")
            with closing(connect(current)) as source, closing(connect(partial)) as destination:
                source.backup(destination)
            self._audit("kb.refresh.start", {})
            page_results: dict[str, tuple[str, str, str, str, str | None, str | None]] = {}
            with connect(current) as connection:
                previous = {
                    row["url"]: dict(row)
                    for row in connection.execute("SELECT * FROM documents WHERE origin='refresh'")
                }
            files = sorted((self.seed_dir / "sources").glob("*.yaml"))
            hosts: dict[str, list[tuple[str, dict]]] = {}
            for path in files:
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
                for seed in data.get("seeds", []):
                    hosts.setdefault(urlsplit(seed["url"]).netloc, []).append(
                        (data["platform"], seed)
                    )
            seen: set[str] = set()
            seen_lock = threading.Lock()
            page_counter = 0
            page_lock = threading.Lock()
            host_locks: dict[str, threading.Lock] = {}
            request_times: dict[str, float] = {}
            self.progress["hosts"] = {
                host: {"fetched": 0, "skipped": 0, "failed": 0} for host in hosts
            }
            self.progress["phase"] = "crawling"

            def request(host: str, url: str, headers: dict[str, str]) -> httpx.Response:
                lock = host_locks.setdefault(host, threading.Lock())
                with lock:
                    wait = (
                        1 / REQUESTS_PER_HOST_PER_SECOND if REQUESTS_PER_HOST_PER_SECOND > 0 else 0
                    )
                    remaining = wait - (time.monotonic() - request_times.get(host, 0))
                    if request_times.get(host) and remaining > 0:
                        time.sleep(remaining)
                    response = httpx.get(
                        url, headers=headers, timeout=REQUEST_TIMEOUT, follow_redirects=False
                    )
                    request_times[host] = time.monotonic()
                    return response

            def crawl_host(host: str, host_seeds: list[tuple[str, dict]]) -> None:
                nonlocal page_counter
                status = self.progress["hosts"][host]
                first_url = normalize_url(host_seeds[0][1]["url"])
                parsed = urlsplit(first_url)
                robots_url = f"{parsed.scheme}://{host}/robots.txt"
                try:
                    robots_response = request(host, robots_url, {"User-Agent": USER_AGENT})
                    robot_parser = RobotFileParser(robots_url)
                    robot_parser.parse(
                        robots_response.text.splitlines()
                        if robots_response.status_code == 200
                        else []
                    )
                except httpx.HTTPError:
                    robot_parser = RobotFileParser()
                    robot_parser.parse([])
                for platform, seed in host_seeds:
                    base = normalize_url(seed["url"])
                    if not urlsplit(base).path.startswith(seed["path_prefix"]):
                        status["failed"] += 1
                        continue
                    candidates = [base]
                    if not robot_parser.can_fetch(USER_AGENT, base):
                        status["skipped"] += 1
                        continue
                    for index, url in enumerate(candidates):
                        if self.cancel_event.is_set():
                            return
                        if not robot_parser.can_fetch(USER_AGENT, url):
                            status["skipped"] += 1
                            continue
                        with seen_lock:
                            if url in seen:
                                continue
                            seen.add(url)
                        with page_lock:
                            if page_counter >= MAX_PAGES:
                                status["skipped"] += 1
                                return
                            page_counter += 1
                        old = previous.get(url, {})
                        headers = {"User-Agent": USER_AGENT}
                        if old.get("etag"):
                            headers["If-None-Match"] = old["etag"]
                        if old.get("last_modified"):
                            headers["If-Modified-Since"] = old["last_modified"]
                        try:
                            response = request(host, url, headers)
                            if response.status_code == 304:
                                status["skipped"] += 1
                                continue
                            if response.status_code >= 400:
                                status["failed"] += 1
                                continue
                            if (
                                response.status_code != 200
                                or "text/html"
                                not in response.headers.get("content-type", "").casefold()
                            ):
                                status["skipped"] += 1
                                continue
                            body = response.content
                            if len(body) > MAX_PAGE_BYTES:
                                status["failed"] += 1
                                continue
                            digest = hashlib.sha256(body).hexdigest()
                            soup = BeautifulSoup(body, "html.parser")
                            title = soup.title.get_text(" ", strip=True) if soup.title else url
                            if index == 0:
                                for anchor in soup.find_all("a", href=True):
                                    link = normalize_url(urljoin(url, anchor["href"]))
                                    parts = urlsplit(link)
                                    if (
                                        parts.scheme in {"http", "https"}
                                        and parts.netloc == host
                                        and parts.path.startswith(seed["path_prefix"])
                                        and link not in seen
                                        and link not in candidates
                                        and len(candidates) - 1 < MAX_LINKS_PER_SEED
                                    ):
                                        candidates.append(link)
                            if old.get("sha256") == digest:
                                status["skipped"] += 1
                                continue
                            for tag in soup(["nav", "header", "footer", "script", "style"]):
                                tag.decompose()
                            page_results[url] = (
                                platform,
                                title,
                                soup.get_text(" ", strip=True),
                                digest,
                                response.headers.get("etag"),
                                response.headers.get("last-modified"),
                            )
                            status["fetched"] += 1
                        except httpx.HTTPError:
                            status["failed"] += 1

            with ThreadPoolExecutor(max_workers=MAX_HOSTS_IN_PARALLEL) as executor:
                futures = [
                    executor.submit(crawl_host, host, items) for host, items in hosts.items()
                ]
                for future in as_completed(futures):
                    if self.cancel_event.is_set():
                        break
                    future.result()
            if self.cancel_event.is_set():
                self._audit(
                    "kb.refresh.cancel",
                    {"fetched": sum(x["fetched"] for x in self.progress["hosts"].values())},
                )
                return
            chunks = [
                (url, platform, title, digest, etag, modified, text)
                for url, (platform, title, content, digest, etag, modified) in page_results.items()
                for text in chunk_text(content)
            ]
            self.progress["chunks"] = len(chunks)
            vectors = []
            if chunks:
                embedder = self.embedder or get_embedder()
                vectors = embedder.embed([item[6] for item in chunks])
            self.progress["phase"] = "indexing"
            with closing(connect(partial)) as connection:
                now = datetime.now(UTC).isoformat()
                for (url, platform, title, digest, etag, modified, text), vector in zip(
                    chunks, vectors, strict=True
                ):
                    old = previous.get(url)
                    document_id = old["id"] if old else str(uuid.uuid4())
                    if old:
                        old_chunks = connection.execute(
                            "SELECT id FROM chunks WHERE document_id=?", (document_id,)
                        ).fetchall()
                        for old_chunk in old_chunks:
                            connection.execute(
                                "DELETE FROM chunks_fts WHERE rowid=?", (old_chunk["id"],)
                            )
                            connection.execute(
                                "DELETE FROM chunks_vec WHERE rowid=?", (old_chunk["id"],)
                            )
                        connection.execute("DELETE FROM chunks WHERE document_id=?", (document_id,))
                        connection.execute(
                            """UPDATE documents SET title=?,fetched_at=?,platform=?,sha256=?,
                            etag=?,last_modified=? WHERE id=?""",
                            (title, now, platform, digest, etag, modified, document_id),
                        )
                    else:
                        connection.execute(
                            """INSERT INTO documents
                            (id,url,title,fetched_at,platform,sha256,etag,last_modified,origin)
                            VALUES(?,?,?,?,?,?,?,?,?)""",
                            (
                                document_id,
                                url,
                                title,
                                now,
                                platform,
                                digest,
                                etag,
                                modified,
                                "refresh",
                            ),
                        )
                    insert_chunk(
                        connection,
                        (
                            document_id,
                            text,
                            url,
                            now[:10],
                            platform,
                            None,
                            None,
                            "refresh",
                            None,
                            None,
                        ),
                        vector,
                    )
                document_count = connection.execute("SELECT count(*) FROM documents").fetchone()[0]
                chunk_count = connection.execute("SELECT count(*) FROM chunks").fetchone()[0]
                connection.execute(
                    "INSERT INTO kb_versions(version,created_at,documents,chunks) VALUES(?,?,?,?)",
                    (next_version, now, document_count, chunk_count),
                )
                connection.commit()
            if self.cancel_event.is_set():
                return
            final = self.store.version_path(next_version)
            partial.replace(final)
            if self.cancel_event.is_set():
                final.unlink(missing_ok=True)
                self._audit(
                    "kb.refresh.cancel",
                    {"fetched": sum(item["fetched"] for item in self.progress["hosts"].values())},
                )
                return
            hosts_progress = self.progress["hosts"].values()
            summary = {
                "fetched": sum(item["fetched"] for item in hosts_progress),
                "skipped": sum(item["skipped"] for item in hosts_progress),
                "failed": sum(item["failed"] for item in hosts_progress),
                "chunks": len(chunks),
            }
            self.store.activate(next_version, summary)
            self._audit("kb.refresh.done", summary)
        except Exception as error:
            self.error = str(error)
            self._audit("kb.refresh.fail", {"failed": 1})
        finally:
            if partial and partial.exists():
                partial.unlink(missing_ok=True)
            self.done = True
            self.progress["phase"] = "done"
