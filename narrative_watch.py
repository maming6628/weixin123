#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
链上叙事监控 v2 -> 微信推送

【和上一版的区别】
1. 去掉了 X(推特) KOL 推文监控。原因：X 官方 API 从 2026年2月起改成按量付费，
   免费层基本只能发帖、不能读取时间线，免费方案下这条路走不通。
   如果你之后愿意付费开通，可以再加回来（成本大概几美元/月级别，看监控频率）。

2. 叙事监控换成 LunarCrush 的"加密货币类别热门话题榜"接口
   （category/cryptocurrencies/topics），这是按"话题"而不是按"单个币"排名的，
   更贴近"叙事"这个概念（比如"AI币""meme币""RWA"这种主题，而不只是BTC/ETH这种大币）。
   接口自带"1小时前排名""24小时前排名"字段，用来判断"是不是正在被更多人讨论"。

3. 对触发了"正在升温"的话题，额外调用 LunarCrush 的 AI 摘要接口，
   生成一句话说明"这个话题现在在聊什么"，让推送更有实际信息量，不只是干巴巴的排名数字。

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

# ------------ 配置 ------------
TOP_N = 30                # 监控加密类别话题榜前多少名
RANK_JUMP_THRESHOLD = 8    # 排名比"1小时前"跳升超过这个数字，判定为"正在升温"
STATE_FILE = os.path.join(os.path.dirname(__file__), "state.json")
# --------------------------------

LUNARCRUSH_API_KEY = os.environ.get("LUNARCRUSH_API_KEY", "")
SERVERCHAN_KEY = os.environ.get("SERVERCHAN_KEY", "")

CATEGORY_TOPICS_URL = "https://lunarcrush.com/api4/public/category/cryptocurrencies/topics/v1"
TOPIC_WHATSUP_URL = "https://lunarcrush.com/api4/public/topic/{topic}/whatsup/v1"


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"alerted_topics": {}}  # 记录已经提醒过的话题，避免重复推送


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def fetch_crypto_topics():
    """拉取加密货币类别下的热门话题榜"""
    if not LUNARCRUSH_API_KEY:
        print("[错误] 未配置 LUNARCRUSH_API_KEY")
        return []

    headers = {"Authorization": f"Bearer {LUNARCRUSH_API_KEY}"}
    resp = requests.get(CATEGORY_TOPICS_URL, headers=headers, timeout=15)
    print(f"[调试] 请求话题榜接口，状态码: {resp.status_code}")
    resp.raise_for_status()
    data = resp.json().get("data", [])
    print(f"[调试] 拉取到 {len(data)} 个话题")
    return data[:TOP_N]


def fetch_whatsup(topic):
    """调用AI摘要接口，获取某个话题当前在聊什么（一句话）"""
    try:
        headers = {"Authorization": f"Bearer {LUNARCRUSH_API_KEY}"}
        url = TOPIC_WHATSUP_URL.format(topic=topic)
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        return resp.json().get("summary", "")
    except Exception as e:
        print(f"[警告] 获取 {topic} 摘要失败: {e}")
        return ""


def detect_surging_topics(topics, state):
    """找出排名比1小时前明显跳升的话题，且没有在最近提醒过的"""
    alerted = state.get("alerted_topics", {})
    surging = []

    for item in topics:
        topic = item.get("topic", "")
        title = item.get("title", topic)
        rank = item.get("topic_rank")
        rank_1h_prev = item.get("topic_rank_1h_previous")

        if not topic or rank is None or rank_1h_prev is None:
            continue

        jump = rank_1h_prev - rank  # 正数表示排名上升(数字变小=更火)
        if jump >= RANK_JUMP_THRESHOLD:
            # 避免同一个话题短时间内被反复提醒：如果上次提醒时排名跟这次差不多，跳过
            last_alerted_rank = alerted.get(topic)
            if last_alerted_rank is not None and abs(last_alerted_rank - rank) < 3:
                continue
            surging.append((topic, title, rank, rank_1h_prev, jump))
            alerted[topic] = rank

    state["alerted_topics"] = alerted
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

    topics = fetch_crypto_topics()
    if not topics:
        print("[警告] 没拉到话题数据，检查 LUNARCRUSH_API_KEY 是否正确/是否有权限访问该接口")
        save_state(state)
        sys.exit(1)

    surging = detect_surging_topics(topics, state)
    save_state(state)

    if not surging:
        print("本次没有明显升温的叙事话题，不推送")
        return

    lines = []
    for topic, title, rank, prev_rank, jump in surging:
        lines.append(f"📈 {title}：第 {prev_rank} 名(1小时前) -> 第 {rank} 名")
        summary = fetch_whatsup(topic)
        if summary:
            lines.append(f"   💬 {summary}")
        lines.append("")

    content = "\n".join(lines)
    title = f"链上叙事提醒：{len(surging)}个话题正在升温"
    print(title)
    print(content)
    push_to_wechat(title, content)


if __name__ == "__main__":
    main()
