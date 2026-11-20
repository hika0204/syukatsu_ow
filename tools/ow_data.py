"""index.html の中のデータ(const DATA)を読み書きするツール。

index.html が正本。ページの見た目もデータも index.html に入っている。
MHTML は手元に置いてあるだけで、Git には入れない。

使い方(リポジトリのルートで実行):
    python3 tools/ow_data.py memo <フォルダ名> <メモ>   # メモを書く(空にすると消える)
    python3 tools/ow_data.py rank <フォルダ名> <A〜E>   # ランクを変える(手元のフォルダ名も変える)
    python3 tools/ow_data.py add <フォルダ名>...        # 手元の MHTML から企業を追加・数値を更新する
    python3 tools/ow_data.py import-md                  # 勤務地の md 2つから出勤先・候補を取り込む
    python3 tools/ow_data.py list                       # 登録されている企業の一覧
"""
import email
import json
import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent   # リポジトリのルート(企業フォルダもここに置く)
SRC = ROOT
PAGE = ROOT / "index.html"   # 正本
LOCATIONS = SRC / "勤務地候補.md"   # デザイン職の勤務地候補(企業名で突き合わせる)
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


DATA_RE = re.compile(r"(const DATA = )(\[.*?\])(;\n)", re.S)


def load_page():
    html = PAGE.read_text(encoding="utf-8")
    m = DATA_RE.search(html)
    if not m:
        sys.exit("index.html に const DATA が見つかりません")
    return html, json.loads(m.group(2))


def save_page(html, data):
    body = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    new = DATA_RE.sub(lambda m: m.group(1) + body + m.group(3), html, count=1)
    PAGE.write_text(new, encoding="utf-8")


def find(data, folder):
    for c in data:
        if c["folder"] == folder:
            return c
    sys.exit(f"登録されていません: {folder}  (python3 tools/ow_data.py list で確認)")


def cmd_list(html, data):
    for c in data:
        print(f"{c['folder']:26s} {c['name']}  {('メモ: ' + c['memo']) if c.get('memo') else ''}")


def cmd_memo(html, data, args):
    if not args:
        sys.exit("使い方: memo <フォルダ名> <メモ>")
    c = find(data, args[0])
    c["memo"] = " ".join(args[1:]).strip()
    save_page(html, data)
    print(f"{c['folder']}: メモを{'更新' if c['memo'] else '削除'}しました")


def cmd_rank(html, data, args):
    if len(args) != 2 or not re.fullmatch(r"[A-Z]", args[1]):
        sys.exit("使い方: rank <フォルダ名> <A〜Zの1文字>")
    c = find(data, args[0])
    old = c["folder"]
    base = old[2:] if re.match(r"[A-Z]_", old) else old
    new = f"{args[1]}_{base}"
    if any(x["folder"] == new for x in data):
        sys.exit(f"すでにあります: {new}")
    c["folder"], c["rank"] = new, args[1]
    save_page(html, data)
    for suffix in ("", ".webloc"):    # 手元の MHTML フォルダと .webloc も合わせる(あれば)
        src, dst = ROOT / (old + suffix), ROOT / (new + suffix)
        if src.exists() and not dst.exists():
            src.rename(dst)
    print(f"{old} -> {new}")


def cmd_add(html, data, args):
    if not args:
        sys.exit("使い方: add <フォルダ名>...")
    for n in args:
        folder = SRC / n
        if not folder.is_dir() or not any(folder.glob("*.mhtml")):
            print(f"[skip] 手元に MHTML のフォルダがありません: {n}")
            continue
        new = build_company(folder)
        old = next((c for c in data if c["folder"] == n), None)
        if old:   # 更新: 数値は入れ替え、メモ・出勤先・勤務地は残す
            for k in ("memo", "office", "location"):
                if old.get(k):
                    new[k] = old[k]
            data[data.index(old)] = new
        else:
            new.setdefault("memo", "")
            data.append(new)
        write_webloc(folder, new.get("url"))
        missing = [k for k in ("total", "scores", "avg_salary", "age_salary", "occupation_salary") if not new.get(k)]
        print(f"{'更新' if old else '追加'}: {n}  {new['name']}" + (f"  欠け: {missing}" if missing else ""))
    save_page(html, data)


def cmd_import_md(html, data):
    locs, offices = parse_locations(LOCATIONS), parse_offices(OFFICES)
    for c in data:
        loc, off = locs.get(c["name"]), offices.get(c["name"])
        if loc:
            c["location"] = loc
        if off:
            off["place"] = short_place(off["address"])
            c["office"] = off
        if not off:
            print(f"出勤先なし: {c['folder']}  {c['name']}")
    save_page(html, data)
    print(f"取り込みました({len(offices)}社分の出勤先、{len(locs)}社分の候補)")


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("list", "memo", "rank", "add", "import-md"):
        sys.exit(__doc__)
    html, data = load_page()
    cmd, args = sys.argv[1], sys.argv[2:]
    {"list": lambda: cmd_list(html, data), "memo": lambda: cmd_memo(html, data, args),
     "rank": lambda: cmd_rank(html, data, args), "add": lambda: cmd_add(html, data, args),
     "import-md": lambda: cmd_import_md(html, data)}[cmd]()


if __name__ == "__main__":
    main()
