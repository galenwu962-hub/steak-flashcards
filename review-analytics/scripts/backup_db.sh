#!/usr/bin/env bash
# Snapshot data/reviews.db for the backup page (https://claude.ai/artifact/EUAPHi53zTujY8RUwRKB6B).
#   bash scripts/backup_db.sh <out_dir>     -> <out_dir>/reviews-backup.db.xz.b64.txt (+ backup.html)
# Then publish with the Artifact tool: file_path <out_dir>/backup.html, url as above,
# files {"reviews-backup.db.xz.b64.txt": "<out_dir>/reviews-backup.db.xz.b64.txt"}.
# Restore: base64 -d reviews-backup.db.xz.b64.txt | xz -d > data/reviews.db
set -euo pipefail
cd "$(dirname "$0")/.."
out="${1:?out dir}"; mkdir -p "$out"
python3 - "$out/reviews.db" <<'PY'
import sqlite3, sys
src = sqlite3.connect("data/reviews.db"); dst = sqlite3.connect(sys.argv[1]); src.backup(dst); dst.execute("vacuum"); dst.close()
PY
xz -9 -c "$out/reviews.db" | base64 -w0 > "$out/reviews-backup.db.xz.b64.txt"
read -r n d1 a j < <(python3 -c "
import sqlite3; c=sqlite3.connect('$out/reviews.db')
print(c.execute('select count(*) from reviews').fetchone()[0], c.execute('select max(review_date) from reviews').fetchone()[0],
      c.execute('select count(*) from review_analysis').fetchone()[0], c.execute('select count(*) from action_items').fetchone()[0])")
rm "$out/reviews.db"
cat > "$out/backup.html" <<HTML
<title>评价数据备份</title>
<style>:root{--bg:#f9f9f7;--ink:#1a1a1a;--muted:#6b6b6b}@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){color-scheme:dark;--bg:#111;--ink:#eee;--muted:#999}}:root[data-theme="dark"]{color-scheme:dark;--bg:#111;--ink:#eee;--muted:#999}body{background:var(--bg);color:var(--ink);font:15px/1.6 system-ui,sans-serif;padding-block:32px;padding-inline:16px;max-width:640px;margin:0 auto}p{color:var(--muted)}code{font-size:13px}</style>
<h1>评价数据备份</h1>
<p>pLeace 评价洞察工具的数据库快照，供新的工作会话恢复使用。文件由 Claude 会话读取，不需要手动下载。</p>
<ul>
<li><code>reviews-backup.db.xz.b64.txt</code>：SQLite 数据库（xz 压缩后 base64），$(TZ=Asia/Shanghai date '+%Y-%m-%d %H:%M') 快照：$n 条评价（数据到 $d1）、$a 条 AI 解析、$j 条改善事项</li>
</ul>
<p>恢复方法见仓库 review-analytics/HANDOFF.md 第 1 节。</p>
HTML
ls -la "$out"
