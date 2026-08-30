#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
微博热搜"新叙事"实时监控 -> 微信推送

跟之前那个"每天固定推一次Top10"的脚本不同，这个是专门抓"刚冒出来的新热点/
突然爆的梗"，比如"牛来""我的女友景甜"这种一开始名次不高、但迅速冲上榜的话题。

原理：
- 每次运行都拉一次微博热搜榜（默认盯前50名）
- 跟上一次运行记录的排名（state.json）做对比：
    1) 这次是新出现在榜单里的词 -> 判定为"新热点"，直接推送
    2) 排名比上次大幅上升（跳升超过阈值） -> 判定为"正在爆"，推送
- 只推送"变化"，不会每次都把整个榜单推给你一遍，避免刷屏

配合 GitHub Actions 每 10-15 分钟跑一次，基本能做到"热点一冒头就通知你"。

依赖：
- SERVERCHAN_KEY -> https://sct.ftqq.com/ (微信扫码登录获取 SendKey)

用法：
    export SERVERCHAN_KEY="你的SendKey"
    python3 weibo_narrative_watch.py
"""

import os
import sys
import json
import requests

# ------------ 配置 ------------
TOP_N = 50               # 监控热搜榜前多少名（越大越能提前发现"刚冒头"的话题）
RANK_JUMP_THRESHOLD = 15  # 排名比上次跳升超过这个数字，判定为"正在爆"
STATE_FILE = os.path.join(os.path.dirname(__file__), "weibo_state.json")
# --------------------------------

SERVERCHAN_KEY = os.environ.get("SERVERCHAN_KEY", "")
WEIBO_HOT_API = "https://weibo.com/ajax/side/hotSearch"


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"last_rank": {}}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def fetch_hot_search():
    """抓取微博热搜榜，返回 {关键词: (排名, 热度)}"""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Referer": "https://weibo.com/",
    }
    resp = requests.get(WEIBO_HOT_API, headers=headers, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    items = data.get("data", {}).get("realtime", [])
    current = {}
    for idx, item in enumerate(items[:TOP_N], start=1):
        word = item.get("word") or item.get("word_scheme", "")
        hot = item.get("num", "")
        if word:
            current[word] = (idx, hot)
    return current


def detect_changes(current, last_rank):
    """对比当前榜单和上次记录，找出新热点 / 飙升热点"""
    new_hits = []
    surging = []

    for word, (rank, hot) in current.items():
        prev_rank = last_rank.get(word)
        if prev_rank is None:
            # 首次运行时 last_rank 是空的，不算"新热点"，只建立基线
            if last_rank or STATE_FILE_HAS_HISTORY:
                new_hits.append((word, rank, hot))
        elif (prev_rank - rank) >= RANK_JUMP_THRESHOLD:
            surging.append((word, prev_rank, rank, hot))

    return new_hits, surging


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
    global STATE_FILE_HAS_HISTORY
    state = load_state()
    last_rank = state.get("last_rank", {})
    STATE_FILE_HAS_HISTORY = bool(last_rank)

    current = fetch_hot_search()
    if not current:
        print("[警告] 没抓取到热搜数据，微博接口可能变了或被限制访问")
        sys.exit(1)

    new_hits, surging = detect_changes(current, last_rank)

    # 更新排名基线
    state["last_rank"] = {word: rank for word, (rank, hot) in current.items()}
    save_state(state)

    if not new_hits and not surging:
        print("本次没有新热点或飙升热点，不推送")
        return

    lines = []
    if new_hits:
        lines.append("【新上榜】")
        # 新热点按排名从高到低（数字越小越靠前）排序展示
        for word, rank, hot in sorted(new_hits, key=lambda x: x[1]):
            lines.append(f"🆕 {word}（第 {rank} 名，热度 {hot}）")
        lines.append("")

    if surging:
        lines.append("【正在飙升】")
        for word, prev_rank, rank, hot in sorted(surging, key=lambda x: x[2]):
            lines.append(f"📈 {word}：第 {prev_rank} 名 -> 第 {rank} 名（热度 {hot}）")

    content = "\n".join(lines)
    title = f"微博热点提醒：{len(new_hits)}个新上榜 / {len(surging)}个飙升"
    print(title)
    print(content)
    push_to_wechat(title, content)


if __name__ == "__main__":
    main()
