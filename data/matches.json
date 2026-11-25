#!/usr/bin/env python3
"""
GitHub Actions 专用 · 竞彩篮球多路自动同步脚本（免 GITHUB_TOKEN，直接读写仓库 data/matches.json）
"""

import argparse
import datetime
import json
import math
import os
import re
import ssl
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_PATH = os.path.join(REPO_ROOT, "data", "matches.json")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/129.0.0.0 Safari/537.36",
    "Accept": "application/json, text/html, */*",
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


def normalize_team(name):
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
    v = safe_float(ml)
    if v is None or v == 0:
        return None
    dec = (1.0 + v / 100.0) if v > 0 else (1.0 + 100.0 / abs(v))
    return round(max(1.02, dec * 0.91), 2)


def estimate_sfc_odds(home_spread):
    sigma = 11.5
    mean = -home_spread
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


def fetch_text(url, encoding="utf-8", timeout=10):
    req = urllib.request.Request(url, headers=HEADERS)
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        raw = resp.read()
    try:
        return raw.decode(encoding, errors="ignore")
    except Exception:
        return raw.decode("gbk", errors="ignore")


def strip_tags(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s)).strip()


def fetch_espn_nba_all():
    """ESPN NBA 全量通道：拉取美东昨/今/明三天全部新赛程、实时盘口、完场比分与球队战绩。"""
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    et_today = now_utc - datetime.timedelta(hours=5)
    dates = [(et_today + datetime.timedelta(days=off)).strftime("%Y%m%d") for off in (-1, 0, 1)]

    upcoming_by_date = {}
    scores = []
    records = {}

    for d in dates:
        url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={d}"
        data = json.loads(fetch_text(url, timeout=10))
        seq = 301
        for ev in data.get("events") or []:
            comp = (ev.get("competitions") or [{}])[0]
            done = bool(((comp.get("status") or {}).get("type") or {}).get("completed"))
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
                        "code": "",
                        "away": a_zh,
                        "home": h_zh,
                        "awayPts": int(away_c.get("score", 0)),
                        "homePts": int(home_c.get("score", 0)),
                    })
                except ValueError:
                    pass
                continue

            try:
                dt_utc = datetime.datetime.fromisoformat((ev.get("date") or "").replace("Z", "+00:00"))
                dt_bjt = dt_utc.astimezone(datetime.timezone(datetime.timedelta(hours=8)))
            except Exception:
                dt_bjt = bjt_now() + datetime.timedelta(hours=10)

            issue_date = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
            wd = WEEKDAY_ZH[datetime.date(int(d[:4]), int(d[4:6]), int(d[6:8])).weekday()]

            odds_arr = comp.get("odds") or []
            odds0 = odds_arr[0] if odds_arr else {}
            spread_home = safe_float(odds0.get("spread"), 0.0)
            m_det = re.match(r"([A-Z]{2,4})\s*([+-]?\d+(?:\.\d+)?)", str(odds0.get("details") or "").strip())
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


def fetch_163_results():
    """网易竞彩篮球完场比分补充（覆盖欧篮联等非 NBA 完场赛果）。"""
    results = []
    try:
        html = fetch_text("https://sports.163.com/caipiao/match/basketball/jclq", timeout=8)
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
            seg = strip_tags(tr)
            if "完" not in seg:
                continue
            code_m = re.search(r"(周[一二三四五六日]\d{3})", seg)
            after_fin = seg.split("完", 1)[1]
            sc_m = re.search(r"([^\s\d]{2,10})\s+(\d{2,3})\s*[-:]\s*(\d{2,3})\s+([^\s\d]{2,10})", after_fin)
            if not sc_m:
                continue
            ap, hp = int(sc_m.group(2)), int(sc_m.group(3))
            if not (50 <= ap <= 210 and 50 <= hp <= 210):
                continue
            results.append({
                "code": (code_m.group(1)[:2] + " " + code_m.group(1)[2:]) if code_m else "",
                "away": normalize_team(sc_m.group(1)),
                "home": normalize_team(sc_m.group(4)),
                "awayPts": ap,
                "homePts": hp,
            })
    except Exception:
        pass
    return results


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
        "awayStats": inc.get("awayStats", f"{inc.get('away', '')}（赛季 {inc.get('awayRecord', '--')}），已自动接入最新让分与大小分盘口。"),
        "homeStats": inc.get("homeStats", f"{inc.get('home', '')}（赛季 {inc.get('homeRecord', '--')}），已自动接入最新让分与大小分盘口。"),
        "awayInjuries": inc.get("awayInjuries", "请关注赛前 30 分钟官方伤病报告更新。"),
        "homeInjuries": inc.get("homeInjuries", "请关注赛前 30 分钟官方伤病报告更新。"),
        "h2h": inc.get("h2h", f"{inc.get('away', '')}(客) VS {inc.get('home', '')}(主)：主让 {sp:+.1f} / 预设总分 {tot}。"),
        "awayMotiv": inc.get("awayMotiv", 78 if sp > 0 else 72),
        "homeMotiv": inc.get("homeMotiv", 78 if sp < 0 else 72),
        "motivationNote": inc.get("motivationNote", f"GitHub Actions 自动同步：主让 {sp:+.1f}，预设总分 {tot}。"),
    }


def merge_store(store, incoming_by_date, all_scores, team_records):
    issues = store.setdefault("issuesByDate", {})
    results = store.setdefault("results", {})
    added_m, upd_o, hit_s = 0, 0, 0

    for d_str, inc_list in (incoming_by_date or {}).items():
        cur_list = issues.setdefault(d_str, [])
        for inc in inc_list:
            ia, ih = normalize_team(inc.get("away")), normalize_team(inc.get("home"))
            idx = next(
                (i for i, m in enumerate(cur_list)
                 if normalize_team(m.get("away")) == ia and normalize_team(m.get("home")) == ih),
                None,
            )
            if idx is not None:
                for k in ("sfOpen", "oddsSf", "spread", "oddsSpread", "total", "oddsTotal", "sfcOdds", "kickoff", "kickoffIso", "awayRecord", "homeRecord"):
                    if inc.get(k) is not None and inc.get(k) != "--":
                        cur_list[idx][k] = inc[k]
                upd_o += 1
            else:
                cur_list.append(build_default_match(inc))
                added_m += 1

    for d_str, m_list in issues.items():
        for m in m_list:
            ma, mh = normalize_team(m.get("away")), normalize_team(m.get("home"))
            m_code = (m.get("code") or "").replace(" ", "")
            if ma in team_records:
                m["awayRecord"] = team_records[ma]
            if mh in team_records:
                m["homeRecord"] = team_records[mh]
            for sc in all_scores:
                code_ok = bool(sc.get("code") and m_code and sc["code"].replace(" ", "") == m_code)
                team_ok = bool(normalize_team(sc.get("away")) == ma and normalize_team(sc.get("home")) == mh)
                if code_ok or team_ok:
                    results[m["id"]] = {"awayPts": sc["awayPts"], "homePts": sc["homePts"]}
                    hit_s += 1
                    break

    return added_m, upd_o, hit_s


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args, _ = parser.parse_known_args()

    if args.self_test:
        st = {"meta": {}, "issuesByDate": {"2026-11-24": [{"id": "tue306", "code": "周二 306", "away": "活塞", "home": "魔术", "spread": 2.5}]}, "results": {}}
        _, _, hits = merge_store(st, {}, [{"away": "底特律活塞", "home": "奥兰多魔术", "awayPts": 104, "homePts": 86}], {"活塞": "11-6"})
        assert hits == 1 and st["results"]["tue306"] == {"awayPts": 104, "homePts": 86}
        print("SELF-TEST PASSED: DATA_PATH =", DATA_PATH)
        return 0

    if os.path.exists(DATA_PATH):
        with open(DATA_PATH, "r", encoding="utf-8") as f:
            store = json.load(f)
    else:
        store = {"meta": {}, "issuesByDate": {}, "results": {}}

    channels_ok, channels_err = [], []
    incoming_by_date, all_scores, team_records = {}, [], {}

    with ThreadPoolExecutor(max_workers=2) as pool:
        f_espn = pool.submit(fetch_espn_nba_all)
        f_163 = pool.submit(fetch_163_results)

        try:
            up, sc, rec = f_espn.result(timeout=20)
            incoming_by_date.update(up)
            all_scores.extend(sc)
            team_records.update(rec)
            channels_ok.append("espn_nba_all")
            print(f"[OK] espn_nba_all: 新赛程 {sum(len(v) for v in up.values())} 场, 完场 {len(sc)} 场, 战绩 {len(rec)} 队")
        except Exception as e:
            channels_err.append(f"espn_nba_all: {e}")
            print(f"[WARN] espn_nba_all: {e}")

        try:
            r163 = f_163.result(timeout=12)
            if r163:
                all_scores.extend(r163)
                channels_ok.append("163_results")
                print(f"[OK] 163_results: 完场 {len(r163)} 场")
        except Exception as e:
            channels_err.append(f"163_results: {e}")

    added_m, upd_o, hit_s = merge_store(store, incoming_by_date, all_scores, team_records)
    store["meta"] = {
        "updatedAt": bjt_now().strftime("%Y-%m-%d %H:%M:%S +08:00"),
        "runner": "github-actions",
        "channelsOk": channels_ok,
        "channelsError": channels_err,
        "stats": {"addedMatches": added_m, "updatedOdds": upd_o, "matchedScores": hit_s},
    }

    os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)

    print(f"[SUCCESS] 已更新 {DATA_PATH} (新增{added_m}场 / 更新赔率{upd_o}场 / 匹配赛果{hit_s}场)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
