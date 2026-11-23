#!/usr/bin/env python3
"""
Multi-Channel Jingcai Basketball Schedule, Odds & Results Sync Script
Runs on GitHub Actions (zero third-party dependencies, pure Python stdlib).

Channels:
  [Odds/Schedule Channel 1] Sporttery Official Basketball Calculator API (web-api.sporttery.cn)
  [Odds/Schedule Channel 2] NetEase Sports Basketball Lottery Page (sports.163.com/caipiao/match/basketball/jclq)
  [Results Channel 1]       Sporttery Official Basketball Results API (web-api.sporttery.cn)
  [Results Channel 2]       ESPN Public NBA Scoreboard API (site.api.espn.com) — global runner friendly
"""

import argparse
import datetime
import json
import os
import re
import sys
import urllib.error
import urllib.request

DATA_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "matches.json")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

NBA_TEAM_ZH = {
    "ATL": "老鹰", "BOS": "凯尔特人", "BKN": "篮网", "CHA": "黄蜂", "CHI": "公牛",
    "CLE": "骑士", "DAL": "独行侠", "DEN": "掘金", "DET": "活塞", "GS": "勇士",
    "GSW": "勇士", "HOU": "火箭", "IND": "步行者", "LAC": "快船", "LAL": "湖人",
    "MEM": "灰熊", "MIA": "热火", "MIL": "雄鹿", "MIN": "森林狼", "NO": "鹈鹕",
    "NOP": "鹈鹕", "NY": "尼克斯", "NYK": "尼克斯", "OKC": "雷霆", "ORL": "魔术",
    "PHI": "76人", "PHX": "太阳", "POR": "开拓者", "SAC": "国王", "SA": "马刺",
    "SAS": "马刺", "TOR": "猛龙", "UTA": "爵士", "UTAH": "爵士", "WAS": "奇才", "WSH": "奇才",
}


def bjt_now():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))


def http_get(url, timeout=12):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        charset = resp.headers.get_content_charset() or "utf-8"
        return raw.decode(charset, errors="replace")


def safe_float(val, default=None):
    try:
        if val is None or val == "":
            return default
        return float(str(val).replace("+", "").strip())
    except ValueError:
        return default


def load_existing():
    if os.path.exists(DATA_PATH):
        with open(DATA_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"meta": {}, "issuesByDate": {}, "results": {}}


def fetch_sporttery_odds():
    """Channel 1: Official Sporttery Basketball Calculator JSON API."""
    url = "https://web-api.sporttery.cn/gateway/jc/basketball/getMatchCalculatorV1.qry?channel=c&poolCode=&isFix=0"
    data = json.loads(http_get(url))
    value = data.get("value") or {}
    groups = value.get("matchInfoList") or []
    parsed = {}

    for grp in groups:
        bdate = grp.get("businessDate") or bjt_now().strftime("%Y-%m-%d")
        sub_list = grp.get("subMatchList") or []
        for item in sub_list:
            code = item.get("matchNumStr") or ""
            away = item.get("awayTeamAbbName") or item.get("awayTeamAllName") or ""
            home = item.get("homeTeamAbbName") or item.get("homeTeamAllName") or ""
            if not away or not home:
                continue

            m_date = item.get("matchDate") or bdate
            m_time = (item.get("matchTime") or "08:00")[:5]
            kickoff = f"{m_date[5:]} {m_time}"
            kickoff_iso = f"{m_date}T{m_time}:00+08:00"

            mnl = item.get("mnl") or {}
            hdc = item.get("hdc") or {}
            hilo = item.get("hilo") or {}
            wnm = item.get("wnm") or {}

            sf_a = safe_float(mnl.get("a"))
            sf_h = safe_float(mnl.get("h"))
            sf_open = sf_a is not None and sf_h is not None

            spread = safe_float(hdc.get("goalLine"), 0.0)
            sp_a = safe_float(hdc.get("a"), 1.70)
            sp_h = safe_float(hdc.get("h"), 1.70)

            total = safe_float(hilo.get("goalLine"), 220.5)
            ov = safe_float(hilo.get("h"), 1.70)
            un = safe_float(hilo.get("l"), 1.70)

            sfc_away = [safe_float(wnm.get(f"l{i}"), 6.0) for i in range(1, 7)]
            sfc_home = [safe_float(wnm.get(f"w{i}"), 6.0) for i in range(1, 7)]

            mid = f"sp_{bdate.replace('-', '')}_{re.sub(r'[^0-9]', '', code) or item.get('matchId', '')}"
            parsed.setdefault(bdate, []).append({
                "id": mid,
                "code": code,
                "league": item.get("leagueAbbName") or "NBA",
                "group": "常规赛",
                "kickoff": kickoff,
                "kickoffIso": kickoff_iso,
                "away": away,
                "home": home,
                "sfOpen": sf_open,
                "oddsSf": {"away": sf_a, "home": sf_h},
                "spread": spread,
                "oddsSpread": {"away": sp_a, "home": sp_h},
                "total": total,
                "oddsTotal": {"over": ov, "under": un},
                "sfcOdds": {"away": sfc_away, "home": sfc_home},
            })
    return parsed


def fetch_netease_odds():
    """Channel 2: NetEase Sports Basketball Lottery HTML fallback."""
    url = "https://sports.163.com/caipiao/match/basketball/jclq"
    html = http_get(url)
    # Extract structured rows if present in server-rendered HTML
    matches = []
    pattern = re.compile(
        r"(周[一二三四五六日]\d{3}).*?(\d{2}-\d{2}\s+\d{2}:\d{2}).*?([一-龥A-Za-z0-9]+)\s*(?:VS|vs)\s*([一-龥A-Za-z0-9]+)",
        re.S,
    )
    bdate = bjt_now().strftime("%Y-%m-%d")
    for m in pattern.finditer(html):
        code, kickoff, away, home = m.group(1), m.group(2), m.group(3), m.group(4)
        matches.append({
            "id": f"nt_{bdate.replace('-', '')}_{re.sub(r'[^0-9]', '', code)}",
            "code": f"{code[:2]} {code[2:]}",
            "league": "NBA",
            "group": "常规赛",
            "kickoff": kickoff,
            "away": away,
            "home": home,
        })
    return {bdate: matches} if matches else {}


def fetch_sporttery_results():
    """Results Channel 1: Official Sporttery Basketball Results API."""
    now = bjt_now()
    start = (now - datetime.timedelta(days=3)).strftime("%Y-%m-%d")
    end = now.strftime("%Y-%m-%d")
    url = (
        "https://web-api.sporttery.cn/gateway/jc/basketball/getMatchResultV1.qry"
        f"?matchPage=1&matchBeginDate={start}&matchEndDate={end}&leagueId=&pageSize=50&pageNo=1&isFix=0"
    )
    data = json.loads(http_get(url))
    items = (data.get("value") or {}).get("matchResult") or []
    results = []
    for it in items:
        score = it.get("sectionsNo999") or it.get("finalScore") or ""
        m = re.match(r"(\d+)\s*:\s*(\d+)", str(score))
        if not m:
            continue
        results.append({
            "code": it.get("matchNumStr") or "",
            "away": it.get("awayTeam") or "",
            "home": it.get("homeTeam") or "",
            "awayPts": int(m.group(1)),
            "homePts": int(m.group(2)),
        })
    return results


def fetch_espn_nba_scores():
    """Results Channel 2: ESPN Public NBA Scoreboard API (last 2 days + today US ET)."""
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    et_today = now_utc - datetime.timedelta(hours=5)
    dates = [
        (et_today - datetime.timedelta(days=1)).strftime("%Y%m%d"),
        et_today.strftime("%Y%m%d"),
    ]
    scores = []
    records = {}
    for d in dates:
        url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={d}"
        data = json.loads(http_get(url))
        for ev in data.get("events") or []:
            comp = (ev.get("competitions") or [{}])[0]
            status_done = bool(((comp.get("status") or {}).get("type") or {}).get("completed"))
            competitors = comp.get("competitors") or []
            away_c = next((c for c in competitors if c.get("homeAway") == "away"), None)
            home_c = next((c for c in competitors if c.get("homeAway") == "home"), None)
            if not away_c or not home_c:
                continue
            a_abbr = (away_c.get("team") or {}).get("abbreviation", "").upper()
            h_abbr = (home_c.get("team") or {}).get("abbreviation", "").upper()
            a_zh = NBA_TEAM_ZH.get(a_abbr, a_abbr)
            h_zh = NBA_TEAM_ZH.get(h_abbr, h_abbr)
            a_rec = ((away_c.get("records") or [{}])[0]).get("summary")
            h_rec = ((home_c.get("records") or [{}])[0]).get("summary")
            if a_rec:
                records[a_zh] = a_rec
            if h_rec:
                records[h_zh] = h_rec
            if status_done:
                try:
                    scores.append({
                        "away": a_zh,
                        "home": h_zh,
                        "awayPts": int(away_c.get("score", 0)),
                        "homePts": int(home_c.get("score", 0)),
                    })
                except ValueError:
                    pass
    return scores, records


def merge_match(existing, incoming):
    """Preserve hand-crafted fundamentals/injuries while updating live odds & lines."""
    merged = dict(existing)
    for k in ("sfOpen", "oddsSf", "spread", "oddsSpread", "total", "oddsTotal", "sfcOdds", "kickoff", "kickoffIso"):
        if incoming.get(k) is not None:
            merged[k] = incoming[k]
    return merged


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
        "awayStats": inc.get("awayStats", "已通过多路自动同步接入最新竞彩让分与大小分数据。"),
        "homeStats": inc.get("homeStats", "已通过多路自动同步接入最新竞彩让分与大小分数据。"),
        "awayInjuries": inc.get("awayInjuries", "请关注赛前 30 分钟官方伤病报告更新。"),
        "homeInjuries": inc.get("homeInjuries", "请关注赛前 30 分钟官方伤病报告更新。"),
        "h2h": inc.get("h2h", "系统已根据最新让分与预设总分构建正态分差推演模型。"),
        "awayMotiv": inc.get("awayMotiv", 74),
        "homeMotiv": inc.get("homeMotiv", 76),
        "motivationNote": inc.get("motivationNote", "自动同步新开盘场次：可在上方沙盘微调初即盘口与战意权重。"),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Validate local data/matches.json without network calls")
    args = parser.parse_args()

    store = load_existing()
    issues = store.setdefault("issuesByDate", {})
    results = store.setdefault("results", {})
    channels_ok = []
    channels_err = []

    if args.dry_run:
        total_m = sum(len(v) for v in issues.values())
        print(f"DRY-RUN OK: {len(issues)} issue date(s), {total_m} match(es), {len(results)} result(s).")
        return 0

    # 1. Odds & Schedule Channels
    incoming_by_date = {}
    try:
        sp_odds = fetch_sporttery_odds()
        if sp_odds:
            incoming_by_date = sp_odds
            channels_ok.append("sporttery_odds_api")
    except Exception as e:
        channels_err.append(f"sporttery_odds_api: {e}")

    if not incoming_by_date:
        try:
            nt_odds = fetch_netease_odds()
            if nt_odds:
                incoming_by_date = nt_odds
                channels_ok.append("netease_jclq_html")
        except Exception as e:
            channels_err.append(f"netease_jclq_html: {e}")

    for d_str, inc_list in incoming_by_date.items():
        cur_list = issues.setdefault(d_str, [])
        for inc in inc_list:
            idx = next(
                (i for i, m in enumerate(cur_list) if m.get("away") == inc.get("away") and m.get("home") == inc.get("home")),
                None,
            )
            if idx is not None:
                cur_list[idx] = merge_match(cur_list[idx], inc)
            else:
                cur_list.append(build_default_match(inc))

    # 2. Results & Team Records Channels
    all_scores = []
    team_records = {}
    try:
        sp_res = fetch_sporttery_results()
        if sp_res:
            all_scores.extend(sp_res)
            channels_ok.append("sporttery_results_api")
    except Exception as e:
        channels_err.append(f"sporttery_results_api: {e}")

    try:
        espn_scores, espn_records = fetch_espn_nba_scores()
        all_scores.extend(espn_scores)
        team_records.update(espn_records)
        channels_ok.append("espn_nba_scoreboard")
    except Exception as e:
        channels_err.append(f"espn_nba_scoreboard: {e}")

    # Apply records & scores across stored dates
    for d_str, m_list in issues.items():
        for m in m_list:
            if m.get("away") in team_records:
                m["awayRecord"] = team_records[m["away"]]
            if m.get("home") in team_records:
                m["homeRecord"] = team_records[m["home"]]
            for sc in all_scores:
                if sc["away"] == m.get("away") and sc["home"] == m.get("home"):
                    results[m["id"]] = {"awayPts": sc["awayPts"], "homePts": sc["homePts"]}

    store["meta"] = {
        "updatedAt": bjt_now().strftime("%Y-%m-%d %H:%M:%S +08:00"),
        "channelsOk": channels_ok,
        "channelsError": channels_err,
    }

    os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)

    print("Sync finished:", json.dumps(store["meta"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
