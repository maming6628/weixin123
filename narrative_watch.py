 这个接口需要更高付费档位，你现在的套餐访问不了。
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
链上叙事监控 v3 -> 微信推送

【v2 -> v3 的变化】
v2 用的 LunarCrush "加密类别话题榜"接口（category/cryptocurrencies/topics）
返回 402 Payment Required ——
v3 换回确认能访问的基础接口 coins/list/v2（之前一直没报过错），
但不再只看单个币的排名，而是自己把"赛道/叙事"拼出来：

- coins/list/v2 每个币自带 categories 字段（比如 "layer-1,meme,ai" 这种标签）
- 脚本按类别把所有币的 interactions_24h（24小时社交互动量）加总，
  相当于自己算出"哪个赛道整体最热门"
- 跟上一次运行记录的赛道排名对比，判断"整体排名是否明显上升" -> 判定为"叙事升温"
- 推送时附带该赛道里涨幅最猛的1-2个代表性币种，作为"由谁带动"的参考

这样完全在你现有免费/试用额度能访问的接口范围内实现"叙事级别"的监控，
不需要额外付费，也不用调用可能同样受限的 AI 摘要接口。

依赖：
- LUNARCRUSH_API_KEY -> https://lunarcrush.com/developers/api
- SERVERCHAN_KEY     -> https://sct.ftqq.com/

用法：
    export LUNARCRUSH_API_KEY="..."
    export SERVERCHAN_KEY="..."
    python3 narrative_watch.py
"""

import os
import sys
import json
import requests
from collections import defaultdict

# ------------ 配置 ------------
COINS_LIMIT = 300          # 拉取市值/热度前多少个币来做赛道聚合（越大覆盖面越广，但请求更久）
CATEGORY_RANK_JUMP = 5      # 赛道排名比上次跳升超过这个名次，判定为"正在升温"
STATE_FILE = os.path.join(os.path.dirname(__file__), "state.json")
# --------------------------------

LUNARCRUSH_API_KEY = os.environ.get("LUNARCRUSH_API_KEY", "")
SERVERCHAN_KEY = os.environ.get("SERVERCHAN_KEY", "")

COINS_LIST_URL = "https://lunarcrush.com/api4/public/coins/list/v2"


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"last_category_rank": {}}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def fetch_coins():
    """拉取热度靠前的币种列表（确认可访问的基础接口）"""
    if not LUNARCRUSH_API_KEY:
        print("[错误] 未配置 LUNARCRUSH_API_KEY")
        return []

    headers = {"Authorization": f"Bearer {LUNARCRUSH_API_KEY}"}
    params = {"sort": "interactions_24h", "desc": "true", "limit": COINS_LIMIT}
    resp = requests.get(COINS_LIST_URL, headers=headers, params=params, timeout=20)
    print(f"[调试] 请求币种列表接口，状态码: {resp.status_code}")
    resp.raise_for_status()
    data = resp.json().get("data", [])
    print(f"[调试] 拉取到 {len(data)} 个币种")
    return data


def aggregate_by_category(coins):
    """按 categories 标签把币种的热度加总，自己拼出赛道级别的排名"""
    category_interactions = defaultdict(float)
    category_top_coins = defaultdict(list)  # 记录每个赛道里热度最高的几个币，用于推送时展示

    for coin in coins:
        interactions = coin.get("interactions_24h") or 0
        symbol = coin.get("symbol", "")
        categories_str = coin.get("categories", "") or ""
        cats = [c.strip() for c in categories_str.split(",") if c.strip()]

        for cat in cats:
            category_interactions[cat] += interactions
            category_top_coins[cat].append((symbol, interactions))

    # 每个赛道只保留热度最高的2个代表币种
    for cat in category_top_coins:
        category_top_coins[cat].sort(key=lambda x: x[1], reverse=True)
        category_top_coins[cat] = category_top_coins[cat][:2]

    # 按总热度排名，生成 {类别: 排名}
    ranked = sorted(category_interactions.items(), key=lambda x: x[1], reverse=True)
    category_rank = {cat: idx + 1 for idx, (cat, _) in enumerate(ranked)}

    return category_rank, category_top_coins


def detect_surging_categories(category_rank, category_top_coins, state):
    """对比上次记录，找出排名明显上升的赛道"""
    last_rank = state.get("last_category_rank", {})
    surging = []

    for cat, rank in category_rank.items():
        prev_rank = last_rank.get(cat)
        if prev_rank is None:
            continue  # 第一次出现的赛道不算"升温"，只建立基线
        jump = prev_rank - rank
        if jump >= CATEGORY_RANK_JUMP:
            top_coins = category_top_coins.get(cat, [])
            surging.append((cat, prev_rank, rank, top_coins))

    state["last_category_rank"] = category_rank
    return surging


def push_to_wechat(title, content):
    if not SERVERCHAN_KEY:
        print("[错误] 未配置 SERVERCHAN_KEY，无法推送")
        return
    url = f"https://sctapi.ftqq.com/{SERVERCHAN_KEY}.send"
    resp = requests.post(url, data={"title": title, "desp": content}, timeout=10)
    try:
        result = resp.json()
        if result.get("code") == 0:
            print("[成功] 已推送到微信")
        else:
            print(f"[失败] {result}")
    except Exception:
        print(f"[失败] 推送返回异常: {resp.text}")


def main():
    state = load_state()

    coins = fetch_coins()
    if not coins:
        print("[警告] 没拉到币种数据，检查 LUNARCRUSH_API_KEY 是否正确")
        save_state(state)
        sys.exit(1)

    category_rank, category_top_coins = aggregate_by_category(coins)
    print(f"[调试] 聚合出 {len(category_rank)} 个赛道")

    surging = detect_surging_categories(category_rank, category_top_coins, state)
    save_state(state)

    if not surging:
        print("本次没有明显升温的赛道，不推送")
        return

    lines = []
    surging.sort(key=lambda x: x[2])  # 按当前排名从高到低展示
    for cat, prev_rank, rank, top_coins in surging:
        lines.append(f"📈 {cat}：第 {prev_rank} 名 -> 第 {rank} 名")
        if top_coins:
            coin_str = "、".join([f"{sym}" for sym, _ in top_coins])
            lines.append(f"   由 {coin_str} 等带动")
        lines.append("")

    content = "\n".join(lines)
    title = f"链上叙事提醒：{len(surging)}个赛道正在升温"
    print(title)
    print(content)
    push_to_wechat(title, content)


if __name__ == "__main__":
    main()
