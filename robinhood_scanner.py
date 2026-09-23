"""
Robinhood Chain 新币扫描器（仿 PONSI BOT 的筛选逻辑）
- 数据：GeckoTerminal（network=robinhood）+ Blockscout + GoPlus（若支持 4663）+ X 计数
- 条件：市值≥10万、上线≥10分钟、合约开源、LP/流动性检查、非蜜罐、持仓分布健康、X 有讨论
- 推送：Telegram + Server酱；已推送过的池子记录在 seen_rh.json，避免重复
机器人只负责筛，买不买你自己决定。
"""
import json, os, time
from datetime import datetime, timezone
import requests

# ============ 可调参数 ============
MIN_MCAP = 10_000          # 市值下限 (USD) — 临时调低测试推送，测试完成后需改回 100_000
MIN_AGE_MIN = 10            # 上线至少 N 分钟
MAX_AGE_HOURS = 48          # 太老的不看
MIN_LIQ = 20_000            # 流动性下限 (USD)
MIN_LIQ_RATIO = 0.05        # 流动性 / 市值 ≥ 5%
MIN_SELLS_1H = 5            # 1h 内至少有 N 笔卖出（能卖 = 大概率不是蜜罐）
MAX_TOP10_PCT = 40          # 前10持仓合计上限 (%)
MIN_HOLDERS = 20            # 最少持币地址 — 临时调低测试推送，测试完成后需改回 150
MIN_X_MENTIONS = 0          # 过去24h X 上提及次数下限（无 X token 时跳过此项） — 临时调低测试推送，测试完成后需改回 5
MAX_TAX = 10                # 买/卖税上限 (%)
NEW_POOL_PAGES = 3          # 扫几页 new_pools（每页20个）

NET = "robinhood"
GT = "https://api.geckoterminal.com/api/v2"
BLOCKSCOUT = "https://robinhoodchain.blockscout.com/api/v2"
GOPLUS = "https://api.gopluslabs.io/api/v1/token_security/4663"
STATE_FILE = "seen_rh.json"

TG_TOKEN = os.getenv("TG_BOT_TOKEN")
TG_CHAT = os.getenv("TG_CHAT_ID")
SCT_KEY = os.getenv("SERVERCHAN_KEY")
X_BEARER = os.getenv("X_BEARER_TOKEN")

S = requests.Session()
S.headers["Accept"] = "application/json"


def get(url, params=None, headers=None, pause=2.1):
    """GeckoTerminal 免费版约 30 次/分钟，默认每次请求后歇 2.1 秒"""
    try:
        r = S.get(url, params=params, headers=headers, timeout=20)
        time.sleep(pause)
        if r.status_code == 200:
            return r.json()
        print(f"[{r.status_code}] {url}")
    except Exception as e:
        print(f"[ERR] {url}: {e}")
    return None


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


# ============ 1. 拉池子 ============
def fetch_pools():
    pools = {}
    urls = [f"{GT}/networks/{NET}/new_pools?page={p}&include=base_token"
            for p in range(1, NEW_POOL_PAGES + 1)]
    urls.append(f"{GT}/networks/{NET}/trending_pools?include=base_token")
    for u in urls:
        d = get(u)
        if not d:
            continue
        tokens = {t["id"]: t["attributes"] for t in d.get("included", [])}
        for p in d.get("data", []):
            a = p["attributes"]
            base_id = p["relationships"]["base_token"]["data"]["id"]
            a["_token_addr"] = base_id.split("_", 1)[1]
            a["_symbol"] = tokens.get(base_id, {}).get("symbol", a["name"].split(" /")[0])
            pools[a["address"]] = a
    return list(pools.values())


# ============ 2. 基础过滤（不花额外请求） ============
def basic_filter(a):
    created = datetime.fromisoformat(a["pool_created_at"].replace("Z", "+00:00"))
    age_min = (datetime.now(timezone.utc) - created).total_seconds() / 60
    mcap = f(a.get("market_cap_usd")) or f(a.get("fdv_usd"))
    liq = f(a.get("reserve_in_usd"))
    sells_1h = a.get("transactions", {}).get("h1", {}).get("sells", 0)
    a.update(_age_min=age_min, _mcap=mcap, _liq=liq)

    sym = a.get("_symbol")
    if age_min < MIN_AGE_MIN or age_min > MAX_AGE_HOURS * 60:
        print(f"✗ {sym}: 上线时间不符 {age_min/60:.1f}h"); return False
    if mcap < MIN_MCAP:
        print(f"✗ {sym}: 市值太低 {fmt_usd(mcap)}"); return False
    if liq < MIN_LIQ or liq / mcap < MIN_LIQ_RATIO:
        print(f"✗ {sym}: 流动性不足 {fmt_usd(liq)} ({liq/mcap*100:.1f}%)"); return False
    if sells_1h < MIN_SELLS_1H:
        print(f"✗ {sym}: 1h卖出太少 ({sells_1h})"); return False
    return True


# ============ 3. 深度检查 ============
def goplus_check(addr):
    """返回 (是否通过, 说明)；GoPlus 不支持该链时返回 (None, ...)"""
    d = get(GOPLUS, {"contract_addresses": addr}, pause=1)
    info = (d or {}).get("result", {}).get(addr.lower())
    if not info:
        return None, "GoPlus无数据"
    if info.get("is_honeypot") == "1":
        return False, "蜜罐"
    for k in ("hidden_owner", "can_take_back_ownership", "owner_change_balance",
              "selfdestruct", "is_blacklisted", "transfer_pausable"):
        if info.get(k) == "1":
            return False, f"风险:{k}"
    if max(f(info.get("buy_tax")), f(info.get("sell_tax"))) * 100 > MAX_TAX:
        return False, "税太高"
    locked = sum(f(h.get("percent")) for h in info.get("lp_holders", []) if h.get("is_locked") == 1)
    return True, f"LP锁定{locked*100:.0f}%" if locked else "LP未锁/非V2池"


def contract_verified(addr):
    d = get(f"{BLOCKSCOUT}/smart-contracts/{addr}", pause=0.5)
    return bool(d and d.get("is_verified"))


def holder_check(addr):
    d = get(f"{GT}/networks/{NET}/tokens/{addr}/info")
    attrs = (d or {}).get("data", {}).get("attributes", {})
    h = attrs.get("holders") or {}
    count = h.get("count") or 0
    top10 = f((h.get("distribution_percentage") or {}).get("top_10"))
    if not count:  # GT 没有就用 Blockscout
        b = get(f"{BLOCKSCOUT}/tokens/{addr}", pause=0.5) or {}
        count = int(f(b.get("holders_count") or b.get("holders")))
    ok = count >= MIN_HOLDERS and (top10 == 0 or top10 <= MAX_TOP10_PCT)
    return ok, count, top10, attrs.get("gt_score")


def x_mentions(symbol, addr):
    if not X_BEARER:
        return None
    q = f'(${symbol} OR "{addr}") -is:retweet'
    d = get("https://api.x.com/2/tweets/counts/recent",
            {"query": q, "granularity": "day"},
            headers={"Authorization": f"Bearer {X_BEARER}"}, pause=1)
    if d is None:  # 请求失败：返回 None（未查），不要当作 0 次提及
        return None
    return d.get("meta", {}).get("total_tweet_count", 0)


# ============ 4. 推送 ============
def push(title, body):
    if TG_TOKEN and TG_CHAT:
        S.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
               json={"chat_id": TG_CHAT, "text": f"{title}\n\n{body}",
                     "disable_web_page_preview": True}, timeout=20)
    if SCT_KEY:
        S.post(f"https://sctapi.ftqq.com/{SCT_KEY}.send",
               data={"title": title[:32], "desp": body.replace("\n", "\n\n")}, timeout=20)


def fmt_usd(x):
    return f"${x/1e6:.2f}M" if x >= 1e6 else f"${x/1e3:.1f}K"


# ============ 主流程 ============
def main():
    seen = json.load(open(STATE_FILE)) if os.path.exists(STATE_FILE) else {}
    pools = fetch_pools()
    print(f"拉到 {len(pools)} 个池子")
    cands = [a for a in pools if a["address"] not in seen and basic_filter(a)]
    print(f"基础过滤后 {len(cands)} 个")

    hits = []
    for a in cands:
        addr, sym = a["_token_addr"], a["_symbol"]
        gp_ok, gp_note = goplus_check(addr)
        if gp_ok is False:
            print(f"✗ {sym}: {gp_note}"); continue
        if not contract_verified(addr):
            print(f"✗ {sym}: 合约未开源"); continue
        h_ok, holders, top10, gt_score = holder_check(addr)
        if not h_ok:
            print(f"✗ {sym}: 持仓不健康 holders={holders} top10={top10}"); continue
        xm = x_mentions(sym, addr)
        if xm is not None and xm < MIN_X_MENTIONS:
            print(f"✗ {sym}: X讨论太少({xm})"); continue

        tx = a["transactions"]["h1"]
        pc = a.get("price_change_percentage", {})
        hits.append(
            f"🟢 {sym}  ({a['name']})\n"
            f"市值 {fmt_usd(a['_mcap'])} | 流动性 {fmt_usd(a['_liq'])} | 上线 {a['_age_min']/60:.1f}h\n"
            f"1h 买/卖 {tx['buys']}/{tx['sells']} | 5m {pc.get('m5')}% 1h {pc.get('h1')}% 24h {pc.get('h24')}%\n"
            f"24h量 {fmt_usd(f(a['volume_usd']['h24']))} | 持币 {holders} | 前10 {top10:.0f}% | GT分 {gt_score}\n"
            f"安全: {gp_note} | X提及24h: {xm if xm is not None else '未查'}\n"
            f"CA: {addr}\n"
            f"https://www.geckoterminal.com/{NET}/pools/{a['address']}"
        )
        seen[a["address"]] = int(time.time())

    if hits:
        push(f"Robinhood链 {len(hits)} 个币过筛", "\n\n".join(hits))
        print("\n\n".join(hits))

    # 只保留 3 天内的记录，防止文件无限变大
    cutoff = time.time() - 3 * 86400
    seen = {k: v for k, v in seen.items() if v > cutoff}
    json.dump(seen, open(STATE_FILE, "w"))


if __name__ == "__main__":
    main()
