from pathlib import Path
import subprocess,sys
root=Path(__file__).resolve().parents[1]
venv=root/'.venv'
if not venv.exists(): subprocess.run([sys.executable,'-m','venv',str(venv)],check=True)
pip=venv/'bin'/'pip'; py=venv/'bin'/'python'
for rel in ['00_系统','10_论文','20_知识/方法','20_知识/流程','20_知识/批判','20_知识/启发','90_附件/论文','.research_index']:(root/rel).mkdir(parents=True,exist_ok=True)
subprocess.run([str(pip),'install','-r',str(root/'requirements.txt')],check=False)
print(f'虚拟环境已准备：{venv}')
print('目录已初始化；如需语义索引，请配置 vault 外 embedding.env。')
