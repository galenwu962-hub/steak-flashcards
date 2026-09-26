"""Export improvement items and cross-store themes to JSON for scripts/report_docx.js.

usage: python -m app.export_items [--from 2026-09-12 --to 2026-09-25] [--out data/reports/items.json]
Defaults to the latest period in action_items.
"""
import argparse
import json
from pathlib import Path

from .db import connect

ORDER = ['食品安全与卫生', '出品品质', '口味与配方', '等位与出餐速度', '服务执行',
         '环境与硬件', '价格与套餐价值', '菜单与产品结构', '结账与优惠']


def export(conn, date_from, date_to):
    rows = conn.execute(
        """SELECT a.*, s.name AS store FROM action_items a JOIN stores s ON s.id = a.store_id
           WHERE a.period_from = ? AND a.period_to = ? ORDER BY s.name, a.id""",
        (date_from, date_to)).fetchall()
    items = []
    for r in rows:
        d = dict(r)
        d['evidence'] = json.loads(d.pop('evidence_json') or '[]')
        d['escalate'] = int(d['escalate'] or 0)
        items.append(d)

    themes = []
    for r in conn.execute(
            """SELECT * FROM themes WHERE period_from = ? AND period_to = ?
               ORDER BY store_count DESC, evidence_total DESC""", (date_from, date_to)):
        d = dict(r)
        d['stores'] = json.loads(d.pop('stores_json') or '[]')
        d['item_ids'] = json.loads(d.pop('item_ids_json') or '[]')
        d['review_total'] = len({e['review_id'] for i in items if i['id'] in d['item_ids'] for e in i['evidence']})
        themes.append(d)

    for i in items:
        i['review_count'] = i.get('review_count') or len({e['review_id'] for e in i['evidence']})
        i['aware'] = i.get('kind') == '知晓'
    categories = []
    for cat in ORDER:
        sub = [i for i in items if i['category'] == cat and not i['aware']]
        if sub:
            categories.append({'category': cat, 'items': len(sub),
                               'reviews': len({e['review_id'] for i in sub for e in i['evidence']}),
                               'stores': len({i['store'] for i in sub})})

    stores = {}
    for i in items:
        s = stores.setdefault(i['store'], {'name': i['store'], 'n': 0, 'high': 0, 'esc': 0, 'aware': 0})
        if i['aware']:
            s['aware'] += 1
            continue
        s['n'] += 1
        s['high'] += i['priority'] == '高'
        s['esc'] += i['escalate']

    return {'from': date_from, 'to': date_to, 'order': ORDER, 'items': items, 'themes': themes,
            'categories': categories,
            'stores': sorted(stores.values(), key=lambda s: (-s['esc'], -s['high'], -s['n']))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--from', dest='date_from')
    ap.add_argument('--to', dest='date_to')
    ap.add_argument('--out', default='data/reports/items.json')
    args = ap.parse_args()
    conn = connect()
    if not args.date_from:
        args.date_from, args.date_to = conn.execute(
            'SELECT period_from, period_to FROM action_items ORDER BY period_to DESC, period_from DESC LIMIT 1').fetchone()
    data = export(conn, args.date_from, args.date_to)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f"{len(data['items'])} items, {len(data['themes'])} themes -> {out}")


if __name__ == '__main__':
    main()
