"""保存した OpenWork の MHTML から比較用の HTML を作る。

使い方:
    python3 tools/build_compare.py A_roland A_honda   # 指定した企業だけ
    python3 tools/build_compare.py                    # フォルダ内の全社
"""
import email
import json
import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent   # リポジトリのルート(企業フォルダもここに置く)
SRC = ROOT
OUT = ROOT / "index.html"
TEMPLATE = Path(__file__).resolve().parent / "compare_template.html"
LOCATIONS = SRC / "勤務地候補.md"   # デザイン職の勤務地候補(企業名で突き合わせる)
MEMOS = SRC / "メモ.json"   # 自分用のメモ({フォルダ名: メモ})
OFFICES = SRC / "勤務地_GoogleMaps.md"   # 想定出勤先・住所・Googleマップ

SCORE_KEYS = [
    ("satisfy", "待遇面の満足度"), ("spirit", "社員の士気"), ("airy", "風通しの良さ"),
    ("team", "社員の相互尊重"), ("junior", "20代成長環境"), ("senior", "人材の長期育成"),
    ("law", "法令順守意識"), ("assess", "人事評価の適正感"),
]


def load_html(path: Path) -> BeautifulSoup:
    msg = email.message_from_binary_file(path.open("rb"))
    for part in msg.walk():
        if part.get_content_type() == "text/html":
            return BeautifulSoup(part.get_payload(decode=True).decode("utf-8", "ignore"), "html.parser")
    raise ValueError(f"HTML がありません: {path}")


def snapshot_url(path: Path) -> str | None:
    head = path.open("rb").read(4000).decode("utf-8", "ignore")
    m = re.search(r"^Snapshot-Content-Location:\s*(\S+)", head, re.M)
    return m.group(1) if m else None


def num(text):
    if text is None:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", text.replace(",", ""))
    return float(m.group()) if m else None


def txt(el):
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip() if el else None


def find_file(folder: Path, mark: str):
    for f in folder.glob("*.mhtml"):
        if mark in f.name:
            return f
    return None


def parse_top(s: BeautifulSoup) -> dict:
    d = {}
    a = s.select_one("a.noLink[href*='company.php']")
    d["name"] = txt(a)
    d["total"] = num(txt(s.select_one(".totalEvaluation_item .fw-b")))
    d["top_percent"] = num(txt(s.select_one(".totalEvaluation_topPercentile")))
    resp = s.find(string=re.compile("回答者："))
    d["respondents"] = num(txt(resp.parent)) if resp else None
    main_chart = s.select_one("#mainColumn .averageScore .jq_horizontalChart")  # 職種別などのモーダル内の値は使わない
    for dl in (main_chart.select("dl") if main_chart else []):
        label, value = txt(dl.dt), num(txt(dl.dd))
        if label and "残業" in label:
            d["overtime"] = value
        elif label and "有給" in label:
            d["paid_leave"] = value
    scores = {}
    for key, label in SCORE_KEYS:
        li = s.select_one(f"li.scoreList_item-{key}")
        scores[label] = num(txt(li.dd)) if li and li.dd else None
    d["scores"] = scores
    summary = []
    for item in s.select("section.vcsSummary .vcsSummaryItem"):
        texts = item.select("p.vcsSummaryText")
        sources = []
        for li in item.select("li.vcsSummarySourceItem"):
            link = li.select_one("a")
            sources.append({
                "meta": txt(li.select_one(".vcsSummarySourceMetaInfo")),
                "date": txt(li.select_one(".vcsSummarySourceMetaDate")),
                "text": txt(link),
                "url": link.get("href") if link else None,
            })
        summary.append({
            "title": txt(texts[0]) if texts else None,
            "body": txt(texts[1]) if len(texts) > 1 else None,
            "sources": sources,
        })
    d["ai_summary"] = summary
    return d


def range_pair(text):
    vals = [float(v) for v in re.findall(r"\d+(?:\.\d+)?", (text or "").replace(",", ""))]
    return vals[:2] if len(vals) >= 2 else None


def parse_salary(s: BeautifulSoup) -> dict:
    d = {}
    th = s.find("th", string=re.compile("回答者の平均年収"))
    if th:
        table = th.find_parent("table")
        d["avg_salary"] = num(txt(th.find_next_sibling("td")))
        for tr in table.select("tr"):
            k, v = txt(tr.th), txt(tr.td)
            if k == "年収範囲":
                d["salary_range"] = range_pair(v)
            elif k == "平均年齢":
                d["avg_age"] = num(v)
            elif k == "回答者数":
                d["salary_respondents"] = num(v)
        note = table.find_next("p")
        m = re.search(r"業界の平均年収(\d+)万円", txt(note) or "")
        if m:
            d["industry_avg"] = float(m.group(1))
            m2 = re.search(r"です。\s*(.+?)業界の平均年収", txt(note))
            d["industry"] = m2.group(1).strip() if m2 else None

    age = []
    art = s.find(id="annualIncomeAgeSummary")
    if art:
        for tr in art.select("table tr")[1:]:
            tds = [txt(td) for td in tr.select("td")]
            if len(tds) >= 3:
                age.append({"age": num(tds[0]), "salary": num(tds[1]), "range": range_pair(tds[2])})
    d["age_salary"] = age

    occ = []
    art = s.find(id="annualIncomeOccupationSummary")
    if art:
        for tr in art.select("tr.incomeDataList"):
            title = tr.select_one(".incomeDataList_title")
            count = title.select_one("span") if title else None
            name = txt(title)
            if count:
                name = name.replace(txt(count), "").strip()
            occ.append({
                "job": name,
                "count": num(txt(count)),
                "avg": num(txt(tr.select_one(".incomeDataList_average"))),
                "range": range_pair(txt(tr.select_one(".incomeDataList_range"))),
            })
    d["occupation_salary"] = occ

    trend = {}
    art = s.find(id="annualIncomeJobTitleAge")
    if art:
        for det in art.select("details"):
            job = txt(det.select_one("summary"))
            rows = []
            for tr in det.select("tbody tr"):
                tds = [txt(td) for td in tr.select("td")]
                if len(tds) >= 3:
                    rows.append({"age": num(tds[0]), "salary": num(tds[1]), "range": range_pair(tds[2])})
            if job and rows:
                trend[job] = rows
    d["occupation_age_salary"] = trend
    return d


def build_company(folder: Path) -> dict:
    top = find_file(folder, "「社員クチコミ」 就職")
    sal = find_file(folder, "「年収・給与制度」")
    d = {"folder": folder.name, "rank": folder.name[0] if re.match(r"[A-Z]_", folder.name) else "-"}
    if top:
        d.update(parse_top(load_html(top)))
        d["url"] = snapshot_url(top)
    if sal:
        d.update(parse_salary(load_html(sal)))
        d["salary_url"] = snapshot_url(sal)
    if not d.get("name"):
        d["name"] = folder.name
    return d


def parse_locations(path: Path) -> dict:
    """勤務地候補.md の表を {企業名: {...}} にする。"""
    if not path.exists():
        return {}
    locs = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        cols = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cols) != 5 or cols[0] in ("企業名", "") or set(cols[0]) <= set("-: "):
            continue
        name, first, others, basis, note = cols
        link = re.search(r"\[出典\]\((\S+?)\)", note)
        locs[name] = {
            "first": first.replace("**", "").strip(),
            "others": None if others in ("―", "-", "") else others,
            "basis": basis,
            "note": re.sub(r"\s*\[出典\]\(\S+?\)", "", note).strip(),
            "source": link.group(1) if link else None,
        }
    return locs


def parse_offices(path: Path) -> dict:
    """勤務地_GoogleMaps.md の表(企業|想定出勤先|住所|確度|補足|Googleマップ)を {企業名: {...}} にする。"""
    if not path.exists():
        return {}
    offices = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        cols = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cols) != 6 or cols[0] in ("企業", "") or set(cols[0]) <= set("-: "):
            continue
        name, office, address, basis, note, maplink = cols
        m = re.search(r"\((https?://\S+?)\)\s*$", maplink)
        offices[name] = {
            "office": office.replace("**", "").strip(),
            "address": address,
            "basis": basis,
            "note": note,
            "map": m.group(1).replace(" ", "+") if m else None,
        }
    return offices


def short_place(address: str) -> str:
    """住所から「都道府県+市区町村」までを取り出す(基本情報用)。"""
    m = re.match(r"(.+?[都道府県])(.+?郡.+?[町村]|.+?市.+?区|.+?[市区町村])", address or "")
    return m.group(1) + m.group(2) if m else (address or "")


def write_webloc(folder: Path, url: str):
    out = folder.parent / f"{folder.name}.webloc"
    if out.exists() or not url:
        return
    out.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        f'<plist version="1.0">\n<dict>\n\t<key>URL</key>\n\t<string>{url.replace("&", "&amp;")}</string>\n</dict>\n</plist>\n',
        encoding="utf-8")
    print(f"  リンクを作成: {out.name}")


def main():
    names = sys.argv[1:] or sorted(p.name for p in SRC.iterdir()
                                   if p.is_dir() and any(p.glob("*.mhtml")))
    locs = parse_locations(LOCATIONS)
    offices = parse_offices(OFFICES)
    memos = json.loads(MEMOS.read_text(encoding="utf-8")) if MEMOS.exists() else {}
    data = []
    for n in names:
        folder = SRC / n
        if not folder.is_dir():
            print(f"[skip] フォルダがありません: {n}")
            continue
        c = build_company(folder)
        c["location"] = locs.get(c["name"])
        c["office"] = offices.get(c["name"])
        c["memo"] = memos.get(n, "")
        if c["office"]:
            c["office"]["place"] = short_place(c["office"]["address"])  # 地図リンクと同じ住所から作る
        write_webloc(folder, c.get("url"))
        data.append(c)
        missing = [k for k in ("total", "scores", "avg_salary", "age_salary", "occupation_salary") if not c.get(k)]
        print(f"{n}: {c['name']}  AI要約 {len(c.get('ai_summary', []))}件"
              + ("" if c["office"] else "  出勤先なし")
              + (f"  欠け: {missing}" if missing else ""))
    html = TEMPLATE.read_text(encoding="utf-8").replace(
        "/*__DATA__*/[]", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
    OUT.write_text(html, encoding="utf-8")
    print(f"\n出力: {OUT}  ({len(data)}社)")


if __name__ == "__main__":
    main()
