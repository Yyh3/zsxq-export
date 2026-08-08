# 知识星球 API 参考

## API 版本与端点

实测确认：知识星球 web app 同时使用 v2 和 v3 API，**内容导出只需 v2**。

| API 版本 | 用途 | 认证要求 |
|----------|------|----------|
| v2 | 圈子列表、主题、评论 | cookies + `x-aduid` + `x-version` 头 |
| v3 | 用户信息、全局设置、排行 | 需 `x-signature` 签名（每次请求不同） |

始终使用 v2 API：`https://api.zsxq.com/v2/`

| 功能 | 方法 | 路径 | 参数 |
|------|------|------|------|
| 圈子列表 | GET | `/groups` | `count` |
| 主题列表 | GET | `/groups/{group_id}/topics` | `count`, `end_time`, `scope` |
| 评论列表 | GET | `/topics/{topic_id}/comments` | `count`(≤20), `end_time` |

## 认证

三个要素，缺一不可：

1. **Cookies**：`zsxq_access_token`（登录后获取）
2. **`x-aduid`**：设备标识，登录时从拦截的请求头捕获
3. **`x-version`**：web app 版本号，如 `2.95.0`

登录脚本通过 Playwright 拦截 `api.zsxq.com` 请求自动捕获这些值。
Cookie 有效期约 1-3 个月，过期后 API 返回 401。

## 主题（Topic）结构

### 关键字段映射

| API 字段 | 说明 |
|----------|------|
| `type` | 类型："talk"/"q&a"/"article"/"file" |
| `talk.text` | 正文内容（含 `<e>` 富文本标签） |
| `talk.owner` | 作者信息 `{user_id, name}` |
| `title` | 标题（可选，多为 null） |
| `create_time` | ISO 字符串 `"2026-08-08T13:59:51.169+0800"` |
| `comments_count` | 评论数 |
| `digested` | 是否精华 |
| `sticky` | 是否置顶 |
| `topic_id` | 唯一 ID（大整数） |

### 富文本格式

`talk.text` 包含 zsxq 自定义 `<e>` 标签：
- `<e type="hashtag" hid="..." title="...">#话题#</e>`
- `<e type="mention" ...>@用户名</e>`
- `<e type="link" ...>链接文字</e>`

脚本用 `clean_zsxq_text()` 提取标签内纯文本。

## 分页机制

使用 `end_time` 游标分页，**ISO 格式字符串**（非毫秒时间戳）：

1. 首次请求不传 `end_time`
2. 从最后一条主题的 `create_time` 取值
3. 作为下次请求的 `end_time` 参数
4. 返回空 `topics` 数组时结束

### scope 参数

- `all`：全部主题
- `digests`：仅精华帖

### 评论分页

评论 API 的 `count` 参数上限为 **20**（非 100），超过返回 `code: 17801` 错误。

### 主题 count 限制

主题 API 的 `count` 参数上限同样为 **20**（非 50/100）。count=100 返回空结果。脚本固定使用 `PAGE_SIZE = 20`。

### 分页边界重复

`end_time` 分页是 **inclusive**（包含边界主题）。每页最后一条主题会在下一页第一条重复出现。脚本通过 `exported_ids` 集合自动跳过重复，无需额外处理。

## 已知限制

### 反爬拦截（主题 + 评论）

知识星球反爬机制会周期性拦截非官方工具访问，返回 `"不支持非官方工具访问，建议使用官方 Skill 获取内容"`。**主题和评论端点都会被拦截**，约每 200 条主题触发一次。

拦截是**临时性**的（几秒到几十秒后自动恢复），脚本处理方式：

1. **主题 API**：`fetch_topics` 内置 3 次重试（等待 10s/20s），重试成功则继续分页
2. **评论 API**：首次失败后设置 `comments_blocked` 标志，跳过后续所有评论请求
3. **断点续传**：进度文件保存 `resume_end_time`（已到达的最早 `create_time`），重跑时直接从该时间点继续分页，无需重新翻已知页

### 大圈子导出策略

对于 8000+ 主题的大圈子，单次运行通常只能拉 200-400 条就会被反爬中断。需要**多次重跑**同一命令，每次自动从断点继续。实测 8298 条主题的圈子需要约 6-8 小时、20+ 轮才能全部拉完。

限速设为 3 秒/请求（`RATE_LIMIT_DELAY = 3.0`），比 1 秒显著降低触发频率。

## 响应格式

```json
{
  "succeeded": true,
  "resp_data": {
    "topics": [...]
  }
}
```

成功时 `succeeded` 为布尔 `true`。失败时为 `false`，附带 `code` 和 `info`。

## 常见问题

### 401 Authentication expired

Cookie 过期，重新运行 `zsxq_login.py`。

### code: 1059 "设备状态异常"

使用了 v3 API 而无 `x-signature`。脚本已固定使用 v2，不会触发此错误。

### code: 17801 "无效的count"

评论或主题 API count 参数超过 20。脚本已修正为 count=20。

### "不支持非官方工具访问"

评论 API 被反爬拦截。脚本自动检测并跳过评论获取，主题内容不受影响。这是知识星球服务端限制，非脚本问题。

### 中文编码错误（Windows）

设置环境变量 `PYTHONIOENCODING=utf-8`，或用 `python -X utf8` 运行。

### Playwright headless 报缺少浏览器

运行 `python -m playwright install chromium`，或使用 `headless=False`。
