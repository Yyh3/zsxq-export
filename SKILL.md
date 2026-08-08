---
name: zsxq-export
description: Export all content from a zsxq (知识星球/ZhiShiXingQiu) circle to local Markdown files via QR-code login and API pagination. Use when the user says "导出知识星球""备份知识星球内容""把某个圈子的内容存到本地", or wants to archive/export/zsxq content, or needs incremental sync of new posts from a zsxq circle.
---

# 知识星球导出 (zsxq-export)

## Quick Start

Three steps: login → list circles → export.

### Step 1: Login (QR code scan)

```bash
python scripts/zsxq_login.py --state-file D:/知识星球/zsxq_auth.json
```

Opens a browser window. Scan the QR code with the zsxq mobile app. After login, auth info (cookies + headers + API version) is saved to the state file. **Login is valid ~1-3 months**; re-run when expired (script will report 401).

### Step 2: List your circles

```bash
python scripts/zsxq_export.py list --state-file D:/知识星球/zsxq_auth.json
```

Prints circle ID, name, member count, and your role. Note the `group-id` of the circle you want to export.

### Step 3: Export

```bash
# Full export (all topics + comments)
python scripts/zsxq_export.py export --group-id 123456 --output D:/知识星球/某圈子

# Incremental update (only new topics since last export)
python scripts/zsxq_export.py export --group-id 123456 --output D:/知识星球/某圈子 --incremental

# With images downloaded locally
python scripts/zsxq_export.py export --group-id 123456 --output D:/知识星球/某圈子 --download-images

# Test with 20 topics only
python scripts/zsxq_export.py export --group-id 123456 --output D:/知识星球/某圈子 --limit 20

# Only featured/digest posts
python scripts/zsxq_export.py export --group-id 123456 --output D:/知识星球/某圈子 --scope digests
```

## Output Format

Each topic is saved as one `.md` file named `{date}_{topic_id}_{title_slug}.md`:

```markdown
---
topic_id: 123456
type: text           # text | qa | article | file
author: 张三
created: 2024-01-15T10:30:00+08:00
tags: [pinned, featured]   # optional
---

# Topic title

**Author**: 张三 | **Time**: 2024-01-15 10:30:00 | **Type**: 文字帖

---

Topic content text...

### Images

![image 1](https://...)

## Comments (5)

**1. 李四** (2024-01-16 09:00:00)

Comment text...

> **王五** (2024-01-16 10:00:00): Reply text...
```

Incremental progress is tracked in `.zsxq_progress.json` inside the output directory — stores all exported topic IDs so next run stops at known content.

## Prerequisites

- Python 3.8+
- Playwright browser (`playwright install chromium` if not already installed)
- `requests` library
- Install deps: `pip install -r scripts/requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple`

## How It Works

1. **Login**: Playwright opens `https://wx.zsxq.com`, user scans QR, script polls for `zsxq_access_token` cookie, then intercepts `api.zsxq.com` requests to capture cookies + `x-aduid` + `x-version` headers, saves to JSON.
2. **Export**: `requests` carries cookies + `x-aduid` + `x-version` headers to call zsxq **v2** API: `GET /groups/{id}/topics?count=20` with ISO-format `end_time` pagination, `GET /topics/{id}/comments?count=20` for comments. Rate-limited at 1 req/sec. V3 API requires per-request `x-signature` — avoided by using v2.
3. **Content parsing**: Topic text is in `talk.text` (with zsxq `<e>` rich-text tags, cleaned to plain text). Author in `talk.owner`. Timestamps are ISO strings like `2026-08-08T13:59:51.169+0800`.
4. **Incremental**: Saves all exported `topic_id`s. Next run fetches newest-first, stops at first known ID.

## Troubleshooting

See [reference.md](reference.md) for API details, cookie expiration, rate limiting, and common errors.
