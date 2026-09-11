#!/usr/bin/env python3
"""Local PDF -> Obsidian literature processing pipeline."""
from __future__ import annotations
import argparse, re, subprocess, sys
from pathlib import Path
try:
 from pypdf import PdfReader
except ImportError: PdfReader=None
ROOT=Path(__file__).resolve().parents[1]
PAPERS=ROOT/'10_论文'; ATT=ROOT/'90_附件'/'论文'; KNOW=ROOT/'20_知识'
def slug(s): return re.sub(r'[^A-Za-z0-9\u4e00-\u9fff -]','',s).strip()[:100]
def process(pdf):
 if PdfReader is None: raise RuntimeError('缺少 pypdf，请先安装 requirements.txt')
 reader=PdfReader(str(pdf)); pages=[(p.extract_text() or '') for p in reader.pages]; text='\n'.join(pages)
 title='';
 for line in text.splitlines():
  line=line.strip()
  if 20<=len(line)<=180 and not line.startswith(('Abstract','ABSTRACT')):
   title=line; break
 if not title: title=pdf.stem
 doi=(re.search(r'10\.\d{4,9}/[-._;()/:A-Za-z0-9]+',text) or [None])[0] or '缺失'
 year=(re.search(r'\b(20\d{2})\b',text) or [None])[0] or '待核实'
 safe=slug(title) or pdf.stem
 note=PAPERS/f'{safe}.md'; wf=PAPERS/f'{safe} - ML Workflow.md'
 evidence=f'p. 1, PDF text extraction: "{re.sub(r"\\s+"," ",pages[0][:240]).strip()}"' if pages else '待核实'
 if not note.exists():
  note.write_text(f'''---\ntype: paper\ntitle: "{title.replace(chr(34), chr(39))}"\nyear: {year}\ndoi: "{doi}"\npdf: "[[90_附件/论文/{pdf.name}]]"\nstatus: extracted\nreview_status: needs-review\nevidence_status: partial\ncreated: "2026-09-11"\n---\n\n# {title}\n\n## 一句话结论\n\n> 待人工核实；以下内容由 PDF 初步提取。\n\n## 研究问题与背景\n\n- **研究问题**：待提取\n- **主要贡献**：待提取\n\n## 主要发现\n\n| 结论 | 状态 | 证据锚点 |\n| --- | --- | --- |\n| 待提取 | 待核实 | {evidence} |\n\n## 机器学习/AI 流程\n\n详见 [[{wf.stem}]]。\n\n## 证据与待核实\n\n- PDF 页数：{len(pages)}\n- DOI：{doi}\n- 重要证据：{evidence}\n\n### 待核实事项\n\n- 需要人工复核标题、作者、数据、模型和结论。\n''',encoding='utf-8')
 if not wf.exists():
  wf.write_text(f'''---\ntype: workflow\npaper: "[[{note.stem}]]"\nstatus: extracted\nreview_status: needs-review\nevidence_status: partial\nml_tasks: []\nmodels: []\nreproducibility: partial\ncreated: "2026-09-11"\n---\n\n# {title} - ML Workflow\n\n## 研究问题与目标\n\n- **任务类型**：待提取\n- **预测/优化目标**：待提取\n- **输入与输出**：待提取\n\n## 数据\n\n- **来源与样本量**：待提取\n- **标签与清洗**：待核实\n\n## 特征与预处理\n\n- **特征/表示**：待提取\n- **潜在数据泄漏**：待核实\n\n## 训练与验证\n\n- **划分、基线、模型和调参**：待提取\n\n## 评价与解释\n\n- **指标、误差和外部验证**：待提取\n\n## 批判性审查\n\n- **可迁移性**：结合 [[00_系统/研究画像]] 评估。\n- **证据状态**：明确 / 推断 / 缺失 / 待核实\n\n## 待核实事项\n\n- 需要人工复核 PDF 全文和 Supporting Information。\n''',encoding='utf-8')
 return note,wf

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('pdfs',nargs='+'); args=ap.parse_args(); out=[]
 for raw in args.pdfs:
  pdf=Path(raw).expanduser();
  if not pdf.is_absolute(): pdf=(Path.cwd()/pdf).resolve()
  if not pdf.exists() or pdf.suffix.lower()!='.pdf': raise SystemExit(f'无效 PDF: {pdf}')
  try: out.append(process(pdf))
  except Exception as e: print(f'处理失败 {pdf.name}: {e}',file=sys.stderr)
 try:
  r=subprocess.run([sys.executable,str(ROOT/'scripts/research_search.py'),'index'],capture_output=True,text=True)
  idx=(r.stdout+r.stderr).strip(); status='成功' if r.returncode==0 else '未更新'
 except Exception as e: idx=str(e); status='未更新'
 print('处理完成：'+str(len(out))+' 篇\n\n生成或更新：')
 for n,w in out: print(f'- Paper Note：{n}\n- ML Workflow：{w}')
 print(f'\n索引：{status}\n{idx or "无输出"}\n\n证据状态：review_status=needs-review；请人工核实关键事实。')
if __name__=='__main__': main()
