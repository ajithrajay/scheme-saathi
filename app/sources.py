import difflib
import hashlib
import io
import ipaddress
import json
import re
import socket
from urllib.parse import urlparse, urljoin
from pathlib import Path
import httpx
from bs4 import BeautifulSoup
from pypdf import PdfReader
from .config import ROOT
from .store import now

def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()

def seed(store):
    # The bundled Tamil/English catalogue is UTF-8 on every platform.
    data = json.loads((ROOT / "app/data/catalogue.json").read_text(encoding="utf-8"))
    for source in data["sources"]:
        if not store.get("source", source["id"]):
            source["hash"] = digest(source["text"])
            source["last_fetch_at"] = None
            store.put("source", source["id"], source)
            store.put("source_version", source["id"] + ":" + source["hash"], source)
    for scheme in data["schemes"]:
        if not store.get("scheme", scheme["id"]):
            scheme["source_hashes"] = {s: store.get("source", s)["hash"] for s in scheme["source_ids"]}
            store.put("scheme", scheme["id"], scheme)
    # Upgrade shipped entry routes and dated evidence once. Keep user-reviewed
    # policy versions, custom routes, settings, documents and profiles intact.
    migration = "1.0.3-preparation"
    if not store.get("migration", migration):
        for bundled in data["schemes"]:
            saved = store.get("scheme", bundled["id"])
            if saved["id"] == "nmmss" and saved.get("portal_url") == "https://scholarships.gov.in/":
                saved["portal_url"] = bundled["portal_url"]
                saved["application_route"] = bundled["application_route"]
            if saved["id"] in {"pudhumai-penn", "tamil-pudhalvan"} and saved.get("portal_url") == "https://umis.tn.gov.in/":
                saved["portal_url"] = bundled["portal_url"]
            if (saved["id"] == "nmmss" and saved.get("rule_version") == "draft-2026.09.29"
                    and saved.get("window_status") == "UNKNOWN"
                    and not saved.get("window_start") and not saved.get("window_end")):
                for key in ["window_status", "window_start", "window_end", "window_evidence_url", "window_checked_at"]:
                    saved[key] = bundled[key]
            store.put("scheme", saved["id"], saved)
        store.put("migration", migration, {"completed_at": now()})

def safe_official_url(url, hosts):
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443) or host not in hosts:
        raise ValueError("URL is outside this scheme's approved official hosts")
    if not (host.endswith(".gov.in") or host.endswith(".nic.in")):
        raise ValueError("Source must be an approved government host")
    addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError("Private or non-routable source address rejected")
    return url

async def fetch_source(url):
    host = urlparse(url).hostname
    allowed = {host}
    async with httpx.AsyncClient(timeout=httpx.Timeout(15), follow_redirects=False) as client:
        for _ in range(4):
            safe_official_url(url, allowed)
            async with client.stream("GET", url, headers={"User-Agent": "SchemeSaathi/1.0 source-review"}) as response:
                if response.is_redirect:
                    url = urljoin(url, response.headers["location"])
                    continue
                response.raise_for_status()
                buf = bytearray()
                async for part in response.aiter_bytes():
                    buf.extend(part)
                    if len(buf) > 8 * 1024 * 1024:
                        raise ValueError("Source exceeds the 8 MB review limit")
                raw = bytes(buf)
                if raw.startswith(b"%PDF"):
                    reader = PdfReader(io.BytesIO(raw))
                    text = "\n".join(f"[Page {i+1}]\n" + (p.extract_text() or "") for i, p in enumerate(reader.pages[:100]))
                else:
                    soup = BeautifulSoup(raw, "html.parser")
                    for tag in soup(["script", "style", "nav", "footer", "header"]):
                        tag.decompose()
                    text = (soup.find("main") or soup).get_text(" ", strip=True)
                text = re.sub(r"[ \t]+", " ", text).strip()[:200000]
                if len(text) < 100:
                    raise ValueError("Source has too little extractable text; review the original")
                return text, raw, url
    raise ValueError("Source redirect limit exceeded")

async def refresh_sources(store, llm=None):
    output = []
    for source in store.all("source"):
        if not source.get("url"):
            continue
        try:
            text, raw, final_url = await fetch_source(source["url"])
            sha = digest(text)
            changed = sha != source["hash"]
            source["last_fetch_at"] = now()
            source["last_error"] = None
            if changed:
                before = source["text"]
                old_hash = source["hash"]
                source.update(text=text, hash=sha, snapshot_kind="downloaded", final_url=final_url, raw_sha256=hashlib.sha256(raw).hexdigest(), previous_hash=old_hash)
                source["diff"] = "\n".join(difflib.unified_diff(before.splitlines(), text.splitlines(), fromfile=old_hash[:12], tofile=sha[:12]))[:30000]
                path = store.directory / "sources"
                path.mkdir(exist_ok=True)
                (path / (hashlib.sha256(raw).hexdigest() + ".bin")).write_bytes(raw)
                store.put("source_version", source["id"] + ":" + sha, source)
                for scheme in store.all("scheme"):
                    if source["id"] in scheme["source_ids"]:
                        scheme["policy_changed"] = True
                        store.put("scheme", scheme["id"], scheme)
                # Drafted commentary never activates policy changes.
                if llm and llm.available():
                    try:
                        source["change_draft"] = await llm.json("You are the Policy Update Agent. Compare the two official source passages. Return {summary: string, review_items: string[]}. Describe material rule changes only; do not write or activate rules.", {"before": before[:7000], "after": text[:7000]})
                    except Exception:
                        source["change_draft"] = {"summary": "Source changed. Manual comparison required.", "review_items": []}
            store.put("source", source["id"], source)
            output.append({"source_id": source["id"], "status": "CHANGED" if changed else "UNCHANGED"})
        except Exception as exc:
            source["last_error"] = "Fetch failed (" + type(exc).__name__ + "). Open the official source and review manually."
            source["last_attempt_at"] = now()
            store.put("source", source["id"], source)
            output.append({"source_id": source["id"], "status": "FETCH_FAILED"})
    return output

def retrieve(store, query, scheme_ids=None, k=8):
    """Small-catalogue lexical retrieval. Optional multilingual dense reranking.

    Eligibility always evaluates ALL catalogue candidates, not just these hits.
    """
    tokens = set(re.findall(r"[\w]+", query.lower()))
    allowed = None
    if scheme_ids:
        allowed = {s for scheme in store.all("scheme") if scheme["id"] in scheme_ids for s in scheme["source_ids"]}
    passages = []
    for source in store.all("source"):
        if allowed is not None and source["id"] not in allowed:
            continue
        text = source["text"]
        for start in range(0, len(text), 1600):
            chunk = text[start:start+1900]
            words = set(re.findall(r"[\w]+", chunk.lower()))
            score = len(words & tokens) / max(1, len(tokens))
            passages.append({"source_id": source["id"], "hash": source["hash"], "title": source["title"], "url": source.get("url"), "section": source["section"], "start": start, "text": chunk, "score": score})
    import os
    if os.getenv("SCHEMESAATHI_SEMANTIC") == "1" and passages:
        from .semantic import rerank
        passages = rerank(query, passages)
    return sorted(passages, key=lambda p: p["score"], reverse=True)[:k]

def graph(store, scheme_ids):
    # Explicit source-backed edges only; no invented certificate dependencies.
    nodes, edges = {}, []
    for scheme in store.all("scheme"):
        if scheme["id"] not in scheme_ids:
            continue
        nodes[scheme["id"]] = {"id": scheme["id"], "type": "scheme", "label": scheme["name"]}
        for doc in scheme["documents"]:
            nodes[doc["id"]] = {"id": doc["id"], "type": "document", "label": doc["name"], "url": doc.get("issuing_url")}
            edges.append({"from": scheme["id"], "to": doc["id"], "relation": "requires", "sources": scheme["source_ids"]})
        for sid in scheme["source_ids"]:
            source = store.get("source", sid)
            nodes[sid] = {"id": sid, "type": "source", "label": source["title"], "url": source.get("url")}
            edges.append({"from": scheme["id"], "to": sid, "relation": "supported_by"})
    return {"nodes": list(nodes.values()), "edges": edges}
