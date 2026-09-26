// Build the 改善事项清单 Word document from data/reports/items.json (written by the export step).
// usage: node scripts/report_docx.js data/reports/items.json data/reports/改善事项清单.docx
const fs = require('fs');
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell, WidthType, AlignmentType, HeadingLevel,
  BorderStyle, ShadingType, PageOrientation, LevelFormat, PageBreak,
} = require('docx');

const [inPath, outPath] = process.argv.slice(2);
const D = JSON.parse(fs.readFileSync(inPath, 'utf8'));

const FONT = 'Microsoft YaHei';
const INK = '1a1a1a', MUTED = '6b6b6b', RED = 'b3261e', HEAD_BG = 'f1f1ee', ESC_BG = 'fdecea';
const CAT_NOTE = {
  '食品安全与卫生': '异物、变质、虫害、身体不适、清洁剂污染、员工个人卫生',
  '出品品质': '新鲜度与预制感、火候过老过柴、上桌温度、分量缩水、出品不稳定',
  '口味与配方': '偏咸偏甜偏油、调味争议、口味平淡没惊喜',
  '等位与出餐速度': '排队、叫号、上菜慢、出餐顺序',
  '服务执行': '漏单、承诺未兑现、撤盘不及时、态度、叫不到人',
  '环境与硬件': '拥挤、噪音、空调、座位、异味、设备故障',
  '价格与套餐价值': '人均偏高、性价比、套餐含金量',
  '菜单与产品结构': '菜品下架、选择少、缺清爽菜、儿童餐',
  '结账与优惠': '团购核销、多扣款、优惠规则',
};

const run = (text, o = {}) => new TextRun({ text: String(text ?? ''), font: FONT, size: o.size || 19, bold: o.bold, color: o.color || INK });
const p = (text, o = {}) => new Paragraph({ children: [run(text, o)], spacing: { after: o.after ?? 80, before: o.before ?? 0 }, alignment: o.align });
const h1 = t => new Paragraph({ heading: HeadingLevel.HEADING_1, children: [run(t, { size: 28, bold: true })], spacing: { before: 280, after: 120 } });
const h2 = t => new Paragraph({ heading: HeadingLevel.HEADING_2, children: [run(t, { size: 23, bold: true })], spacing: { before: 200, after: 80 } });
const note = t => p(t, { size: 17, color: MUTED, after: 60 });
const bullet = t => new Paragraph({ children: [run(t)], numbering: { reference: 'bullets', level: 0 }, spacing: { after: 40 } });

const border = { style: BorderStyle.SINGLE, size: 4, color: 'd9d9d4' };
const borders = { top: border, bottom: border, left: border, right: border };
function table(widths, header, rows, opts = {}) {
  const total = widths.reduce((a, b) => a + b, 0);
  const cell = (text, w, o = {}) => new TableCell({
    width: { size: w, type: WidthType.DXA }, borders,
    shading: o.bg ? { type: ShadingType.CLEAR, fill: o.bg, color: 'auto' } : undefined,
    margins: { top: 50, bottom: 50, left: 80, right: 80 },
    children: (Array.isArray(text) ? text : [text]).map(t => new Paragraph({ children: [run(t, { size: o.size || 17, bold: o.bold, color: o.color })], spacing: { after: 0 } })),
  });
  return new Table({
    width: { size: total, type: WidthType.DXA }, columnWidths: widths,
    rows: [
      new TableRow({ tableHeader: true, children: header.map((t, i) => cell(t, widths[i], { bg: HEAD_BG, bold: true })) }),
      ...rows.map(r => new TableRow({ children: r.cells.map((t, i) => cell(t, widths[i], { bg: r.bg, color: r.color && i === r.colorIdx ? r.color : undefined, bold: r.boldIdx === i })) })),
    ],
  });
}

const allItems = D.items, themes = D.themes;
const items = allItems.filter(i => !i.aware), aware = allItems.filter(i => i.aware);
const nHigh = items.filter(i => i.priority === '高').length, nEsc = items.filter(i => i.escalate).length;
const reviewTotal = new Set(allItems.flatMap(i => (i.evidence || []).map(e => e.review_id))).size;
const quote1 = it => { const e = (it.evidence || []).find(e => e.quote); return e ? `「${e.quote}」${e.star ?? ''}★` : ''; };
const byId = Object.fromEntries(allItems.map(i => [i.id, i]));

const children = [];
children.push(new Paragraph({ children: [run('pLeace 顾客评价改善事项清单', { size: 36, bold: true })], spacing: { after: 60 } }));
children.push(p(`${D.from} 至 ${D.to} · 13 家门店 · 由顾客评价里的负面意见（含好评中的抱怨）自动归纳，供营运与店总审阅`, { size: 19, color: MUTED, after: 200 }));

// 1 概览
children.push(h1('一、概览'));
children.push(p(`两周共归纳出 ${allItems.length} 条事项，来自 ${reviewTotal} 条顾客评价。其中 ${items.length} 条需要门店整改（高优先级 ${nHigh} 条，${nEsc} 条属于食品安全或卫生类，需区域经理知悉并立即处理）；另有 ${aware.length} 条评价里没有具体的菜、时段或环节，只需留意，见第五部分。`));
children.push(h2('按分类'));
children.push(table([2400, 5200, 1100, 1100, 1100], ['分类', '包含什么', '事项', '评价', '门店'],
  D.categories.map(c => ({ cells: [c.category, CAT_NOTE[c.category] || '', c.items, c.reviews, c.stores] }))));
children.push(p(''));
children.push(h2('按门店'));
children.push(table([3400, 1200, 1400, 1600], ['门店', '事项数', '高优先级', '需升级'],
  D.stores.map(s => ({ cells: [s.name, s.n, s.high, s.esc], color: s.esc ? RED : undefined, colorIdx: 3 }))));

// 2 跨门店
children.push(new Paragraph({ children: [new PageBreak()] }));
children.push(h1('二、跨门店普遍问题'));
children.push(note('单店看只是"我们店的问题"，放在一起才知道是公司层面的问题。以下每个主题至少覆盖 3 家门店，按覆盖门店数排序。'));
themes.forEach((t, i) => {
  children.push(h2(`${i + 1}. ${t.title}`));
  children.push(p(`${t.category} · ${t.store_count} 家门店 · ${t.review_total} 条评价`, { size: 17, color: MUTED, after: 40 }));
  children.push(p(`涉及门店：${t.stores.map(s => s.replace(/^(wow |P3 by )?pLeace2? ?(island )?(LOFT )?/, '')).join('、')}`, { size: 17, after: 40 }));
  children.push(p(`共同点：${t.what_is_common}`, { after: 40 }));
  children.push(new Paragraph({ children: [run('公司层面建议：', { bold: true }), run(t.company_action)], spacing: { after: 60 } }));
  const ex = t.item_ids.map(id => byId[id]).filter(Boolean).slice(0, 4);
  if (ex.length) children.push(table([2200, 5200, 4400], ['门店', '门店事项', '顾客原话'],
    ex.map(it => ({ cells: [it.store, it.title, quote1(it)] }))));
  children.push(p(''));
});

// 3 红线
children.push(new Paragraph({ children: [new PageBreak()] }));
children.push(h1('三、需立即处理：食品安全与卫生'));
children.push(note('这些事项不看数量，一条就要处理。负责人品控，区域经理知悉。'));
children.push(table([2000, 3600, 4000, 3600], ['门店', '问题', '建议动作', '顾客原话'],
  items.filter(i => i.escalate).map(it => ({ cells: [it.store, it.title, it.action, quote1(it)], bg: ESC_BG }))));

// 4 全部事项按分类
children.push(new Paragraph({ children: [new PageBreak()] }));
children.push(h1('四、需整改事项（按分类）'));
children.push(note('优先级：高 = 安全卫生或导致顾客明确不再来；中 = 反复出现的出品或服务问题；低 = 偶发或建议类。负责人为角色：店长 / 品控 / 区域经理。'));
for (const cat of D.order) {
  const list = items.filter(i => i.category === cat).sort((a, b) => (b.escalate - a.escalate) || ('高中低'.indexOf(a.priority) - '高中低'.indexOf(b.priority)) || (b.review_count - a.review_count));
  if (!list.length) continue;
  children.push(h2(`${cat}（${list.length} 条）`));
  children.push(table([1700, 700, 900, 3500, 3900, 2700], ['门店', '优先', '负责', '问题', '建议动作', '顾客原话'],
    list.map(it => ({ cells: [it.store.replace(/^(wow |P3 by )?pLeace2? ?/, ''), it.priority, it.owner, it.title, it.action, quote1(it)],
      color: it.priority === '高' ? RED : undefined, colorIdx: 1, bg: it.escalate ? ESC_BG : undefined }))));
  children.push(p(''));
}

// 5 仅知晓
if (aware.length) {
  children.push(h1('五、仅知晓（留意即可）'));
  children.push(note('这些意见只有笼统感受，评价里找不到具体的菜、时段或环节，门店无从下手，所以不派任务、不设截止日期。店长知晓并留意；同类意见再出现且带出具体信息时，会变成整改事项。'));
  children.push(table([2000, 3400, 4200, 3600], ['门店', '顾客在说什么', '为什么只是知晓', '顾客原话'],
    aware.map(it => ({ cells: [it.store.replace(/^(wow |P3 by )?pLeace2? ?/, ''), it.title, it.kind_reason || '', quote1(it)] }))));
  children.push(p(''));
}

// 6 说明
children.push(h1('六、口径说明'));
[
  '数据来源：大众点评与美团上 13 家门店的顾客评价，经 AI 逐条拆解出每一个被提到的点及其正负面。',
  '改善事项：把一家门店一段时间内的所有负面意见（包括好评里夹带的抱怨）合并同类后归纳而成，每条附顾客原话作为证据。',
  '可落地审核：每条事项都回到评价原文核对，笼统的按原文里的具体菜品、时段、环节改写；原文也没有具体信息的列为仅知晓。',
  '评价数：事项背后有几条顾客评价，一条评价里说了几句也只算一次。',
  '跨门店主题：把 13 家门店的事项放在一起，找出至少 3 家门店共同出现的问题。',
  '分类为固定 9 类，一条事项只归一类，按其最主要的问题决定。',
  '本文档由系统自动生成，建议每周更新一次；处理状态可在看板上勾选。',
].forEach(t => children.push(bullet(t)));

const doc = new Document({
  styles: { default: { document: { run: { font: FONT, size: 19, color: INK } } } },
  numbering: { config: [{ reference: 'bullets', levels: [{ level: 0, format: LevelFormat.BULLET, text: '•', alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 360, hanging: 240 } } } }] }] },
  sections: [{
    properties: { page: { size: { width: 11906, height: 16838 }, margin: { top: 1000, bottom: 1000, left: 1000, right: 1000 } } },
    children,
  }],
});
Packer.toBuffer(doc).then(buf => { fs.writeFileSync(outPath, buf); console.log('written', outPath, buf.length); });
