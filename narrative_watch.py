#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
链上叙事监控 -> 微信推送

两路信号：
1. 指定 KOL 账号在 X 上的最新推文（用 X API v2）
2. LunarCrush 的热度榜（Galaxy Score / AltRank / 社交热度），
   检测排名跳升 / 热度突增的币种或话题

有新推文，或者某个币/话题热度出现明显跳变时，通过 Server酱 推送到微信。

依赖的三把 key（都需要你自己申请，免费额度通常够用于个人监控）：
- X_BEARER_TOKEN     -> https://developer.x.com/  (X API v2，免费层每月有请求额度限制)
- LUNARCRUSH_API_KEY -> https://lunarcrush.com/developers/api  (有免费/试用额度)
- SERVERCHAN_KEY     -> https://sct.ftqq.com/ (微信扫码登录即可获取)

用法：
    export X_BEARER_TOKEN="..."
    export LUNARCRUSH_API_KEY="..."
    export SERVERCHAN_KEY="..."
    python3 narrative_watch.py

首次运行会建立基线（state.json），之后每次运行只报"新出现的东西"，
不会重复推送同样的内容。建议用 GitHub Actions 定时跑（见 workflows/narrative_watch.yml），
比如每 30 分钟一次。
"""

import os
import sys
import json
import requests

# ------------ 配置：按需修改 ------------
# 你想盯的 KOL，填 X 用户名（不带 @）
KOL_USERNAMES = [
    "example_kol_1",
    "example_kol_2",
]

# LunarCrush 热度榜：监控 Top N 币种，排名跳升超过 RANK_JUMP_THRESHOLD 名就报警
LUNARCRUSH_TOP_N = 30
RANK_JUMP_THRESHOLD = 10  # 排名比上次监控上升超过这么多名，就当作"热度突增"

STATE_FILE = os.path.join(os.path.dirname(__file__), "state.json")
# --------------------------------------------

X_BEARER_TOKEN = os.environ.get("X_BEARER_TOKEN", "")
LUNARCRUSH_API_KEY = os.environ.get("LUNARCRUSH_API_KEY", "")
SERVERCHAN_KEY = os.environ.get("SERVERCHAN_KEY", "")


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"last_tweet_id": {}, "last_rank": {}}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ---------- 信号一：KOL 新推文 ----------
def fetch_new_tweets(state):
    if not X_BEARER_TOKEN:
        print("[跳过] 未配置 X_BEARER_TOKEN，跳过 KOL 推文监控")
        return []

    headers = {"Authorization": f"Bearer {X_BEARER_TOKEN}"}
    alerts = []

    for username in KOL_USERNAMES:
        try:
            # 1. 用户名 -> user_id
            user_resp = requests.get(
                f"https://api.x.com/2/users/by/username/{username}",
                headers=headers,
                timeout=10,
            )
            user_resp.raise_for_status()
            user_id = user_resp.json()["data"]["id"]

            # 2. 拉取最新推文
            since_id = state["last_tweet_id"].get(username)
            params = {"max_results": 5, "tweet.fields": "created_at"}
            if since_id:
                params["since_id"] = since_id

            tweets_resp = requests.get(
                f"https://api.x.com/2/users/{user_id}/tweets",
                headers=headers,
                params=params,
                timeout=10,
            )
            tweets_resp.raise_for_status()
            tweets = tweets_resp.json().get("data", [])

            if tweets:
                # 记录最新的 tweet id 作为下次的 since_id
                state["last_tweet_id"][username] = tweets[0]["id"]
                for t in reversed(tweets):  # 按时间正序推送
                    alerts.append(
                        f"@{username} 发新推文：\n{t['text']}\n"
                        f"https://x.com/{username}/status/{t['id']}"
                    )
        except Exception as e:
            print(f"[警告] 抓取 @{username} 推文失败: {e}")

    return alerts


# ---------- 信号二：LunarCrush 热度榜跳变 ----------
def fetch_narrative_shifts(state):
    if not LUNARCRUSH_API_KEY:
        print("[跳过] 未配置 LUNARCRUSH_API_KEY，跳过热度榜监控")
        return []

    alerts = []
    try:
        resp = requests.get(
            "https://lunarcrush.com/api4/public/coins/list/v2",
            headers={"Authorization": f"Bearer {LUNARCRUSH_API_KEY}"},
            params={"sort": "galaxy_score", "limit": LUNARCRUSH_TOP_N},
            timeout=15,
        )
        resp.raise_for_status()
        coins = resp.json().get("data", [])

        last_rank = state.get("last_rank", {})
        new_rank = {}

        for idx, coin in enumerate(coins, start=1):
            symbol = coin.get("symbol", "")
            new_rank[symbol] = idx
            prev = last_rank.get(symbol)

            if prev is None and last_rank:
                # 首次进入榜单（且不是第一次运行）
                alerts.append(f"🆕 {symbol} 首次进入热度榜 Top{LUNARCRUSH_TOP_N}，当前排名第 {idx}")
            elif prev is not None and (prev - idx) >= RANK_JUMP_THRESHOLD:
                alerts.append(
                    f"📈 {symbol} 热度排名跳升：第 {prev} 名 -> 第 {idx} 名"
                )

        state["last_rank"] = new_rank
    except Exception as e:
        print(f"[警告] 拉取 LunarCrush 榜单失败: {e}")

    return alerts


# ---------- 推送 ----------
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

    tweet_alerts = fetch_new_tweets(state)
    narrative_alerts = fetch_narrative_shifts(state)

    all_alerts = []
    if tweet_alerts:
        all_alerts.append("【KOL 新动态】\n" + "\n\n".join(tweet_alerts))
    if narrative_alerts:
        all_alerts.append("【热度榜异动】\n" + "\n".join(narrative_alerts))

    save_state(state)

    if not all_alerts:
        print("本次没有新信号，不推送")
        return

    title = f"链上叙事提醒：{len(tweet_alerts)}条新推文 / {len(narrative_alerts)}条热度异动"
    content = "\n\n---\n\n".join(all_alerts)
    print(title)
    print(content)
    push_to_wechat(title, content)


if __name__ == "__main__":
    main()
