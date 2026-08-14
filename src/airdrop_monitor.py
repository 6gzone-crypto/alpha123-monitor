#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Alpha123 空投监控推送脚本 (GitHub Actions 适配版)

监控 https://alpha123.uk/zh/ 的官方数据 API，当出现新空投（今日空投 / 空投预告）
时，通过虾推啥(xtuis.cn)推送通道发送微信通知。

数据源：https://alpha123.uk/api/data?fresh=1
状态文件：state.json  记录已推送过的空投，避免重复通知（由 git commit 持久化）

运行模式：
  python3 airdrop_monitor.py            # 空投检查模式
  python3 airdrop_monitor.py --heartbeat # 心跳检查模式

环境变量（GitHub Actions Secrets）：
  XTUIS_TOKEN  虾推啥 Token (必填)
  STATE_FILE   state.json 路径 (可选, 默认 ./state.json)
"""

import json
import os
import sys
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone, timedelta

# ======================== 基础配置 ========================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.environ.get("STATE_FILE", os.path.join(SCRIPT_DIR, "state.json"))

API_URL = "https://alpha123.uk/api/data?fresh=1"
PAGE_URL = "https://alpha123.uk/zh/"
HISTORY_URL = "https://alpha123.uk/zh/history.html"

BJT = timezone(timedelta(hours=8))

HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Referer": "https://alpha123.uk/zh/",
    "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

HEARTBEAT_THRESHOLD_HOURS = 2
# =========================================================

def now_bjt():
    return datetime.now(BJT)

def log(msg):
    print(f"[{now_bjt().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)

# ---------- 网络抓取 ----------
def fetch_json(url, timeout=30):
    req = urllib.request.Request(url, headers=HTTP_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    return json.loads(raw)

# ---------- 空投数据解析 ----------
def airdrop_id(a):
    """细指纹：token|date|time|points，用于推送去重。
    任一字段从空补全（或调整）都会改变指纹 -> 再推一次，实现"先粗略后精准"。"""
    token = str(a.get("token") or "").strip()
    date = str(a.get("date") or "").strip()
    t = str(a.get("time") or a.get("beijing_time") or "").strip()
    pts = str(a.get("points") or "").strip()
    key = f"{token}|{date}|{t}|{pts}"
    if key.replace("|", "") == "":
        key = f"{a.get('name') or 'unknown'}|{date}|{t}|{pts}"
    return key

def airdrop_coarse_id(a):
    """粗指纹：token|date，用于累计计数。
    同一空投的粗略版/精准版视为同一个，只增不减。"""
    token = str(a.get("token") or "").strip()
    date = str(a.get("date") or "").strip()
    if not token and not date:
        return f"{a.get('name') or 'unknown'}|{date}"
    return f"{token}|{date}"

def airdrop_category(a, today_str):
    date = str(a.get("date") or "")
    if date == today_str:
        return "today"
    if date and date > today_str:
        return "upcoming"
    if not date:
        return "today"
    return "upcoming"

def airdrop_title(a):
    token = a.get("token") or ""
    name = a.get("name") or ""
    if token and name:
        return f"{token} · {name}"
    return token or name or "未知项目"

def airdrop_brief(a):
    parts = [f"项目: {airdrop_title(a)}"]
    if a.get("points"):
        parts.append(f"积分: {a.get('points')}")
    if a.get("amount"):
        parts.append(f"数量: {a.get('amount')}")
    if a.get("total_amount"):
        parts.append(f"总量: {a.get('total_amount')}")
    when = []
    if a.get("beijing_date") or a.get("date"):
        when.append(str(a.get("beijing_date") or a.get("date")))
    if a.get("beijing_time") or a.get("time"):
        when.append(str(a.get("beijing_time") or a.get("time")))
    if when:
        parts.append("时间: " + " ".join(when) + " (北京时间)")
    if a.get("status"):
        parts.append(f"状态: {a.get('status')}")
    return "\n".join(parts)

# ---------- 状态文件 ----------
def load_state():
    if not os.path.exists(STATE_FILE):
        return None
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def save_state(state):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)

# ---------- 推送通道 ----------
def get_token():
    """优先从环境变量读取 Token（GitHub Actions Secrets），便于本地调试也可用环境变量。"""
    token = os.environ.get("XTUIS_TOKEN", "").strip()
    if token:
        return token
    log("✗ 未设置 XTUIS_TOKEN 环境变量")
    return ""

def push_xtuis(token, title, content):
    """虾推啥 (xtuis.cn)：POST https://wx.xtuis.cn/{token}.send
    微信卡片只保留数字+符号(中文/字母/emoji被过滤)，title 需至少含1个数字。
    编码方案：>空投 #预告 ^_^1正常 !!!异常 !!!!从未运行"""
    if not token:
        return False, "缺少 token"
    url = f"https://wx.xtuis.cn/{urllib.parse.quote(token)}.send"
    payload = urllib.parse.urlencode({"text": title, "desp": content}).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status == 200 and '"code":200' in body, f"HTTP {resp.status} {body[:120]}"
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else ""
        return False, f"HTTP {e.code} {body[:120]}"
    except Exception as e:
        return False, str(e)

def push(title, content):
    token = get_token()
    if not token:
        return False
    ok, info = push_xtuis(token, title, content)
    log(f"{'✓' if ok else '✗'} [xtuis] {info}")
    return ok

# ---------- 主流程 ----------
def main():
    today_str = now_bjt().strftime("%Y-%m-%d")

    try:
        data = fetch_json(API_URL)
    except Exception as e:
        log(f"✗ 抓取 API 失败: {e}")
        return 1

    airdrops = data.get("airdrops") or []
    log(f"获取到 {len(airdrops)} 条空投记录 (last_rollover_date={data.get('last_rollover_date')})")

    current = {}
    for a in airdrops:
        aid = airdrop_id(a)
        cat = airdrop_category(a, today_str)
        current[aid] = {"data": a, "category": cat}

    prev_state = load_state()
    is_first_run = prev_state is None

    if is_first_run:
        seen = list(current.keys())
        seen_coarse = list({airdrop_coarse_id(v["data"]) for v in current.values()})
        save_state({
            "last_check": now_bjt().isoformat(),
            "last_success": now_bjt().isoformat(),
            "seen_ids": seen,
            "seen_airdrops": seen_coarse,
        })
        log(f"首次运行，记录当前 {len(seen)} 条空投为已读基线。累计监测到 {len(seen_coarse)} 个不同空投。")

        today_items = [v for v in current.values() if v["category"] == "today"]
        upcoming_items = [v for v in current.values() if v["category"] == "upcoming"]
        total = len(today_items) + len(upcoming_items)
        title = f"*{total}"
        lines = [f"✅ Alpha123 空投监控已启动",
                 f"当前时间: {now_bjt().strftime('%Y-%m-%d %H:%M')} (北京时间)",
                 f"今日空投: {len(today_items)} 个",
                 f"空投预告: {len(upcoming_items)} 个"]
        if today_items:
            lines.append("\n--- 今日空投 ---")
            for v in today_items:
                lines.append(airdrop_brief(v["data"]))
        if upcoming_items:
            lines.append("\n--- 空投预告 ---")
            for v in upcoming_items:
                lines.append(airdrop_brief(v["data"]))
        if not today_items and not upcoming_items:
            lines.append("\n目前暂无空投，将在出现新空投时自动推送。")
        lines.append(f"\n详情: {PAGE_URL}")
        push(title, "\n".join(lines))
        return 0

    seen_ids = set(prev_state.get("seen_ids", []))
    seen_airdrops = set(prev_state.get("seen_airdrops", []))
    new_items = []
    for aid, info in current.items():
        if aid not in seen_ids:
            new_items.append(info)
            seen_airdrops.add(airdrop_coarse_id(info["data"]))

    save_state({
        "last_check": now_bjt().isoformat(),
        "last_success": now_bjt().isoformat(),
        "seen_ids": list(current.keys()),
        "seen_airdrops": list(seen_airdrops),
    })

    if not new_items:
        log("无新增空投。")
        return 0

    log(f"发现 {len(new_items)} 条新空投，开始推送...")
    new_today = [v for v in new_items if v["category"] == "today"]
    new_upcoming = [v for v in new_items if v["category"] == "upcoming"]

    if new_today:
        first = new_today[0]["data"]
        pts = str(first.get("points") or "")
        t = str(first.get("beijing_time") or first.get("time") or "")
        title = f">{pts} {t}"
        head = " / ".join(airdrop_title(v["data"]) for v in new_today)
        head_line = f"🟢 今日空投: {head}"
    else:
        first = new_upcoming[0]["data"]
        pts = str(first.get("points") or "")
        d = str(first.get("beijing_date") or first.get("date") or "")
        title = f"#{pts} {d}"
        head = " / ".join(airdrop_title(v["data"]) for v in new_upcoming)
        head_line = f"🔵 空投预告: {head}"

    lines = [head_line,
             f"检测到 {len(new_items)} 条新空投",
             f"时间: {now_bjt().strftime('%Y-%m-%d %H:%M')} (北京时间)\n"]
    if new_today:
        lines.append("🟢 今日空投")
        for v in new_today:
            lines.append(airdrop_brief(v["data"]))
            lines.append("")
    if new_upcoming:
        lines.append("🔵 空投预告")
        for v in new_upcoming:
            lines.append(airdrop_brief(v["data"]))
            lines.append("")
    lines.append(f"详情: {PAGE_URL}")
    lines.append(f"历史: {HISTORY_URL}")
    push(title, "\n".join(lines))
    return 0

# ---------- 心跳检查 ----------
def heartbeat():
    now = now_bjt()
    state = load_state()

    if not state:
        msg = (f"❌ Alpha123 空投监控运行异常\n"
               f"当前时间: {now.strftime('%Y-%m-%d %H:%M')} (北京时间)\n"
               f"无法读取运行状态文件，监控可能从未成功运行。\n"
               f"请检查定时任务是否正常触发。")
        log("心跳: 无状态文件，判定异常。")
        push("!!!!9999", msg)
        return 0

    last_success = state.get("last_success")
    if not last_success:
        msg = (f"❌ Alpha123 空投监控运行异常\n"
               f"当前时间: {now.strftime('%Y-%m-%d %H:%M')} (北京时间)\n"
               f"未记录成功运行时间，监控可能未正常运行。")
        log("心跳: 缺少 last_success，判定异常。")
        push("!!!!9999", msg)
        return 0

    try:
        last_dt = datetime.fromisoformat(last_success)
    except Exception:
        msg = (f"❌ Alpha123 空投监控运行异常\n"
               f"当前时间: {now.strftime('%Y-%m-%d %H:%M')} (北京时间)\n"
               f"运行状态记录格式异常: {last_success}")
        log("心跳: last_success 格式异常，判定异常。")
        push("!!!!9999", msg)
        return 0

    delta = now - last_dt
    hours = delta.total_seconds() / 3600
    seen_count = len(state.get("seen_airdrops", []))

    if hours <= HEARTBEAT_THRESHOLD_HOURS:
        mins = int(delta.total_seconds() / 60)
        title = "^_^ 1"
        msg = (f"✅ Alpha123 空投监控运行正常\n"
               f"当前时间: {now.strftime('%Y-%m-%d %H:%M')} (北京时间)\n"
               f"最近一次成功检查: {last_dt.strftime('%Y-%m-%d %H:%M')} ({mins}分钟前)\n"
               f"累计监测到空投数: {seen_count}\n"
               f"监控页面: {PAGE_URL}")
        log(f"心跳: 运行正常（{mins}分钟前成功检查）。")
        push(title, msg)
    else:
        mins = int(delta.total_seconds() / 60)
        title = f"!!!{mins}"
        msg = (f"❌ Alpha123 空投监控运行异常\n"
               f"当前时间: {now.strftime('%Y-%m-%d %H:%M')} (北京时间)\n"
               f"最近一次成功检查: {last_dt.strftime('%Y-%m-%d %H:%M')} ({hours:.1f}小时前)\n"
               f"已超过{HEARTBEAT_THRESHOLD_HOURS}小时未成功运行！\n\n"
               f"可能原因:\n"
               f"· GitHub Actions 未触发\n"
               f"· 网络抓取持续失败\n"
               f"· 网站接口改版\n\n"
               f"请检查 Actions 运行日志。")
        log(f"心跳: 运行异常（{hours:.1f}小时未成功）。")
        push(title, msg)
    return 0

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--heartbeat":
        sys.exit(heartbeat())
    sys.exit(main())
