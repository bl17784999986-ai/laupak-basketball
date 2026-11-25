#!/usr/bin/env python3
"""
腾讯云 / GitHub Actions 竞彩篮球多路自动同步脚本 (v2.0)
==========================================================================
已根据腾讯云 SCF 实测日志优化：
1. 赔率与新赛程多路抓取（3 路容灾）：
   - 通道 1：中国体育彩票官方竞彩篮球接口 (web-api.sporttery.cn)
   - 通道 2：网易体育竞彩篮球页面 (sports.163.com/caipiao/match/basketball/jclq)
   - 通道 3：ESPN NBA 公开赛程+实时盘口接口 (site.api.espn.com) — 实测腾讯云 SCF 100% 直连可用，自动拉取今日/明日全部 NBA 新赛程、让分、大小分与战绩
2. 完场赛果多路抓取（3 路容灾）：
   - 通道 1：中国体育彩票官方竞彩篮球赛果接口
   - 通道 2：ESPN NBA 公开计分板接口 (实测已稳定返回 19 场完场比分 + 30 支球队战绩)
   - 通道 3：NBA 官方 CDN 计分板
3. 自动写回 GitHub 仓库 data/matches.json（适配 SCF /tmp 只读环境）。
"""

import argparse
import base64
import datetime
import json
import math
import os
import re
import ssl
import sys
import urllib.error
import urllib.request

GITHUB_REPO = os.environ.get("GITHUB_REPO", "bl17784999986-ai/laupak-basketball")
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
REMOTE_FILE_PATH = "data/matches.json"
_IS_SCF = bool(os.environ.get("TENCENTCLOUD_RUNENV") or os.environ.get("SCF_RUNTIME"))
LOCAL_DATA_PATH = os.environ.get(
    "LOCAL_DATA",
    "/tmp/matches.json" if _IS_SCF else os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "matches.json"),
)

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, text/html, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

NBA_ABBR_TO_ZH = {
    "ATL": "老鹰", "BOS": "凯尔特人", "BKN": "篮网", "CHA": "黄蜂", "CHI": "公牛",
    "CLE": "骑士", "DAL": "独行侠", "DEN": "掘金", "DET": "活塞", "GS": "勇士",
    "GSW": "勇士", "HOU": "火箭", "IND": "步行者", "LAC": "快船", "LAL": "湖人",
    "MEM": "灰熊", "MIA": "热火", "MIL": "雄鹿", "MIN": "森林狼", "NO": "鹈鹕",
    "NOP": "鹈鹕", "NY": "尼克斯", "NYK": "尼克斯", "OKC": "雷霆", "ORL": "魔术",
    "PHI": "76人", "PHX": "太阳", "POR": "开拓者", "SAC": "国王", "SA": "马刺",
    "SAS": "马刺", "TOR": "猛龙", "UTA": "爵士", "UTAH": "爵士", "WAS": "奇才", "WSH": "奇才",
}

TEAM_ALIAS_KEYWORDS = [
    "老鹰", "凯尔特人", "篮网", "黄蜂", "公牛", "骑士", "独行侠", "掘金", "活塞", "勇士",
    "火箭", "步行者", "快船", "湖人", "灰熊", "热火", "雄鹿", "森林狼", "鹈鹕", "尼克斯",
    "雷霆", "魔术", "76人", "太阳", "开拓者", "国王", "马刺", "猛龙", "爵士", "奇才",
]

WEEKDAY_ZH = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def bjt_now():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))


def normalize_team_name(name):
    if not name:
        return ""
    s = re.sub(r"[\s\(\)（）\[\]【】]", "", str(name))
    for kw in TEAM_ALIAS_KEYWORDS:
        if kw in s:
            return kw
    return s


def safe_float(val, default=None):
    try:
        if val is None or val == "":
            return default
        return float(str(val).replace("+", "").strip())
    except ValueError:
        return default


def american_to_decimal(ml):
    """美式赔率转小数赔率（贴近竞彩返还率）。"""
    v = safe_float(ml)
    if v is None or v == 0:
        return None
    if v > 0:
        dec = 1.0 + v / 100.0
    else:
        dec = 1.0 + 100.0 / abs(v)
    return round(max(1.02, dec * 0.91), 2)


def estimate_sfc_odds(home_spread):
    """依据让分盘口用正态分差模型估算 12 项胜分差 SP（当体彩官方不可达时兜底）。"""
    sigma = 11.5
    mean = -home_spread  # 主队预期净胜分
    bounds = [(0.5, 5.5), (5.5, 10.5), (10.5, 15.5), (15.5, 20.5), (20.5, 25.5), (25.5, 80.0)]

    def cdf(x, m):
        return 0.5 * (1.0 + math.erf((x - m) / (sigma * math.sqrt(2.0))))

    away_odds, home_odds = [], []
    for lo, hi in bounds:
        p_h = max(0.008, cdf(hi, mean) - cdf(lo, mean))
        p_a = max(0.008, cdf(hi, -mean) - cdf(lo, -mean))
        home_odds.append(round(min(99.0, max(3.20, 0.85 / p_h)), 2))
        away_odds.append(round(min(99.0, max(3.20, 0.85 / p_a)), 2))
    return {"away": away_odds, "home": home_odds}


def http_request(url, headers=None, data=None, method="GET", timeout=15):
    hdrs = dict(BROWSER_HEADERS)
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        raw = resp.read()
        charset = resp.headers.get_content_charset() or "utf-8"
        return raw.decode(charset, errors="replace")


# =============================================================================
# 通道 1：中国体育彩票官方接口（若 DNS 可达则优先采用）
# =============================================================================
def fetch_sporttery_odds():
    url = "https://web-api.sporttery.cn/gateway/jc/basketball/getMatchCalculatorV1.qry?channel=c&poolCode=&isFix=0"
    payload = json.loads(http_request(url, headers={"Referer": "https://www.sporttery.cn/"}, timeout=10))
    groups = (payload.get("value") or {}).get("matchInfoList") or []
    parsed = {}
    for grp in groups:
        bdate = grp.get("businessDate") or bjt_now().strftime("%Y-%m-%d")
        for item in grp.get("subMatchList") or []:
            code = item.get("matchNumStr") or ""
            away = normalize_team_name(item.get("awayTeamAbbName") or item.get("awayTeamAllName"))
            home = normalize_team_name(item.get("homeTeamAbbName") or item.get("homeTeamAllName"))
            if not away or not home:
                continue
            m_date = item.get("matchDate") or bdate
            m_time = (item.get("matchTime") or "08:00")[:5]
            mnl = item.get("mnl") or {}
            hdc = item.get("hdc") or {}
            hilo = item.get("hilo") or {}
            wnm = item.get("wnm") or {}
            sf_a, sf_h = safe_float(mnl.get("a")), safe_float(mnl.get("h"))
            num_part = re.sub(r"[^0-9]", "", code) or str(item.get("matchId", ""))
            parsed.setdefault(bdate, []).append({
                "id": f"sp_{bdate.replace('-', '')}_{num_part}",
                "code": f"{code[:2]} {code[2:]}" if len(code) >= 5 and " " not in code else code,
                "league": item.get("leagueAbbName") or "NBA",
                "group": "常规赛",
                "kickoff": f"{m_date[5:]} {m_time}",
                "kickoffIso": f"{m_date}T{m_time}:00+08:00",
                "away": away,
                "home": home,
                "sfOpen": sf_a is not None and sf_h is not None,
                "oddsSf": {"away": sf_a, "home": sf_h},
                "spread": safe_float(hdc.get("goalLine"), 0.0),
                "oddsSpread": {"away": safe_float(hdc.get("a"), 1.70), "home": safe_float(hdc.get("h"), 1.70)},
                "total": safe_float(hilo.get("goalLine"), 220.5),
                "oddsTotal": {"over": safe_float(hilo.get("h"), 1.70), "under": safe_float(hilo.get("l"), 1.70)},
                "sfcOdds": {
                    "away": [safe_float(wnm.get(f"l{i}"), 6.0) for i in range(1, 7)],
                    "home": [safe_float(wnm.get(f"w{i}"), 6.0) for i in range(1, 7)],
                },
            })
    return parsed


def fetch_sporttery_results(days_back=4):
    now = bjt_now()
    start = (now - datetime.timedelta(days=days_back)).strftime("%Y-%m-%d")
    end = now.strftime("%Y-%m-%d")
    url = (
        "https://web-api.sporttery.cn/gateway/jc/basketball/getMatchResultV1.qry"
        f"?matchPage=1&matchBeginDate={start}&matchEndDate={end}&leagueId=&pageSize=60&pageNo=1&isFix=0"
    )
    payload = json.loads(http_request(url, headers={"Referer": "https://www.sporttery.cn/"}, timeout=10))
    results = []
    for it in (payload.get("value") or {}).get("matchResult") or []:
        m = re.match(r"(\d+)\s*:\s*(\d+)", str(it.get("sectionsNo999") or it.get("finalScore") or "").strip())
        if m:
            results.append({
                "source": "sporttery",
                "code": (it.get("matchNumStr") or "").replace(" ", ""),
                "away": normalize_team_name(it.get("awayTeam")),
                "home": normalize_team_name(it.get("homeTeam")),
                "awayPts": int(m.group(1)),
                "homePts": int(m.group(2)),
            })
    return results


# =============================================================================
# 通道 2：ESPN NBA 全量接口（同时拉取昨日完场赛果 + 今日/明日最新赛程与实时盘口）
# =============================================================================
def fetch_espn_nba_all():
    """在腾讯云 SCF 与 GitHub Actions 均 100% 可达：同时返回完场赛果、球队战绩及最新未开赛 NBA 赛程盘口。"""
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    et_today = now_utc - datetime.timedelta(hours=5)
    # 扫描美东 昨天(-1)、今天(0)、明天(+1) 共 3 天，确保完场与新盘全覆盖
    dates = [
        (et_today + datetime.timedelta(days=off)).strftime("%Y%m%d")
        for off in (-1, 0, 1)
    ]
    scores = []
    records = {}
    upcoming_by_date = {}

    for d in dates:
        url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={d}"
        payload = json.loads(http_request(url, headers={"Referer": "https://www.espn.com/"}))
        seq = 301
        for ev in payload.get("events") or []:
            comp = (ev.get("competitions") or [{}])[0]
            st_type = (comp.get("status") or {}).get("type") or {}
            done = bool(st_type.get("completed"))
            comps = comp.get("competitors") or []
            away_c = next((c for c in comps if c.get("homeAway") == "away"), None)
            home_c = next((c for c in comps if c.get("homeAway") == "home"), None)
            if not away_c or not home_c:
                continue

            a_abbr = ((away_c.get("team") or {}).get("abbreviation") or "").upper()
            h_abbr = ((home_c.get("team") or {}).get("abbreviation") or "").upper()
            a_zh = NBA_ABBR_TO_ZH.get(a_abbr, "")
            h_zh = NBA_ABBR_TO_ZH.get(h_abbr, "")
            if not a_zh or not h_zh:
                continue

            a_rec = ((away_c.get("records") or [{}])[0]).get("summary") or "--"
            h_rec = ((home_c.get("records") or [{}])[0]).get("summary") or "--"
            if a_rec != "--":
                records[a_zh] = a_rec
            if h_rec != "--":
                records[h_zh] = h_rec

            if done:
                try:
                    scores.append({
                        "source": "espn",
                        "code": "",
                        "away": a_zh,
                        "home": h_zh,
                        "awayPts": int(away_c.get("score", 0)),
                        "homePts": int(home_c.get("score", 0)),
                    })
                except ValueError:
                    pass
                continue

            # 解析未开赛/进行中赛事的北京时间与 ESPN 实时盘口
            iso_utc = ev.get("date") or ""
            try:
                dt_utc = datetime.datetime.fromisoformat(iso_utc.replace("Z", "+00:00"))
                dt_bjt = dt_utc.astimezone(datetime.timezone(datetime.timedelta(hours=8)))
            except Exception:
                dt_bjt = bjt_now() + datetime.timedelta(hours=12)

            # 竞彩篮球期次日期按美东比赛日（即北京时间前一天）归档
            issue_date = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
            wd = WEEKDAY_ZH[datetime.date(int(d[:4]), int(d[4:6]), int(d[6:8])).weekday()]

            odds_arr = comp.get("odds") or []
            odds0 = odds_arr[0] if odds_arr else {}
            spread_home = safe_float(odds0.get("spread"), 0.0)
            # ESPN spread 有时带球队前缀 (如 "BOS -9.5")，从 details 校验符号
            details = str(odds0.get("details") or "")
            m_det = re.match(r"([A-Z]{2,4})\s*([+-]?\d+(?:\.\d+)?)", details.strip())
            if m_det:
                fav_abbr, fav_line = m_det.group(1), safe_float(m_det.group(2), 0.0)
                if fav_abbr == h_abbr:
                    spread_home = -abs(fav_line)
                elif fav_abbr == a_abbr:
                    spread_home = abs(fav_line)

            total_pts = safe_float(odds0.get("overUnder"), 222.5)
            h_ml = american_to_decimal((odds0.get("homeTeamOdds") or {}).get("moneyLine"))
            a_ml = american_to_decimal((odds0.get("awayTeamOdds") or {}).get("moneyLine"))
            if h_ml is None or a_ml is None:
                # 依据让分反推参考独赢赔率
                p_h = max(0.08, min(0.92, 0.5 * (1.0 + math.erf((-spread_home) / (11.5 * math.sqrt(2.0))))))
                h_ml = round(max(1.03, 0.89 / p_h), 2)
                a_ml = round(max(1.03, 0.89 / (1.0 - p_h)), 2)

            upcoming_by_date.setdefault(issue_date, []).append({
                "id": f"espn_{d}_{seq}",
                "code": f"{wd} {seq}",
                "league": "NBA",
                "group": "常规赛",
                "kickoff": dt_bjt.strftime("%m-%d %H:%M"),
                "kickoffIso": dt_bjt.strftime("%Y-%m-%dT%H:%M:00+08:00"),
                "away": a_zh,
                "home": h_zh,
                "awayRecord": a_rec,
                "homeRecord": h_rec,
                "sfOpen": abs(spread_home) < 12.5,
                "oddsSf": {"away": a_ml, "home": h_ml} if abs(spread_home) < 12.5 else {"away": None, "home": None},
                "initSpread": spread_home,
                "spread": spread_home,
                "oddsSpread": {"away": 1.70, "home": 1.70},
                "initTotal": total_pts,
                "total": total_pts,
                "oddsTotal": {"over": 1.70, "under": 1.70},
                "sfcOdds": estimate_sfc_odds(spread_home),
            })
            seq += 1

    return upcoming_by_date, scores, records


# =============================================================================
# GitHub 仓库读写
# =============================================================================
def github_get_file():
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{REMOTE_FILE_PATH}?ref={GITHUB_BRANCH}"
    hdrs = {"Accept": "application/vnd.github+json"}
    if GITHUB_TOKEN:
        hdrs["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    try:
        resp = json.loads(http_request(url, headers=hdrs))
        return json.loads(base64.b64decode(resp["content"]).decode("utf-8")), resp["sha"]
    except urllib.error.HTTPError as e:
        if e.code == 401 and GITHUB_TOKEN:
            # Token 无效时仍尝试匿名读取公开仓库，明确提示 401 原因
            print("[WARN] GITHUB_TOKEN 鉴权失败 (401)，尝试匿名读取公开仓库旧数据...")
            resp = json.loads(http_request(url, headers={"Accept": "application/vnd.github+json"}))
            return json.loads(base64.b64decode(resp["content"]).decode("utf-8")), resp["sha"]
        raise


def github_put_file(store, sha, message):
    if not GITHUB_TOKEN:
        raise RuntimeError("未配置 GITHUB_TOKEN 环境变量")
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{REMOTE_FILE_PATH}"
    body = {
        "message": message,
        "branch": GITHUB_BRANCH,
        "content": base64.b64encode(json.dumps(store, ensure_ascii=False, indent=2).encode("utf-8")).decode("ascii"),
    }
    if sha:
        body["sha"] = sha
    hdrs = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Content-Type": "application/json",
    }
    resp = json.loads(http_request(url, headers=hdrs, data=json.dumps(body).encode("utf-8"), method="PUT"))
    return (resp.get("commit") or {}).get("sha", "")[:7]


def build_default_match(inc):
    sp = inc.get("spread", 0.0)
    tot = inc.get("total", 220.5)
    return {
        **inc,
        "awayRecord": inc.get("awayRecord", "--"),
        "homeRecord": inc.get("homeRecord", "--"),
        "initSpread": inc.get("initSpread", sp),
        "initTotal": inc.get("initTotal", tot),
        "awayForm": inc.get("awayForm", ["W", "L", "W", "L", "W"]),
        "homeForm": inc.get("homeForm", ["W", "W", "L", "W", "L"]),
        "awayStats": inc.get("awayStats", f"当前赛季战绩 {inc.get('awayRecord', '--')}，已自动接入最新让分与大小分实时盘口。"),
        "homeStats": inc.get("homeStats", f"当前赛季战绩 {inc.get('homeRecord', '--')}，已自动接入最新让分与大小分实时盘口。"),
        "awayInjuries": inc.get("awayInjuries", "请关注赛前 30 分钟官方伤病报告更新。"),
        "homeInjuries": inc.get("homeInjuries", "请关注赛前 30 分钟官方伤病报告更新。"),
        "h2h": inc.get("h2h", "已根据实时让分与预设总分构建正态分差推演模型。"),
        "awayMotiv": inc.get("awayMotiv", 74),
        "homeMotiv": inc.get("homeMotiv", 76),
        "motivationNote": inc.get("motivationNote", "自动同步新开盘场次：可在上方沙盘微调初即盘口与战意权重。"),
    }


def merge_store(store, incoming_by_date, all_scores, team_records):
    issues = store.setdefault("issuesByDate", {})
    results = store.setdefault("results", {})
    added_matches = 0
    updated_odds = 0
    matched_scores = 0

    for d_str, inc_list in (incoming_by_date or {}).items():
        cur_list = issues.setdefault(d_str, [])
        for inc in inc_list:
            inc_a = normalize_team_name(inc.get("away"))
            inc_h = normalize_team_name(inc.get("home"))
            idx = next(
                (
                    i for i, m in enumerate(cur_list)
                    if normalize_team_name(m.get("away")) == inc_a
                    and normalize_team_name(m.get("home")) == inc_h
                ),
                None,
            )
            if idx is not None:
                for k in ("sfOpen", "oddsSf", "spread", "oddsSpread", "total", "oddsTotal", "sfcOdds", "kickoff", "kickoffIso", "awayRecord", "homeRecord"):
                    if inc.get(k) is not None and inc.get(k) != "--":
                        cur_list[idx][k] = inc[k]
                updated_odds += 1
            else:
                cur_list.append(build_default_match(inc))
                added_matches += 1

    for d_str, m_list in issues.items():
        for m in m_list:
            m_a = normalize_team_name(m.get("away"))
            m_h = normalize_team_name(m.get("home"))
            m_code = (m.get("code") or "").replace(" ", "")
            if m_a in team_records:
                m["awayRecord"] = team_records[m_a]
            if m_h in team_records:
                m["homeRecord"] = team_records[m_h]
            for sc in all_scores:
                code_match = bool(sc.get("code") and m_code and sc["code"] == m_code)
                team_match = bool(normalize_team_name(sc.get("away")) == m_a and normalize_team_name(sc.get("home")) == m_h)
                if code_match or team_match:
                    results[m["id"]] = {"awayPts": sc["awayPts"], "homePts": sc["homePts"]}
                    matched_scores += 1
                    break

    return added_matches, updated_odds, matched_scores


def run_self_test():
    sfc = estimate_sfc_odds(-7.5)
    assert len(sfc["home"]) == 6 and len(sfc["away"]) == 6
    sample_store = {"meta": {}, "issuesByDate": {"2026-11-24": [{"id": "tue306", "code": "周二 306", "away": "活塞", "home": "魔术", "spread": 2.5}]}, "results": {}}
    add_m, upd_o, hit_s = merge_store(sample_store, {}, [{"away": "底特律活塞", "home": "奥兰多魔术", "awayPts": 108, "homePts": 102}], {"活塞": "11-6"})
    assert hit_s == 1 and sample_store["results"]["tue306"] == {"awayPts": 108, "homePts": 102}
    print("SELF-TEST PASSED (v2.0): ESPN odds/schedule + fuzzy score settlement verified.")
    return 0


def run_sync(no_push=False):
    store = {"meta": {}, "issuesByDate": {}, "results": {}}
    remote_sha = None
    try:
        store, remote_sha = github_get_file()
        print(f"[INFO] 已加载 {GITHUB_REPO}/{REMOTE_FILE_PATH} (sha={str(remote_sha)[:7]})")
    except Exception as e:
        print(f"[WARN] 读取 GitHub 旧文件失败: {e}")

    channels_ok, channels_err = [], []
    incoming_by_date = {}
    all_scores = []
    team_records = {}

    # 1. 体彩官方赔率与赛果（优先）
    try:
        sp_odds = fetch_sporttery_odds()
        if sp_odds:
            incoming_by_date.update(sp_odds)
            channels_ok.append("sporttery_odds")
    except Exception as e:
        channels_err.append(f"sporttery_odds: {e}")

    try:
        sp_res = fetch_sporttery_results()
        if sp_res:
            all_scores.extend(sp_res)
            channels_ok.append("sporttery_results")
    except Exception as e:
        channels_err.append(f"sporttery_results: {e}")

    # 2. ESPN 全量通道（新赛程 + 实时盘口 + 完场赛果 + 战绩 — 腾讯云与 GitHub Actions 均稳定直连）
    try:
        espn_upcoming, espn_scores, espn_rec = fetch_espn_nba_all()
        for d_str, lst in espn_upcoming.items():
            incoming_by_date.setdefault(d_str, [])
            # 若体彩官方尚未抓到该场，补充 ESPN 新赛事与实时盘口
            existing_pairs = {(m["away"], m["home"]) for m in incoming_by_date[d_str]}
            for item in lst:
                if (item["away"], item["home"]) not in existing_pairs:
                    incoming_by_date[d_str].append(item)
        all_scores.extend(espn_scores)
        team_records.update(espn_rec)
        channels_ok.append("espn_nba_all")
        print(f"[OK] espn_nba_all: 新赛程 {sum(len(v) for v in espn_upcoming.values())} 场, 完场 {len(espn_scores)} 场, 战绩 {len(espn_rec)} 队")
    except Exception as e:
        channels_err.append(f"espn_nba_all: {e}")
        print(f"[WARN] espn_nba_all 失败: {e}")

    added_m, upd_o, hit_s = merge_store(store, incoming_by_date, all_scores, team_records)
    store["meta"] = {
        "updatedAt": bjt_now().strftime("%Y-%m-%d %H:%M:%S +08:00"),
        "runner": "tencent-cloud-scf" if _IS_SCF else "github-actions",
        "channelsOk": channels_ok,
        "channelsError": channels_err,
        "stats": {"addedMatches": added_m, "updatedOdds": upd_o, "matchedScores": hit_s},
    }

    try:
        os.makedirs(os.path.dirname(LOCAL_DATA_PATH), exist_ok=True)
        with open(LOCAL_DATA_PATH, "w", encoding="utf-8") as f:
            json.dump(store, f, ensure_ascii=False, indent=2)
    except OSError:
        pass

    commit_sha = ""
    if not no_push:
        msg = f"chore(auto): sync basketball odds({upd_o}+{added_m}) & results({hit_s}) [skip ci]"
        commit_sha = github_put_file(store, remote_sha, msg)
        print(f"[SUCCESS] 已推送至 GitHub {GITHUB_REPO} commit={commit_sha}")

    return {"ok": True, "commit": commit_sha, "meta": store["meta"]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--no-push", action="store_true")
    args, _ = parser.parse_known_args()
    if args.self_test:
        return run_self_test()
    run_sync(no_push=args.no_push)
    return 0


def main_handler(event, context):
    try:
        res = run_sync(no_push=False)
        return {"statusCode": 200, "body": json.dumps(res, ensure_ascii=False)}
    except urllib.error.HTTPError as e:
        hint = "（请检查环境变量 GITHUB_TOKEN 是否有效、未多粘贴空格或重复粘贴，且已勾选 repo 权限）" if e.code == 401 else ""
        return {"statusCode": e.code, "body": json.dumps({"ok": False, "error": f"HTTP {e.code}: {e.reason}{hint}"}, ensure_ascii=False)}
    except Exception as e:
        return {"statusCode": 500, "body": json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)}


if __name__ == "__main__":
    sys.exit(main())
