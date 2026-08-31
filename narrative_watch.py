#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
链上叙事监控 v4（全免费版）-> 微信推送

【和 v3 的区别】
LunarCrush 需要付费套餐才能访问任何接口，v3 的免费额度实际上根本不存在。
v4 换成两个完全免费、不需要注册/不需要 API key 的数据源：

1. CoinGecko 趋势榜（/search/trending）
   过去24小时被搜索最多的币种和赛道，免费无需key。
   检测"新出现在趋势榜里的币"，类似之前微博热搜监控的逻辑。

2. DefiLlama 协议数据（/protocols）
   全网 DeFi 协议的锁仓资金(TVL)数据，每个协议自带"1小时变化率""24小时变化率"，
   免费无需key。脚本按协议所属赛道（Lending、RWA、Restaking、Dexes等）做加权平均，
   算出"哪个赛道的资金正在加速流入"，这是比社交热度更"硬"的叙事信号。

依赖：
- SERVERCHAN_KEY -> https://sct.ftqq.com/ （唯一还需要的key，用于推送到微信）

用法：
    export SERVERCHAN_KEY="..."
    python3 narrative_watch.py
"""

import os
import sys
import json
import requests
from collections import defaultdict

# ------------ 配置 ------------
TVL_MIN_THRESHOLD = 10_000_000    # 只关注锁仓资金超过这个数字(美元)的赛道，过滤掉噪音小赛道
CATEGORY_CHANGE_ALERT = 3.0        # 赛道加权平均1小时变化率超过这个百分比，判定为"资金流入加速"
COOLDOWN_REPEAT_GAP = 2.0          # 同一赛道再次提醒，变化率至少要比上次提醒时再高出这么多百分点
STATE_FILE = os.path.join(os.path.dirname(__file__), "state.json")
# --------------------------------

SERVERCHAN_KEY = os.environ.get("SERVERCHAN_KEY", "")

COINGECKO_TRENDING_URL = "https://api.coingecko.com/api/v3/search/trending"
DEFILLAMA_PROTOCOLS_URL = "https://api.llama.fi/protocols"


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"last_trending_coins": [], "alerted_categories": {}}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ---------- 信号一：CoinGecko 趋势榜新币 ----------
def fetch_trending_coins():
    try:
        resp = requests.get(COINGECKO_TRENDING_URL, timeout=15)
        print(f"[调试] 请求CoinGecko趋势榜，状态码: {resp.status_code}")
        resp.raise_for_status()
        data = resp.json()
        coins = data.get("coins", [])
        result = []
        for c in coins:
            item = c.get("item", {})
            symbol = item.get("symbol", "")
            name = item.get("name", "")
            rank = item.get("market_cap_rank")
            if symbol:
                result.append({"symbol": symbol, "name": name, "market_cap_rank": rank})
        print(f"[调试] 拉取到 {len(result)} 个趋势币种")
        return result
    except Exception as e:
        print(f"[警告] 拉取CoinGecko趋势榜失败: {e}")
        return []


def detect_new_trending(current_coins, state):
    last_symbols = set(state.get("last_trending_coins", []))
    current_symbols = [c["symbol"] for c in current_coins]

    new_coins = []
    if last_symbols:  # 第一次运行不算"新增"，只建立基线
        for c in current_coins:
            if c["symbol"] not in last_symbols:
                new_coins.append(c)

    state["last_trending_coins"] = current_symbols
    return new_coins


# ---------- 信号二：DefiLlama 赛道资金流 ----------
def fetch_defillama_protocols():
    try:
        resp = requests.get(DEFILLAMA_PROTOCOLS_URL, timeout=20)
        print(f"[调试] 请求DefiLlama协议数据，状态码: {resp.status_code}")
        resp.raise_for_status()
        data = resp.json()
        print(f"[调试] 拉取到 {len(data)} 个协议")
        return data
    except Exception as e:
        print(f"[警告] 拉取DefiLlama协议数据失败: {e}")
        return []


def aggregate_category_flow(protocols):
    """按赛道把协议的TVL加权，算出赛道整体1小时资金变化率"""
    cat_tvl = defaultdict(float)
    cat_weighted_change = defaultdict(float)
    cat_top_protocols = defaultdict(list)

    for p in protocols:
        category = p.get("category") or "未分类"
        tvl = p.get("tvl") or 0
        change_1h = p.get("change_1h")

        if tvl <= 0 or change_1h is None:
            continue

        cat_tvl[category] += tvl
        cat_weighted_change[category] += tvl * change_1h
        cat_top_protocols[category].append((p.get("name", ""), change_1h, tvl))

    category_stats = {}
    for cat, total_tvl in cat_tvl.items():
        if total_tvl < TVL_MIN_THRESHOLD:
            continue
        avg_change = cat_weighted_change[cat] / total_tvl
        top = sorted(cat_top_protocols[cat], key=lambda x: x[2], reverse=True)[:2]
        category_stats[cat] = {
            "total_tvl": total_tvl,
            "avg_change_1h": avg_change,
            "top_protocols": top,
        }

    return category_stats


def detect_surging_categories(category_stats, state):
    alerted = state.get("alerted_categories", {})
    surging = []

    for cat, stats in category_stats.items():
        change = stats["avg_change_1h"]
        if change >= CATEGORY_CHANGE_ALERT:
            last_alerted_change = alerted.get(cat)
            if last_alerted_change is not None and (change - last_alerted_change) < COOLDOWN_REPEAT_GAP:
                continue  # 跟上次提醒时差不多，跳过，避免刷屏
            surging.append((cat, stats))
            alerted[cat] = change

    state["alerted_categories"] = alerted
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

    # 信号一：新晋趋势币
    trending_coins = fetch_trending_coins()
    new_coins = detect_new_trending(trending_coins, state) if trending_coins else []

    # 信号二：赛道资金流
    protocols = fetch_defillama_protocols()
    surging_categories = []
    if protocols:
        category_stats = aggregate_category_flow(protocols)
        print(f"[调试] 聚合出 {len(category_stats)} 个赛道(过滤小赛道后)")
        surging_categories = detect_surging_categories(category_stats, state)

    save_state(state)

    if not new_coins and not surging_categories:
        print("本次没有新信号，不推送")
        return

    lines = []
    if new_coins:
        lines.append("【新晋趋势币】")
        for c in new_coins:
            rank_str = f"(市值排名第{c['market_cap_rank']}名)" if c.get("market_cap_rank") else ""
            lines.append(f"🆕 {c['name']} ({c['symbol'].upper()}) {rank_str}")
        lines.append("")

    if surging_categories:
        lines.append("【资金流入加速的赛道】")
        surging_categories.sort(key=lambda x: x[1]["avg_change_1h"], reverse=True)
        for cat, stats in surging_categories:
            lines.append(f"📈 {cat}：近1小时锁仓资金加权变化 +{stats['avg_change_1h']:.1f}%")
            top_str = "、".join([f"{name}" for name, _, _ in stats["top_protocols"]])
            if top_str:
                lines.append(f"   由 {top_str} 等带动")
        lines.append("")

    content = "\n".join(lines)
    title = f"链上叙事提醒：{len(new_coins)}个新趋势币 / {len(surging_categories)}个赛道升温"
    print(title)
    print(content)
    push_to_wechat(title, content)


if __name__ == "__main__":
    main()
