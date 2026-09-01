#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KOL 推文监控 -> 微信推送（付费 X API 版）

【省钱设计说明】
X API 现在是按量付费（约 $0.005/条读取），这个脚本做了两处优化控制成本：

1. 缓存 user_id：用户名 -> user_id 的查询只在第一次运行时执行一次，
   之后的运行直接从 state.json 里读缓存的 user_id，不重复查询。

2. 用 since_id 增量拉取：每次只拉"上次检查之后的新推文"，
   如果KOL没发新推文，这次检查基本不产生费用（读取到0条）。
   只有真的抓到新推文时才会产生费用，按实际条数计费。

依赖：
- X_BEARER_TOKEN -> https://developer.x.com/ （按量付费，需要绑定支付方式）
- SERVERCHAN_KEY -> https://sct.ftqq.com/

用法：
    export X_BEARER_TOKEN="..."
    export SERVERCHAN_KEY="..."
    python3 kol_watch.py
"""

import os
import sys
import json
import requests

# ------------ 配置：把下面的用户名换成你真正想监控的 X 账号 ------------
KOL_USERNAMES = [
    "jiujinshan2022",
    "fmpumpguy",
    "zhaoxiao5781",
    "WallStreet0Name",
    "hexiecs",
    "CycleStudies",
    "brc20niubi",
    "lanaaielsa",
    "xiaomustock",
    "WallStreetAiBot",
]

MAX_RESULTS_PER_CHECK = 5  # 每次最多拉取多少条新推文（越小越省钱，但极端情况下可能漏掉）
STATE_FILE = os.path.join(os.path.dirname(__file__), "kol_state.json")
# --------------------------------------------------------------------

X_BEARER_TOKEN = os.environ.get("X_BEARER_TOKEN", "")
SERVERCHAN_KEY = os.environ.get("SERVERCHAN_KEY", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

X_API_BASE = "https://api.x.com/2"


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"user_ids": {}, "last_tweet_id": {}}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def get_user_id(username, state, headers):
    """查用户名对应的user_id，优先用缓存，没有才调用API（只查一次）"""
    cached = state["user_ids"].get(username)
    if cached:
        return cached

    try:
        resp = requests.get(
            f"{X_API_BASE}/users/by/username/{username}",
            headers=headers,
            timeout=10,
        )
        if resp.status_code != 200:
            print(f"[警告] 查询 @{username} 的user_id失败，状态码: {resp.status_code}，返回: {resp.text[:200]}")
            return None
        user_id = resp.json()["data"]["id"]
        state["user_ids"][username] = user_id
        print(f"[调试] @{username} 的user_id已缓存: {user_id}")
        return user_id
    except Exception as e:
        print(f"[警告] 查询 @{username} 的user_id出错: {e}")
        return None


def fetch_new_tweets(username, user_id, state, headers):
    """增量拉取某个KOL自上次检查以来的新推文"""
    since_id = state["last_tweet_id"].get(username)
    params = {
        "max_results": MAX_RESULTS_PER_CHECK,
        "tweet.fields": "created_at",
        "exclude": "retweets,replies",
    }
    if since_id:
        params["since_id"] = since_id

    try:
        resp = requests.get(
            f"{X_API_BASE}/users/{user_id}/tweets",
            headers=headers,
            params=params,
            timeout=10,
        )
        if resp.status_code != 200:
            print(f"[警告] 拉取 @{username} 推文失败，状态码: {resp.status_code}，返回: {resp.text[:200]}")
            return []

        tweets = resp.json().get("data", [])
        if tweets:
            # 记录最新的tweet id，作为下次检查的起点
            state["last_tweet_id"][username] = tweets[0]["id"]
        return tweets
    except Exception as e:
        print(f"[警告] 拉取 @{username} 推文出错: {e}")
        return []


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

def push_to_telegram(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[提示] 未配置 TELEGRAM_BOT_TOKEN 或 TELEGRAM_CHAT_ID，跳过Telegram推送")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(
            url,
            data={"chat_id": TELEGRAM_CHAT_ID, "text": text, "disable_web_page_preview": True},
            timeout=10,
        )
        result = resp.json()
        if result.get("ok"):
            print("[成功] 已推送到Telegram")
        else:
            print(f"[失败-Telegram] {result}")
    except Exception as e:
        print(f"[失败-Telegram] {e}")

def main():
    if not X_BEARER_TOKEN:
        print("[错误] 未配置 X_BEARER_TOKEN")
        sys.exit(1)

    headers = {"Authorization": f"Bearer {X_BEARER_TOKEN}"}
    state = load_state()

    all_new_tweets = []  # [(username, tweet), ...]
    is_first_run_for_all = not state["last_tweet_id"]

    for username in KOL_USERNAMES:
        user_id = get_user_id(username, state, headers)
        if not user_id:
            continue

        tweets = fetch_new_tweets(username, user_id, state, headers)
        print(f"[调试] @{username} 拉取到 {len(tweets)} 条新推文")

        for t in tweets:
            all_new_tweets.append((username, t))

    save_state(state)

    if is_first_run_for_all:
        print("首次运行，仅建立基线（记录每个账号当前最新推文ID），不推送")
        return

    if not all_new_tweets:
        print("本次没有新推文，不推送")
        return

    lines = []
    for username, t in all_new_tweets:
        lines.append(f"📝 @{username}：\n{t['text']}\nhttps://x.com/{username}/status/{t['id']}")
        lines.append("")

    content = "\n".join(lines)
    title = f"KOL推文提醒：{len(all_new_tweets)}条新推文"
    print(title)
    print(content)
    push_to_wechat(title, content)
    push_to_telegram(f"{title}\n\n{content}")


if __name__ == "__main__":
    main()
