"""
知识星球内容导出脚本

从知识星球圈子批量导出内容为本地 Markdown 文件。
支持增量更新、评论导出、图片下载。

用法:
    # 列出所有圈子
    python zsxq_export.py list

    # 导出全部内容
    python zsxq_export.py export --group-id 123456 --output D:/知识星球/某圈子

    # 增量更新（仅新增内容）
    python zsxq_export.py export --group-id 123456 --output D:/知识星球/某圈子 --incremental

    # 下载图片到本地
    python zsxq_export.py export --group-id 123456 --output D:/知识星球/某圈子 --download-images

    # 仅导出前 20 条（测试用）
    python zsxq_export.py export --group-id 123456 --output D:/知识星球/某圈子 --limit 20
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

DEFAULT_STATE_FILE = "zsxq_auth.json"
DEFAULT_OUTPUT_DIR = "."
RATE_LIMIT_DELAY = 3.0  # seconds between API calls (increased to avoid anti-crawling)
MAX_RETRIES = 3
PAGE_SIZE = 20

BEIJING_TZ = timezone(timedelta(hours=8))

TYPE_LABELS = {
    "talk": "文字帖",
    "q&a": "问答",
    "article": "长文",
    "file": "文件",
    "task": "任务",
}


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def parse_create_time(raw: str) -> datetime:
    """Parse zsxq ISO create_time string to datetime."""
    if not raw:
        return None
    try:
        # Format: "2026-08-08T13:59:51.169+0800"
        return datetime.fromisoformat(raw)
    except Exception:
        try:
            # Fallback: try without microseconds
            return datetime.strptime(raw[:19], "%Y-%m-%dT%H:%M:%S").replace(
                tzinfo=BEIJING_TZ
            )
        except Exception:
            return None


def format_time_display(raw: str) -> str:
    """Format create_time for display."""
    dt = parse_create_time(raw)
    if dt:
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    return str(raw) if raw else ""


def format_time_iso(raw: str) -> str:
    """Format create_time as ISO string for frontmatter."""
    dt = parse_create_time(raw)
    if dt:
        return dt.isoformat()
    return str(raw) if raw else ""


def sanitize_filename(name: str, max_len: int = 50) -> str:
    """Make a string safe for use as a filename."""
    # Replace OS-reserved chars + zsxq common chars (#, full-width punctuation)
    name = re.sub(r'[\\/:*?"<>|\n\r\t#？：]', "_", name)
    name = re.sub(r"_+", "_", name)  # collapse repeated underscores
    name = re.sub(r"\s+", " ", name).strip()
    name = name.strip("_. ")
    if len(name) > max_len:
        name = name[:max_len].strip("_. ")
    return name or "untitled"


def clean_zsxq_text(text: str) -> str:
    """Strip zsxq <e> rich-text tags, keep inner text."""
    if not text:
        return ""
    # Remove <e ...>inner</e> → keep inner
    text = re.sub(r'<e[^>]*>(.*?)</e>', r'\1', text)
    # Remove any remaining tags
    text = re.sub(r'<[^>]+>', '', text)
    return text.strip()


def get_topic_title(topic: dict) -> str:
    """Extract a display title from a topic."""
    # Explicit title field
    title = topic.get("title") or ""
    if title:
        return title

    # For talk type, use talk.text
    talk = topic.get("talk") or {}
    text = talk.get("text", "")
    text = clean_zsxq_text(text)

    # For Q&A
    question = topic.get("question") or {}
    if question:
        text = clean_zsxq_text(question.get("text", "")) or text

    # For article
    article = topic.get("article") or {}
    if article and article.get("title"):
        return article["title"]

    # For file
    file_info = topic.get("file") or {}
    if file_info and file_info.get("name"):
        return f"file: {file_info['name']}"

    # Use first line of text
    return text[:60].replace("\n", " ").strip() or "untitled"


def get_topic_type(topic: dict) -> str:
    """Determine topic type from the 'type' field."""
    t = topic.get("type", "talk")
    return t


def get_topic_owner(topic: dict) -> dict:
    """Get the owner of a topic."""
    talk = topic.get("talk") or {}
    if talk.get("owner"):
        return talk["owner"]
    question = topic.get("question") or {}
    if question.get("owner"):
        return question["owner"]
    return topic.get("owner") or {}


def get_topic_text(topic: dict) -> str:
    """Get the main text content of a topic."""
    talk = topic.get("talk") or {}
    if talk.get("text"):
        return clean_zsxq_text(talk["text"])
    question = topic.get("question") or {}
    if question.get("text"):
        return clean_zsxq_text(question.get("text", ""))
    return topic.get("text", "") or ""


def get_topic_images(topic: dict) -> list:
    """Get all images from a topic."""
    images = []
    talk = topic.get("talk") or {}
    images.extend(talk.get("images") or [])
    question = topic.get("question") or {}
    images.extend(question.get("images") or [])
    answer = topic.get("answer") or {}
    images.extend(answer.get("images") or [])
    images.extend(topic.get("images") or [])
    return images


# ---------------------------------------------------------------------------
# Markdown formatting
# ---------------------------------------------------------------------------

def format_images_markdown(images: list) -> str:
    if not images:
        return ""
    parts = []
    for i, img in enumerate(images, 1):
        img_url = img.get("url", {})
        url = img_url.get("large") or img_url.get("thumbnail", "")
        if url:
            parts.append(f"![image {i}]({url})")
    if parts:
        return "\n\n### Images\n\n" + "\n\n".join(parts) + "\n"
    return ""


def format_comments_markdown(comments: list) -> str:
    if not comments:
        return ""
    parts = [f"\n\n## Comments ({len(comments)})\n"]
    for i, comment in enumerate(comments, 1):
        c_owner = comment.get("owner", {}).get("name", "anonymous")
        c_time = format_time_display(comment.get("create_time", ""))
        c_text = clean_zsxq_text(comment.get("text", ""))
        parts.append(f"**{i}. {c_owner}** ({c_time})\n\n{c_text}\n")

        replies = comment.get("replies", [])
        for reply in replies:
            r_owner = reply.get("owner", {}).get("name", "anonymous")
            r_time = format_time_display(reply.get("create_time", ""))
            r_text = clean_zsxq_text(reply.get("text", ""))
            parts.append(f"> **{r_owner}** ({r_time}): {r_text}\n")
    return "\n".join(parts)


def format_topic_markdown(topic: dict, comments: list = None) -> str:
    """Format a complete topic as a Markdown document."""
    topic_id = topic.get("topic_id", "")
    create_time_raw = topic.get("create_time", "")
    time_str = format_time_display(create_time_raw)
    time_iso = format_time_iso(create_time_raw)

    owner = get_topic_owner(topic)
    author = owner.get("name", "unknown")

    title = get_topic_title(topic)
    topic_type = get_topic_type(topic)
    type_label = TYPE_LABELS.get(topic_type, topic_type)

    tags = []
    if topic.get("sticky"):
        tags.append("pinned")
    if topic.get("digested"):
        tags.append("featured")

    # Build content based on type
    all_images = get_topic_images(topic)

    if topic_type == "q&a":
        question = topic.get("question") or {}
        answer = topic.get("answer") or {}
        q_owner = question.get("owner", {}).get("name", "anonymous")
        q_text = clean_zsxq_text(question.get("text", ""))
        a_owner = answer.get("owner", {}).get("name", "unanswered") if answer else "unanswered"
        a_text = clean_zsxq_text(answer.get("text", "")) if answer else "(no answer yet)"

        content = f"## Question\n\n{q_text}\n\n## Answer\n\n{a_text}"
        author_line = (
            f"**Question by**: {q_owner} | **Answer by**: {a_owner} | "
            f"**Time**: {time_str} | **Type**: {type_label}"
        )

    elif topic_type == "article":
        article = topic.get("article") or {}
        article_content = article.get("article_content", "")
        if not article_content:
            article_content = article.get("text", "")
        content = clean_zsxq_text(article_content)
        author_line = f"**Author**: {author} | **Time**: {time_str} | **Type**: {type_label}"

    elif topic_type == "file":
        file_info = topic.get("file") or {}
        file_name = file_info.get("name", "unknown file")
        file_size = file_info.get("size", 0)
        size_str = f"{file_size / 1024:.1f} KB" if file_size else "unknown"

        text = get_topic_text(topic)
        content = f"**File name**: {file_name}\n\n**Size**: {size_str}\n\n"
        if file_info.get("description"):
            content += f"**Description**: {file_info['description']}\n\n"
        if text:
            content += text
        author_line = f"**Uploader**: {author} | **Time**: {time_str} | **Type**: {type_label}"

    else:
        # talk or other types
        text = get_topic_text(topic)
        content = text
        author_line = f"**Author**: {author} | **Time**: {time_str} | **Type**: {type_label}"

    if tags:
        author_line += f" | **Tags**: {', '.join(tags)}"

    image_md = format_images_markdown(all_images)

    comment_md = ""
    comments_count = topic.get("comments_count", 0) or 0
    if comments:
        comment_md = format_comments_markdown(comments)
    elif comments_count > 0:
        comment_md = f"\n\n## Comments ({comments_count} comments, not fetched)\n"

    # YAML frontmatter
    frontmatter = "---\n"
    frontmatter += f"topic_id: {topic_id}\n"
    frontmatter += f"type: {topic_type}\n"
    frontmatter += f"author: {author}\n"
    frontmatter += f"created: {time_iso}\n"
    if tags:
        frontmatter += f"tags: [{', '.join(tags)}]\n"
    frontmatter += "---\n\n"

    # Assemble
    md = frontmatter
    md += f"# {title}\n\n"
    md += author_line + "\n\n---\n\n"
    md += content
    md += image_md
    md += comment_md

    return md


def get_topic_filename(topic: dict) -> str:
    """Generate a filename for a topic."""
    topic_id = topic.get("topic_id", "unknown")
    create_time_raw = topic.get("create_time", "")
    dt = parse_create_time(create_time_raw)
    date_str = dt.strftime("%Y-%m-%d") if dt else "unknown"
    title = get_topic_title(topic)
    slug = sanitize_filename(title, max_len=30)
    return f"{date_str}_{topic_id}_{slug}.md"


# ---------------------------------------------------------------------------
# API Client
# ---------------------------------------------------------------------------

class ZsxqClient:
    """Client for the zsxq v2 API."""

    def __init__(self, state_file: str = DEFAULT_STATE_FILE):
        self.state_file = Path(state_file)
        self.session = requests.Session()
        self._load_auth()

    def _load_auth(self):
        if not self.state_file.exists():
            raise FileNotFoundError(
                f"Auth file not found: {self.state_file}\n"
                "Please run zsxq_login.py first to login."
            )

        auth = json.loads(self.state_file.read_text(encoding="utf-8"))

        # Always use v2 API (v3 requires x-signature, v2 doesn't)
        self.api_base = "https://api.zsxq.com/v2/"
        cookies = auth.get("cookies", {})
        saved_headers = auth.get("headers", {})

        # Set up session cookies
        self.session.cookies.update(cookies)

        # Set up headers with required auth headers
        self.session.headers.update({
            "User-Agent": saved_headers.get("user-agent", (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Origin": "https://wx.zsxq.com",
            "Referer": "https://wx.zsxq.com/",
            "Connection": "keep-alive",
            # Required for v2 API authentication
            "x-aduid": saved_headers.get("x-aduid", ""),
            "x-version": saved_headers.get("x-version", "2.95.0"),
        })

    def _get(self, path: str, params: dict = None, quiet: bool = False) -> dict:
        """Make a GET request to the zsxq v2 API.

        When quiet=True, suppress warnings for known anti-crawling errors.
        """
        url = self.api_base.rstrip("/") + path
        for attempt in range(MAX_RETRIES):
            try:
                resp = self.session.get(url, params=params, timeout=30)

                if resp.status_code == 401:
                    raise RuntimeError(
                        "Authentication expired (401). "
                        "Please re-run zsxq_login.py to refresh login."
                    )

                if resp.status_code != 200:
                    print(f"  [ERROR] HTTP {resp.status_code}: {resp.text[:200]}")
                    if attempt < MAX_RETRIES - 1:
                        time.sleep(5)
                        continue
                    return {}

                data = resp.json()
                if not data.get("succeeded"):
                    err = data.get("resp_err") or data.get("info") or str(data)[:200]
                    # Suppress noise for known anti-crawling errors
                    if not quiet:
                        print(f"  [WARN] API error: {err}")
                time.sleep(RATE_LIMIT_DELAY)
                return data

            except requests.RequestException as e:
                print(f"  [ERROR] Network: {e}")
                if attempt < MAX_RETRIES - 1:
                    time.sleep(5)
                    continue
                raise
        return {}

    def list_groups(self) -> list:
        """List all groups the user has joined."""
        data = self._get("/groups", params={"count": 50})
        if data.get("succeeded"):
            return data.get("resp_data", {}).get("groups", [])
        return []

    def fetch_topics(self, group_id: int, end_time: str = None,
                     scope: str = None) -> tuple:
        """Fetch a page of topics from a group.

        Returns (topics, next_end_time).
        Pagination uses ISO-format end_time from the last topic's create_time.
        Retries on API errors to handle transient anti-crawling blocks.
        """
        params = {"count": PAGE_SIZE}
        if end_time:
            params["end_time"] = end_time
        if scope and scope != "all":
            params["scope"] = scope

        # Retry up to 3 times on API errors (anti-crawling blocks are transient)
        for attempt in range(3):
            data = self._get(f"/groups/{group_id}/topics", params=params)
            if data.get("succeeded"):
                resp_data = data.get("resp_data", {})
                topics = resp_data.get("topics", [])
                next_end_time = resp_data.get("end_time")

                # Fallback: use last topic's create_time for pagination
                if not next_end_time and topics:
                    last_topic = topics[-1]
                    next_end_time = last_topic.get("create_time")

                return topics, next_end_time

            # API returned error — wait and retry
            if attempt < 2:
                wait = 10 * (attempt + 1)
                print(f"  [WARN] Topics API error, retrying in {wait}s... (attempt {attempt + 1}/3)")
                time.sleep(wait)

        # All retries exhausted
        print("  [ERROR] Topics API failed after 3 retries. Stopping pagination.")
        return [], None

    def fetch_comments(self, topic_id: int) -> list:
        """Fetch all comments for a topic (with pagination).

        Returns empty list if comments API is blocked by anti-crawling.
        """
        all_comments = []
        end_time = None

        while True:
            params = {"count": 20}
            if end_time:
                params["end_time"] = end_time

            data = self._get(f"/topics/{topic_id}/comments", params=params, quiet=True)
            if data.get("succeeded"):
                resp_data = data.get("resp_data", {})
                comments = resp_data.get("comments", [])
                if not comments:
                    break
                all_comments.extend(comments)
                next_end_time = resp_data.get("end_time")
                if not next_end_time:
                    # Fallback: use last comment's create_time
                    if comments:
                        next_end_time = comments[-1].get("create_time")
                    if not next_end_time:
                        break
                end_time = next_end_time
            else:
                # API returned error (likely anti-crawling block)
                break

        return all_comments


# ---------------------------------------------------------------------------
# Export Logic
# ---------------------------------------------------------------------------

class TopicExporter:
    """Handles the export workflow for a zsxq group."""

    def __init__(self, client: ZsxqClient, output_dir: str):
        self.client = client
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.progress_file = self.output_dir / ".zsxq_progress.json"

    def load_progress(self) -> dict:
        if self.progress_file.exists():
            return json.loads(self.progress_file.read_text(encoding="utf-8"))
        return {
            "exported_topic_ids": [],
            "last_export_time": None,
            "total_exported": 0,
            "group_id": None,
            "resume_end_time": None,
        }

    def save_progress(self, progress: dict):
        progress["last_export_time"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self.progress_file.write_text(
            json.dumps(progress, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def export_group(
        self,
        group_id: int,
        incremental: bool = False,
        download_images: bool = False,
        limit: int = None,
        scope: str = None,
    ) -> int:
        """Export all topics from a group."""
        progress = self.load_progress()
        exported_ids = set(progress.get("exported_topic_ids", []))

        if progress.get("group_id") and progress["group_id"] != group_id:
            print(f"[WARN] Progress file is for group {progress['group_id']}, "
                  f"but exporting group {group_id}. Starting fresh.")
            exported_ids = set()
            progress = {
                "exported_topic_ids": [],
                "last_export_time": None,
                "total_exported": 0,
                "group_id": group_id,
            }

        progress["group_id"] = group_id

        images_dir = None
        if download_images:
            images_dir = self.output_dir / "images"
            images_dir.mkdir(exist_ok=True)

        # Resume from where we left off (skip re-fetching known pages)
        end_time = progress.get("resume_end_time")
        if end_time:
            print(f"Resuming from end_time: {end_time}")
        new_count = 0
        total_fetched = 0
        page = 0
        comments_blocked = False  # Track if comments API is blocked
        oldest_create_time = None  # Track the oldest topic we've seen this run

        print(f"Starting export for group {group_id}...")
        if incremental:
            print("Mode: incremental (stop at known content)")
        if limit:
            print(f"Limit: {limit} topics")

        while True:
            page += 1
            topics, next_end_time = self.client.fetch_topics(
                group_id, end_time=end_time, scope=scope
            )

            if not topics:
                print(f"\nNo more topics. Total pages fetched: {page}")
                break

            print(f"\n[Page {page}] Got {len(topics)} topics")

            stop = False
            for topic in topics:
                topic_id = topic.get("topic_id")

                # Skip duplicates (pagination boundary overlap)
                if topic_id and topic_id in exported_ids:
                    if incremental:
                        print(f"  Hit known topic {topic_id}, incremental stop.")
                        stop = True
                        break
                    continue

                # Fetch comments if the topic has any (skip if blocked)
                comments = []
                comments_count = topic.get("comments_count", 0) or 0
                if comments_count > 0 and not comments_blocked:
                    try:
                        comments = self.client.fetch_comments(topic_id)
                        if not comments:
                            # First failed fetch — likely anti-crawling block
                            comments_blocked = True
                            print("  [INFO] Comments API blocked by zsxq, skipping comments for remaining topics.")
                    except Exception as e:
                        print(f"  [WARN] Failed to fetch comments for {topic_id}: {e}")

                # Format and save
                md = format_topic_markdown(topic, comments)
                filename = get_topic_filename(topic)
                filepath = self.output_dir / filename
                filepath.write_text(md, encoding="utf-8")

                exported_ids.add(topic_id)
                new_count += 1
                total_fetched += 1

                # Track oldest create_time for resume
                topic_ct = topic.get("create_time")
                if topic_ct and (oldest_create_time is None or topic_ct < oldest_create_time):
                    oldest_create_time = topic_ct

                title_preview = get_topic_title(topic)[:40]
                print(f"  [{total_fetched}] Saved: {filename}")
                print(f"      {title_preview}")

                if download_images and images_dir:
                    self._download_topic_images(topic, images_dir)

                if limit and total_fetched >= limit:
                    print(f"\nReached limit of {limit} topics.")
                    stop = True
                    break

            if stop:
                break

            if not next_end_time:
                print("\nNo next page marker, stopping.")
                break

            end_time = next_end_time

        # Save progress
        progress["exported_topic_ids"] = list(exported_ids)
        progress["total_exported"] = len(exported_ids)
        # Save resume point: the oldest create_time we've reached
        if oldest_create_time:
            progress["resume_end_time"] = oldest_create_time
        self.save_progress(progress)

        print(f"\n{'=' * 50}")
        print(f"Export complete!")
        print(f"  New topics this run: {new_count}")
        print(f"  Total exported: {len(exported_ids)}")
        print(f"  Output dir: {self.output_dir}")
        print(f"  Progress file: {self.progress_file}")

        return new_count

    def _download_topic_images(self, topic: dict, images_dir: Path):
        """Download all images from a topic."""
        all_images = get_topic_images(topic)

        for img in all_images:
            img_url = img.get("url", {})
            url = img_url.get("large") or img_url.get("thumbnail")
            if not url:
                continue
            try:
                resp = self.client.session.get(url, timeout=30)
                if resp.status_code == 200:
                    filename = url.split("/")[-1].split("?")[0]
                    if not filename:
                        filename = f"image_{topic.get('topic_id', 'unknown')}_{hash(url)}.jpg"
                    filepath = images_dir / filename
                    filepath.write_bytes(resp.content)
            except Exception as e:
                print(f"    [WARN] Image download failed: {e}")


# ---------------------------------------------------------------------------
# Main CLI
# ---------------------------------------------------------------------------

def cmd_list(client: ZsxqClient):
    """List all groups."""
    groups = client.list_groups()
    if not groups:
        print("No groups found. Check if login is still valid.")
        return

    print(f"Found {len(groups)} groups:\n")
    print(f"{'ID':>15}  {'Name':<30}  {'Type':<6}  {'Owner'}")
    print("-" * 75)
    for g in groups:
        gid = g.get("group_id", "")
        name = g.get("name", "unknown")
        gtype = g.get("type", "")
        owner = g.get("owner", {}).get("name", "")
        print(f"{gid:>15}  {name:<30}  {gtype:<6}  {owner}")

    print()
    print("To export a group:")
    print(f"  python zsxq_export.py export --group-id <ID> --output <PATH>")


def cmd_export(client: ZsxqClient, args):
    """Export topics from a group."""
    exporter = TopicExporter(client, args.output)
    exporter.export_group(
        group_id=args.group_id,
        incremental=args.incremental,
        download_images=args.download_images,
        limit=args.limit,
        scope=args.scope,
    )


def main():
    parser = argparse.ArgumentParser(
        description="Export content from zsxq (ZhiShiXingQiu) circles."
    )
    parser.add_argument(
        "--state-file",
        default=DEFAULT_STATE_FILE,
        help="Path to auth state file (default: zsxq_auth.json)",
    )
    subparsers = parser.add_subparsers(dest="command", help="Command")

    shared_parent = argparse.ArgumentParser(add_help=False)
    shared_parent.add_argument(
        "--state-file",
        default=DEFAULT_STATE_FILE,
        help="Path to auth state file (default: zsxq_auth.json)",
    )

    sub_list = subparsers.add_parser("list", help="List all your circles", parents=[shared_parent])

    sub_export = subparsers.add_parser("export", help="Export topics from a circle", parents=[shared_parent])
    sub_export.add_argument("--group-id", type=int, required=True, help="Circle ID")
    sub_export.add_argument("--output", default=DEFAULT_OUTPUT_DIR, help="Output directory")
    sub_export.add_argument("--incremental", action="store_true", help="Only export new topics")
    sub_export.add_argument("--download-images", action="store_true", help="Download images locally")
    sub_export.add_argument("--limit", type=int, help="Max topics to export (for testing)")
    sub_export.add_argument(
        "--scope",
        choices=["all", "digests"],
        default="all",
        help="Scope: all topics or digests only (default: all)",
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    try:
        client = ZsxqClient(args.state_file)
    except FileNotFoundError as e:
        print(f"[ERROR] {e}")
        sys.exit(1)

    if args.command == "list":
        cmd_list(client)
    elif args.command == "export":
        cmd_export(client, args)


if __name__ == "__main__":
    main()
