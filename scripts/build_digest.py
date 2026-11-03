#!/usr/bin/env python3
"""Build today's Hacker News digest without OpenCode.

GitHub-hosted runners can reach the public HN API, but the OpenCode Zen
free tier often accepts a request and never returns a token. This script
fetches stories itself, asks a keyless chat API to write the page, and
if every model is silent it still writes a shorter digest from the same
notes. The workflow should not fail just because a free model is down.
"""

from __future__ import annotations

import html
import json
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HN = "https://hacker-news.firebaseio.com/v0"
TODAY = datetime.now(timezone.utc).date()
OUT = ROOT / "digests" / "daily" / f"{TODAY.isoformat()}.md"
ID_RE = re.compile(r"item\?id=(\d+)")
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


def get_json(url: str, timeout: int = 20) -> object:
    req = urllib.request.Request(url, headers={"User-Agent": "hackerdigest/1.0"})
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except Exception as exc:  # noqa: BLE001 - retry any transport error
            last = exc
            time.sleep(1 + attempt)
    raise RuntimeError(f"GET {url} failed: {last}")


def clean(text: str | None, limit: int = 420) -> str:
    if not text:
        return ""
    text = html.unescape(text)
    text = text.replace("<p>", " ").replace("<br>", " ")
    text = TAG_RE.sub("", text)
    text = WS_RE.sub(" ", text).strip()
    if len(text) > limit:
        text = text[: limit - 1].rsplit(" ", 1)[0] + "."
    return text


def blacklist() -> set[str]:
    found: set[str] = set()
    for delta in (1, 2):
        path = ROOT / "digests" / "daily" / f"{(TODAY - timedelta(days=delta)).isoformat()}.md"
        if path.exists():
            found.update(ID_RE.findall(path.read_text()))
    return found


def fetch_item(item_id: int) -> dict | None:
    try:
        data = get_json(f"{HN}/item/{item_id}.json")
    except Exception as exc:  # noqa: BLE001
        print(f"::warning::item {item_id}: {exc}", file=sys.stderr)
        return None
    return data if isinstance(data, dict) else None


def story_packet(story: dict) -> dict:
    kids = [k for k in (story.get("kids") or [])[:8] if isinstance(k, int)]
    comments = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for item in pool.map(fetch_item, kids):
            if not item or item.get("deleted") or item.get("dead"):
                continue
            text = clean(item.get("text"), 380)
            if len(text) < 80:
                continue
            comments.append(text)
            if len(comments) == 3:
                break
    return {
        "id": story["id"],
        "title": story.get("title") or "",
        "score": story.get("score") or 0,
        "comments": story.get("descendants") or 0,
        "text": clean(story.get("text"), 500),
        "highlights": comments,
    }


def collect_stories() -> list[dict]:
    seen: list[int] = []
    have: set[int] = set()
    for kind, limit in (("topstories", 30), ("showstories", 12), ("askstories", 8), ("newstories", 15)):
        ids = get_json(f"{HN}/{kind}.json")
        if not isinstance(ids, list):
            continue
        for item_id in ids[:limit]:
            if isinstance(item_id, int) and item_id not in have:
                have.add(item_id)
                seen.append(item_id)

    blocked = blacklist()
    stories = []
    with ThreadPoolExecutor(max_workers=12) as pool:
        for item in pool.map(fetch_item, seen):
            if not item or item.get("type") != "story" or item.get("dead"):
                continue
            if str(item.get("id")) in blocked:
                continue
            title = item.get("title") or ""
            score = item.get("score") or 0
            if score < 30 and not title.startswith(("Show HN", "Ask HN")):
                continue
            if title.startswith("Ask HN") and score < 20:
                continue
            stories.append(item)
    stories.sort(key=lambda s: s.get("score") or 0, reverse=True)
    shortlist = stories[:10]
    print(f"shortlist {[s['id'] for s in shortlist]}", file=sys.stderr)
    with ThreadPoolExecutor(max_workers=6) as pool:
        return list(pool.map(story_packet, shortlist))


def packet_text(stories: list[dict]) -> str:
    blocks = []
    for story in stories:
        lines = [
            f"ID {story['id']} | {story['score']} points | {story['comments']} comments",
            story["title"],
        ]
        if story["text"]:
            lines.append(f"Post: {story['text']}")
        for idx, comment in enumerate(story["highlights"], 1):
            lines.append(f"Comment {idx}: {comment}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


SYSTEM = """You write a one-page Hacker News digest for engineers. Opinionated, concrete, no hype.

Output only markdown, using this shape:

# Hacker News Digest -- DATE

---

## Top Stories

### [Rewritten title](https://news.ycombinator.com/item?id=ID)
Two or three short sentences. What happened, why it matters, and one comment insight when the packet has one.

---

## Projects & Tools

- **[Name](https://news.ycombinator.com/item?id=ID)** -- One sentence.

---

## Worth Reading

- **[Title](https://news.ycombinator.com/item?id=ID)** -- One sentence.

---

## Community Pulse

- **[Title](https://news.ycombinator.com/item?id=ID)** -- One sentence.

---

*Generated from [Hacker News](https://news.ycombinator.com) top stories and discussions.*

Rules:
- Under 400 words. Drop a section if the packet has nothing good for it.
- 3 or 4 top stories, each a single paragraph of 2 or 3 sentences. Not Show HN and not Ask HN.
- Projects are tools and Show HN. Worth Reading is essays. Community Pulse is Ask HN or a comment thread.
- Use each story ID at most once in the whole digest.
- Use only IDs from the packet. Link every item to its HN discussion, never to the article.
- Never name HN users. Say "one commenter" or "the thread".
- Do not invent numbers, quotes, or claims that are not in the packet.
- Short sentences. No semicolon chains. No "exciting" or "groundbreaking".
"""


def chat(url: str, model: str, messages: list[dict], timeout: int, bearer: str | None = None) -> str:
    body = json.dumps(
        {"model": model, "messages": messages, "temperature": 0.3, "max_tokens": 4096}
    ).encode()
    headers = {"Content-Type": "application/json", "User-Agent": "hackerdigest/1.0"}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    req = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode())
    message = payload["choices"][0]["message"]
    content = (message.get("content") or "").strip()
    if len(content) < 80:
        content = (message.get("reasoning") or content).strip()
    return content


def providers(messages: list[dict]) -> str:
    attempts = [
        ("ovh", "https://oai.endpoints.kepler.ai.cloud.ovh.net/v1/chat/completions", "gpt-oss-20b", None, 50),
        ("pollinations", "https://text.pollinations.ai/openai", "openai-fast", None, 50),
        ("llm7", "https://api.llm7.io/v1/chat/completions", "GLM-5.3-Flash", "unused", 40),
    ]
    errors = []
    for name, url, model, key, timeout in attempts:
        print(f"::notice::trying {name}/{model}", file=sys.stderr)
        try:
            text = chat(url, model, messages, timeout, key)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{name}: {exc}")
            print(f"::warning::{name} failed: {exc}", file=sys.stderr)
            continue
        cleaned = normalize(text)
        reason = acceptable(cleaned)
        if reason is None:
            print(f"::notice::wrote digest with {name}/{model}", file=sys.stderr)
            return cleaned
        errors.append(f"{name}: {reason}")
        print(f"::warning::{name} rejected: {reason}", file=sys.stderr)
    raise RuntimeError("; ".join(errors))


def dedupe(text: str) -> str:
    """Keep the first section that uses a story and drop later repeats."""
    seen: set[str] = set()
    out: list[str] = []
    block: list[str] = []

    def flush() -> None:
        nonlocal block
        ids = ID_RE.findall("\n".join(block))
        if ids and all(item_id in seen for item_id in ids):
            block = []
            return
        seen.update(ids)
        out.extend(block)
        block = []

    for line in text.splitlines():
        if line.startswith("### ") or line.startswith("- "):
            flush()
            block = [line]
        elif block and not line.strip():
            block.append(line)
            flush()
        elif block:
            block.append(line)
        else:
            out.append(line)
    flush()
    return "\n".join(out).strip() + "\n"


def normalize(text: str) -> str:
    text = text.strip()
    fence = re.match(r"^```(?:markdown)?\s*(.*?)\s*```$", text, re.S)
    if fence:
        text = fence.group(1).strip()
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return dedupe(text)


def acceptable(text: str) -> str | None:
    first = text.splitlines()[0].strip() if text else ""
    expected = f"# Hacker News Digest -- {TODAY.isoformat()}"
    if first != expected:
        return f"bad title: {first[:80]}"
    if len(ID_RE.findall(text)) < 4:
        return "too few links"
    if "top stories and discussions.*" not in text:
        return "truncated"
    words = len(re.sub(r"https?://\S+", "", text).split())
    if not 180 <= words <= 650:
        return f"word count {words}"
    return None


def one_sentence(text: str, fallback: str) -> str:
    text = clean(text, 240)
    if not text:
        return fallback
    match = re.match(r"^(.{40,220}?[.!?])(?:\s|$)", text)
    return match.group(1) if match else text


def extractive(stories: list[dict]) -> str:
    def link(story: dict, label: str | None = None) -> str:
        title = label or story["title"]
        return f"[{title}](https://news.ycombinator.com/item?id={story['id']})"

    def blurb(story: dict) -> str:
        comment = one_sentence(story["highlights"][0], "") if story["highlights"] else ""
        post = one_sentence(story["text"], "")
        bits = [bit for bit in (post, comment) if bit]
        if not bits:
            return f"{story['score']} points and {story['comments']} comments on the front page."
        if len(bits) == 1:
            return bits[0]
        return f"{bits[0]} {bits[1]}"

    shows, asks, rest = [], [], []
    for story in stories:
        title = story["title"]
        if title.startswith("Show HN"):
            shows.append(story)
        elif title.startswith("Ask HN"):
            asks.append(story)
        else:
            rest.append(story)

    lines = [f"# Hacker News Digest -- {TODAY.isoformat()}", "", "---", "", "## Top Stories", ""]
    for story in rest[:4]:
        lines += [f"### {link(story)}", blurb(story), ""]
    if shows:
        lines += ["---", "", "## Projects & Tools", ""]
        for story in shows[:2]:
            name = re.sub(r"^Show HN:\s*", "", story["title"])
            lines.append(f"- **{link(story, name)}** -- {one_sentence(story['text'] or (story['highlights'] or [''])[0], 'A new Show HN project.')}")
        lines.append("")
    reading = rest[4:6]
    if reading:
        lines += ["---", "", "## Worth Reading", ""]
        for story in reading:
            lines.append(f"- **{link(story)}** -- {one_sentence(story['text'] or (story['highlights'] or [''])[0], 'Worth a look.')}")
        lines.append("")
    if asks:
        lines += ["---", "", "## Community Pulse", ""]
        story = asks[0]
        lines.append(f"- **{link(story)}** -- {one_sentence((story['highlights'] or [''])[0], story['text'] or 'The thread is the story.')}")
        lines.append("")
    lines += [
        "---",
        "",
        "*Generated from [Hacker News](https://news.ycombinator.com) top stories and discussions.*",
        "",
    ]
    print("::warning::models unavailable, wrote an extractive digest", file=sys.stderr)
    return "\n".join(lines)


def main() -> int:
    stories = collect_stories()
    if len(stories) < 4:
        print("::error::not enough HN stories to build a digest", file=sys.stderr)
        return 1
    messages = [
        {"role": "system", "content": SYSTEM},
        {
            "role": "user",
            "content": (
                f"DATE is {TODAY.isoformat()}. Write today's digest from this packet only.\n\n"
                + packet_text(stories)
            ),
        },
    ]
    try:
        digest = providers(messages)
    except Exception as exc:  # noqa: BLE001
        print(f"::warning::all models failed: {exc}", file=sys.stderr)
        digest = extractive(stories)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(digest if digest.endswith("\n") else digest + "\n")
    words = len(re.sub(r"https?://\S+", "", digest).split())
    print(f"wrote {OUT.relative_to(ROOT)} ({words} words)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"::error::{exc}", file=sys.stderr)
        raise SystemExit(1)
