# Results 承诺-证据映射（Results-as-Validation）

> 来源: PaperSpine results_validation。**每个 Results 小节都必须验证一个贡献承诺（C1/C2…）。**
> 一个只报数字、不对应任何贡献的小节 = 该删或该补进引言。『Contribution Claim Tested』与『Result/Evidence』两列不可为空。
> 校验: `python toolbox.py results-validation <项目目录>`。draft 开始前填写。

| Results 小节 | Contribution Claim Tested | Result/Evidence | Figure/Table | Confirmatory Condition | Allowed Interpretation | Interpretation NOT Allowed |
|------|---------------------------|-----------------|--------------|------------------------|------------------------|----------------------------|
| 4.1 __（对应 C1） | C1: __ | __（具体数字/Δ） | 图X / 表Y | __（哪个split/预算/seed） | __（最强诚实读法） | __（该行不授权的过度声称） |
| 4.2 __（对应 C2） | C2: __ | __ | 图X / 表Y | __ | __ | __ |
| 4.3 __ | C3: __ | __ | 图X / 表Y | __ | __ | __ |

## 填表说明
- **Results 小节**：真实小节标题/编号，让校验收敛到一个真实位置。
- **Contribution Claim Tested**：对应 Introduction 贡献列表的 C1/C2…；空 = 验证不了任何承诺（硬失败）。
- **Result/Evidence**：具体数字/Δ/定性发现；空 = 承诺没有结果（硬失败）。
- **Confirmatory Condition**：结论成立的确切条件（split/预算/seed/硬件），防止过度泛化。
- **Allowed Interpretation**：证据支持的最强诚实句子——你被授权写的话。
- **Interpretation NOT Allowed**：最诱人但本行不授权的过度声称；**填它**是防止 Discussion 悄悄夸大（审稿人"overclaiming"投诉的头号元凶）。

## 检查逻辑
- 声明了 N 个贡献，必须至少 N 个小节对应；否则要么贡献没被测试，要么声称无支撑。
- 反向：某小节无法映射到任何贡献 → 那是填充，删掉，或补进引言成为一个新贡献。
