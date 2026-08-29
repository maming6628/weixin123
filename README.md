# 链上叙事监控 -> 微信推送

监控两路信号，一旦有新动态就推送到你的微信：
1. **KOL 新推文**——你在 `narrative_watch.py` 里指定几个 X 账号，一旦发新推文就报警
2. **热度榜异动**——LunarCrush 热度榜（Galaxy Score）里，某个币首次进榜或排名大幅跳升就报警

## 第一步：申请三把 key

| Key | 去哪申请 | 说明 |
|---|---|---|
| `X_BEARER_TOKEN` | https://developer.x.com/ | 申请开发者账号 -> 创建 App -> 拿到 Bearer Token。免费层每月有请求次数限制，够个人用几个KOL |
| `LUNARCRUSH_API_KEY` | https://lunarcrush.com/developers/api | 注册后在开发者页面拿 key，有免费/试用额度 |
| `SERVERCHAN_KEY` | https://sct.ftqq.com/ | 微信扫码登录即可拿到 SendKey |

## 第二步：改配置

打开 `narrative_watch.py`，把顶部的：

```python
KOL_USERNAMES = [
    "example_kol_1",
    "example_kol_2",
]
```

换成你真正想盯的 X 用户名（不带 @，比如想监控 elonmusk 就写 `"elonmusk"`）。

也可以调整：
- `LUNARCRUSH_TOP_N`：监控热度榜前多少名（默认 30）
- `RANK_JUMP_THRESHOLD`：排名跳升超过多少名才算"异动"（默认 10）

## 第三步：本地测试

```bash
pip install requests
export X_BEARER_TOKEN="你的token"
export LUNARCRUSH_API_KEY="你的key"
export SERVERCHAN_KEY="你的SendKey"
python3 narrative_watch.py
```

**注意**：第一次运行是"建立基线"，通常不会有热度榜异动推送（因为没有"上一次"可比较），
KOL 推文那边只要账号最近发过推文，第一次就会全部当作"新推文"推给你一次，属于正常现象。

## 第四步：GitHub Actions 自动运行

1. 新建 GitHub 仓库，把这个文件夹整个传上去（包括 `state.json` 和 `.github/workflows/`）
2. 仓库 Settings -> Secrets and variables -> Actions，添加三个 Secret：
   - `X_BEARER_TOKEN`
   - `LUNARCRUSH_API_KEY`
   - `SERVERCHAN_KEY`
3. 仓库 Settings -> Actions -> General -> Workflow permissions，
   选择 **Read and write permissions**（因为脚本运行后要把 `state.json` 提交回仓库，
   否则每次都是冷启动、无法去重）
4. 完成，之后每 30 分钟自动跑一次。也可以去 Actions 页面手动点 "Run workflow" 立刻测试

## 常见问题

- **X API 免费额度不够用**：X API v2 的免费层限制比较严格（请求次数、只能查有限账号），
  如果账号一多就容易超限，可以考虑：减少监控账号数量、降低运行频率（比如改成每小时一次）、
  或升级到 X 的付费 API 套餐。
- **想加更多 KOL 或币种关键词过滤**：可以直接改脚本，比如给 LunarCrush 那部分加一个
  白名单/黑名单，只对你关心的板块报警，减少噪音。
- **想同时监控某些"关键词/话题"而不是固定账号**：X API v2 有 filtered stream / search
  recent 接口，可以按关键词搜索，我可以帮你在这个脚本基础上加一个关键词监控模块。
