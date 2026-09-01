# Materials Chemistry + Machine Learning Research Skills

为材料化学与机器学习研究整理的精选 skill 集合，覆盖“文献与实验设计 → 材料数据与结构 → MLIP/性质预测 → DFT/分子动力学”的常见工作流。

## 分类

### literature

- `literature-review`：系统检索、筛选、综合材料化学文献。
- `citation-management`：提取 DOI/元数据、生成和校验 BibTeX。
- `experimental-design`：DOE、随机化、分块和自适应实验设计。

### materials-data

- `pymatgen`：晶体结构、组成、对称性、格式转换和相图分析。
- `database-lookup`：按材料数据库指南检索 Materials Project、COD、PubChem 等资源。
- `mat-db-mp`：查询 Materials Project 的结构、稳定性、弹性、磁性和相似材料。
- `mat-phase-diagram`：从 Materials Project 数据生成组成空间相图。
- `mat-stability`：计算形成能、凸包距离和热力学稳定性。
- `exploratory-data-analysis`：检查数据质量、分布、缺失值、异常值和数据泄漏。

### materials-ml

- `deepchem`：分子/材料特征化、图神经网络和性质预测。
- `datamol`：分子读写、标准化、构象、描述符和可视化。
- `ml-foundation-potentials`：选择 MACE、MatGL、CHGNet、TensorNet、FAIRChem 等基础势。
- `ml-property-predictor`：基于 MACE/MatGL 表征训练自定义材料性质预测头。
- `ml-mlip-benchmark`：评估能量、力、应力 MAE/RMSE 并生成 parity plot。
- `ml-mace-finetune`：用自有 DFT 数据微调 MACE。
- `ml-matgl-finetune`：用自有 DFT 数据微调 MatGL/CHGNet/M3GNet。
- `ml-bayesian-optimization`：针对昂贵模拟或实验做单目标/多目标贝叶斯优化。
- `ml-generative-mattergen`：生成晶体结构候选并开展材料设计。

### atomistic-simulation

- `mat-dft-vasp`：准备 VASP 输入、解析结果并组织 DFT 工作流。
- `mat-lammps-md`：用 MACE/MatGL/FAIRChem 势运行 LAMMPS 分子动力学。
- `molecular-dynamics`：用 MDAnalysis 等工具分析轨迹、扩散和动力学性质。

## 推荐组合

```text
文献综述 → literature-review + citation-management
材料数据集 → mat-db-mp + pymatgen + exploratory-data-analysis
性质预测 → ml-foundation-potentials + ml-property-predictor
模型评估 → ml-mlip-benchmark
模型适配 → ml-mace-finetune 或 ml-matgl-finetune
候选优化 → ml-bayesian-optimization 或 ml-generative-mattergen
物理验证 → mat-dft-vasp + mat-lammps-md + molecular-dynamics
```

## 使用方式

先阅读目标目录下的 `SKILL.md`，然后在对话中直接描述研究任务，例如：

```text
用 Materials Project 查询 Li-Fe-P-O 体系中稳定结构，计算凸包距离并筛选候选。
```

```text
用 MACE/MatGL 在我的 DFT 数据上训练 bulk modulus 预测器，并划分验证集。
```

```text
对这组候选材料做贝叶斯优化，目标是最小化 formation energy、最大化 bandgap。
```

涉及 GPU、VASP、LAMMPS、Materials Project API 或模型 API 时，按 `SKILL.md` 的环境、密钥和单位约定执行。模型输出与 DFT 标签比较前，务必核对能量、力、应力的单位和符号约定。

## 来源与许可证

本目录是从两个公开仓库精选整理而来，具体映射、提交版本和许可证见 [`SOURCES.md`](./SOURCES.md)。各 skill 的原始许可证和引用要求优先于本目录说明。
