# pansou-quark-mcp

基于 **Pansou + TMDB + 夸克网盘 + 第三方大模型 API** 的全自动影视搜刮、验活、去广告转存与规范重命名服务，支持 **远程 MCP Server** 和 **HTTP API**，可免费部署在 Vercel 上。

## 一键部署到 Vercel

[![Deploy with Vercel](https://vercel.com/button)](https://vercel.com/new/clone?repository-url=https%3A%2F%2Fgithub.com%2FYetiL%2Fpansou-quark-mcp&env=PANSOU_URL,QUARK_COOKIE,TMDB_API_KEY,LLM_BASE_URL,LLM_API_KEY,LLM_MODEL,API_SECRET)

## 环境变量说明

| 变量名 | 必填 | 说明 |
| --- | --- | --- |
| `PANSOU_URL` | 是 | 你的 `fish2018/pansou` 后端地址 |
| `QUARK_COOKIE` | 是 | 夸克网盘 Cookie |
| `TMDB_API_KEY` | 是 | TMDB API Key（v3 或 v4 Bearer Token） |
| `LLM_BASE_URL` | 是 | OpenAI 兼容大模型接口（如 `https://api.deepseek.com/v1`） |
| `LLM_API_KEY` | 是 | 大模型 API Key |
| `LLM_MODEL` | 是 | 模型名称（如 `deepseek-chat`） |
| `API_SECRET` | 推荐 | 接口鉴权密钥（Bearer Token） |
| `MOVIE_DIR` | 否 | 电影存放目录，默认 `/影视库/电影` |
| `TV_DIR` | 否 | 剧集存放目录，默认 `/影视库/剧集` |
| `PREFER_KEYWORDS` | 否 | 偏好词，逗号分隔，默认 `4K,2160p,HDR,杜比视界,DoVi,高码率,HQ,REMUX,简繁,内封` |
| `EXCLUDE_KEYWORDS` | 否 | 排除词，逗号分隔，默认 `枪版,TC,TS,CAM,HDRip,删减,预告` |

## 使用方式

### 1. 作为远程 MCP Server（推荐给 Agent 使用）
在支持 Streamable HTTP MCP 的客户端中配置：
- **URL**: `https://你的域名.vercel.app/api/mcp`
- **Headers**: `Authorization: Bearer 你的API_SECRET`

### 2. 作为 HTTP API（快捷指令 / Bot 调用）
```bash
curl -X POST https://你的域名.vercel.app/api/sync \
  -H "Authorization: Bearer 你的API_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"text": "帮我把沙丘2 4K版存一下"}'
```
