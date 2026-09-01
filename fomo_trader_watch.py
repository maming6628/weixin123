#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FOMO(fomo.family) 交易员动态监控 -> 微信 + Telegram 双推送

⚠️ 重要说明：
用的是第三方非官方数据服务 fomoapi.io（不是fomo.family官方提供），
该服务官网自己声明"Independent, unofficial tool - not affiliated with,
endorsed by, or sponsored by fomo.family"。数据准确性、接口稳定性都没有
官方保障，fomo一旦改版这个第三方服务可能随时失效或数据不准。

另外，具体返回数据的字段名（比如金额、方向、代币符号叫什么字段名）
是根据官网营销页面的描述做的合理猜测，不是根据完整官方文档写的。
脚本会打印每次拉到的第一条原始数据（RAW JSON），如果推送内容看起来
不对（比如金额显示成了奇怪的值），把这行日志截图发给开发者，
照着实际字段名调整代码里的 .get() 部分即可。

依赖：
- FOMO_API_KEY        -> https://fomoapi.io/dashboard 免费注册获取
- SERVERCHAN_KEY      -> 微信推送，跟其他脚本共用
- TELEGRAM_BOT_TOKEN  -> Telegram推送，跟其他脚本共用
- TELEGRAM_CHAT_ID    -> Telegram推送，跟其他脚本共用

用法：
    export FOMO_API_KEY="..."
    export SERVERCHAN_KEY="..."
    export TELEGRAM_BOT_TOKEN="..."
    export TELEGRAM_CHAT_ID="..."
    python3 fomo_trader_watch.py
"""

import os
import sys
import json
import requests

# ------------ 配置：把下面换成你在fomo.family上关注的交易员handle ------------
TRADER_HANDLES = [
    "fmpumpguy",
    "ether_monk",
]

MAX_TRADES_PER_CHECK = 10   # 每个交易员每次最多检查最近多少条记录
STATE_FILE = os.path.join(os.path.dirname(__file__), "fomo_state.json")
# --------------------------------------------------------------------------

FOMO_API_KEY = os.environ.get("FOMO_API_KEY", "")
SERVERCHAN_KEY = os.environ.get("SERVERCHAN_KEY", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# 官网只公开了 wss://api.fomoapi.io/ws/alerts 这个websocket地址，
# REST接口大概率是同域名，如果实测发现请求失败(比如404)，
# 去 https://fomoapi.io/docs 确认真实的REST base url再改这里
FOMO_API_BASE = "https://api.fomoapi.io"


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"last_trade_id": {}}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def fetch_trades(handle, headers):
    """拉取某个交易员最近的交易记录"""
    try:
        resp = requests.get(
            f"{FOMO_API_BASE}/v2/users/{handle}/trades",
            headers=headers,
            params={"limit": MAX_TRADES_PER_CHECK},
            timeout=15,
        )
        print(f"[调试] 请求 {handle} 的交易记录，状态码: {resp.status_code}")
        if resp.status_code != 200:
            print(f"[警告] 请求失败，返回: {resp.text[:300]}")
            return []

        data = resp.json()
        # 不同API可能把列表包在 data / trades / results 等不同字段里，都尝试一下
        trades = data.get("data") or data.get("trades") or data.get("results") or []
        if not isinstance(trades, list):
            print(f"[警告] {handle} 返回的数据结构不是预期的列表，原始返回: {json.dumps(data)[:500]}")
            return []

        if trades:
            print(f"[调试] {handle} 第一条原始数据(用于核对字段名): {json.dumps(trades[0], ensure_ascii=False)[:500]}")

        return trades
    except Exception as e:
        print(f"[警告] 拉取 {handle} 交易记录出错: {e}")
        return []


def get_trade_id(trade):
    """尝试从交易记录里找一个唯一标识，用于去重"""
    for key in ("id", "tx_hash", "signature", "hash", "trade_id"):
        if trade.get(key):
            return str(trade[key])
    # 都没有的话，用整条记录的内容做一个简易标识（兜底方案）
    return str(hash(json.dumps(trade, sort_keys=True)))


def format_trade(handle, trade):
    """把一条交易记录格式化成推送文本，字段名是根据官网描述猜测的，
    如果显示不对照着RAW JSON日志调整这里的 .get() 键名"""
    side = trade.get("side") or trade.get("type") or trade.get("action") or "交易"
    token = (
        trade.get("token_symbol")
        or trade.get("symbol")
        or trade.get("token", {}).get("symbol") if isinstance(trade.get("token"), dict) else None
    ) or "未知代币"
    amount_usd = trade.get("amount_usd") or trade.get("value_usd") or trade.get("usd_value") or 0
    thesis = trade.get("thesis") or trade.get("note") or trade.get("comment") or ""

    text = f"{handle} {side} {token}"
    if amount_usd:
        text += f"（约${float(amount_usd):,.0f}）"
    if thesis:
        text += f"\n观点：{thesis}"
    return text


def push_to_wechat(title, content):
    if not SERVERCHAN_KEY:
        return
    url = f"https://sctapi.ftqq.com/{SERVERCHAN_KEY}.send"
    try:
        resp = requests.post(url, data={"title": title, "desp": content}, timeout=10)
        result = resp.json()
        if result.get("code") == 0:
            print("[成功] 已推送到微信")
        else:
            print(f"[失败-微信] {result}")
    except Exception as e:
        print(f"[失败-微信] {e}")


def push_to_telegram(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
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
    if not FOMO_API_KEY:
        print("[错误] 未配置 FOMO_API_KEY")
        sys.exit(1)

    headers = {"Authorization": f"Bearer {FOMO_API_KEY}"}
    state = load_state()
    is_first_run = not state.get("last_trade_id")

    all_new_texts = []

    for handle in TRADER_HANDLES:
        trades = fetch_trades(handle, headers)
        if not trades:
            continue

        last_id = state["last_trade_id"].get(handle)
        new_trades = []

        for trade in trades:
            tid = get_trade_id(trade)
            if tid == last_id:
                break  # 遇到上次记录的位置，后面的都是旧的，停止
            new_trades.append((tid, trade))

        if trades:
            # 记录这次最新一条的id，作为下次的起点
            state["last_trade_id"][handle] = get_trade_id(trades[0])

        for tid, trade in reversed(new_trades):  # 按时间正序推送
            all_new_texts.append(format_trade(handle, trade))

    save_state(state)

    if is_first_run:
        print("首次运行，仅建立基线，不推送")
        return

    if not all_new_texts:
        print("本次没有新动态，不推送")
        return

    content = "\n\n".join(all_new_texts)
    title = f"FOMO交易员动态：{len(all_new_texts)}条新动态"
    print(title)
    print(content)

    push_to_wechat(title, content)
    push_to_telegram(f"{title}\n\n{content}")


if __name__ == "__main__":
    main()
