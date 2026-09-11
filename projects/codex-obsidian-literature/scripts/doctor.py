from pathlib import Path
import sys,importlib.util
root=Path(__file__).resolve().parents[1]
print('Python:',sys.version.split()[0])
for m in ['pypdf','yaml','chromadb']: print(m, 'OK' if importlib.util.find_spec(m) else 'MISSING')
for p in ['00_系统/研究画像.md','00_系统/处理协议.md','10_论文','20_知识','90_附件/论文']: print(p, 'OK' if (root/p).exists() else 'MISSING')
print('Embedding:', 'configured' if (Path.home()/'.config/obsidian-codex/embedding.env').exists() else 'not configured (keyword search remains available)')
