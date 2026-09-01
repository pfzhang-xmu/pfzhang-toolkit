---
name: general-property-units
description: Reference guide for energy, force, and stress units across MLIPs, DFT codes, and ASE, including conversion factors.
category: [general, machine-learning]
---

# Units Reference for Atomistic Simulations

## Goal

Provide a single authoritative reference for units of **energy**, **forces**, and **stress** across all MLIPs, DFT codes, and simulation tools used in this project, including the conversions applied internally.

## Project Standard

All internal representations follow the **ASE (Atomic Simulation Environment)** convention:

| Quantity | Standard Unit | Notes |
|:---------|:-------------|:------|
| **Energy** | eV | Total energy of the system |
| **Energy per atom** | eV/atom | Used for MAE reporting and training labels |
| **Forces** | eV/Å | Negative gradient of energy w.r.t. position |
| **Stress** | eV/Å³ | Voigt notation, 6-component (xx, yy, zz, yz, xz, xy) |

## MLIP Model Units

### Prediction (Inference)

**The raw torch model and the ASE calculator do not return the same stress units.**
Most calculators apply a unit conversion on the way out; two do not. Read the stress
column for the layer you are actually calling.

Energy is eV and forces are eV/Å everywhere, at both layers. Only stress varies:

| Model (calculator class) | raw model output | ASE calculator output | conversion in the ASE layer |
|:-------------------------|:-----------------|:----------------------|:----------------------------|
| **MACE** (`MACECalculator`) | eV/Å³ | eV/Å³ | none |
| **UMA / FairChem** (`FAIRChemCalculator`) | eV/Å³ | eV/Å³ | none |
| **CHGNet standalone** (`CHGNetCalculator`) | **GPa** | eV/Å³ | `× stress_weight`, default `1/160.21766208` |
| **CHGNet / M3GNet / TensorNet via MatGL** (`PESCalculator`) | **GPa** | **GPa** unless asked otherwise | none by default — pass `stress_unit="eV/A3"` |

Measured on one compressed Si cell (xx component), 2026-08-25:

| path | raw | calculator default | calculator eV/Å³ |
|:-----|----:|-------------------:|-----------------:|
| MACE-MP small | -0.0762876 | -0.0762876 | — |
| UMA `uma-s-1p1` (omat) | -0.0821809 | -0.0821810 | — |
| CHGNet standalone 0.4.2 | -13.906347 | -0.0867966 | — |
| MatGL `TensorNet-PES-MatPES-PBE-2025.2` | -10.780773 | -10.780773 | -0.067288 |
| MatGL `CHGNet-PES-MatPES-PBE-2025.2.10` | — | -15.229350 | -0.095054 |
| MatGL `M3GNet-PES-MatPES-2025.2` | — | -20.293510 | -0.126662 |

Every ratio above is exactly `160.21766208`, i.e. GPa per eV/Å³.

> [!IMPORTANT]
> `matgl.ext.ase.PESCalculator` takes `stress_unit: Literal["eV/A3", "GPa"] = "GPa"`,
> so **using it as a drop-in ASE calculator gives GPa, not ASE units** — it prints a
> runtime warning saying so. Calling `Potential.forward` directly also returns GPa.
> Pass `PESCalculator(potential=model, stress_unit="eV/A3")`, or divide by
> `160.21766208`. Mixing this up is a 160x error, not a sign error.

> [!NOTE]
> All of the above are in the **ASE sign convention: positive = tensile,
> compression negative**. A compressed cell therefore gives negative diagonal
> stress at both layers. DFT codes may differ — see [VASP](#vasp) below.

### Training Input Labels

Training labels in `training_data.json` are stored in ASE standard units (eV, eV/Å, eV/Å³). Conversions to trainer-specific units are handled **automatically** inside each wrapper:

| Trainer | Energy Input | Force Input | Stress Input | Internal Conversion |
|:--------|:-------------|:------------|:-------------|:-------------------|
| **MACE** | eV | eV/Å | eV/Å³ | None — trains in eV/Å³ |
| **FairChem (UMA)** | eV | eV/Å | eV/Å³ | None — trains in eV/Å³ |
| **MatGL (CHGNet/M3GNet)** | eV | eV/Å | **GPa** (converted) | `eV/Å³ → GPa` in `_prepare_training_data` |

> [!IMPORTANT]
> **MatGL is the only trainer that requires stress conversion.** The conversion from eV/Å³ → GPa is performed automatically inside `MATGLWrapper._prepare_training_data()`. Users should always provide stress labels in eV/Å³.

### Training Output (Saved Metrics)

Each MLIP trainer natively reports MAE in **eV**. All wrappers apply a **×1000 conversion** to save MAE values in **meV** to `training_history.json` and plot axes in `training_history.png`, for human readability and consistent cross-model comparison:

| Trainer | Native Energy MAE | Native Force MAE | Native Stress MAE | Saved Unit |
|:--------|:------------------|:-----------------|:-------------------|:-----------|
| **MACE** | eV/atom | eV/Å | eV/Å³ | **meV** (×1000) |
| **FairChem (UMA)** | eV/atom | eV/Å | eV/Å³ | **meV** (×1000) |
| **MatGL (CHGNet/M3GNet)** | eV/atom | eV/Å | GPa → eV/Å³ | **meV** (×1000) |

The `training_history.json` keys and their units:

| Key | Unit |
|:----|:-----|
| `energy_mae_train` / `energy_mae_val` | meV/atom |
| `force_mae_train` / `force_mae_val` | meV/Å |
| `stress_mae_train` / `stress_mae_val` | meV/Å³ |
| `loss_train` / `loss_val` | Dimensionless (weighted combination) |

> [!NOTE]
> For MatGL stress: the trainer computes MAE in GPa internally. The wrapper converts back to eV/Å³ first, then multiplies by 1000 to get meV/Å³, matching the other wrappers.

## DFT Code Units

### VASP

| Quantity | VASP Internal | VASP OUTCAR | Conversion to ASE Standard |
|:---------|:-------------|:------------|:--------------------------|
| **Energy** | eV | eV | None needed |
| **Forces** | eV/Å | eV/Å | None needed |
| **Stress** | kB (kilo-Bar) | kB (and GPa) | `kB × 0.1 = GPa`, then `GPa × 0.0062415 = eV/Å³` |

> [!NOTE]
> VASP stores stress internally in **kB** (kilo-Bar). The `vasprun.xml` parser in pymatgen returns stress in kB. The Atomate2 MCP tool applies the conversion `kB → eV/Å³` automatically when `convert_units=True` (default).

### Sign Convention

VASP reports stress with the **opposite sign** to the physics and ASE convention:
- VASP: positive = **compressive** (pressure-like)
- ASE/Physics/MLIPs: positive = **tensile**

The sign flip is handled during VASP output parsing (e.g. in the `atomate2` MCP tool, VASP stress is multiplied by `-1` in addition to the unit conversion).

## Common Conversion Factors

| From | To | Factor | ASE Code |
|:-----|:---|:-------|:---------|
| GPa | eV/Å³ | 0.00624150913 | `ase.units.GPa` |
| eV/Å³ | GPa | 160.21766208 | `1.0 / ase.units.GPa` |
| kB | GPa | 0.1 | — |
| kB | eV/Å³ | 0.000624150913 | `0.1 * ase.units.GPa` |
| eV | kJ/mol | 96.4853 | `ase.units.kJ / ase.units.mol` |
| eV | kcal/mol | 23.0605 | `ase.units.kcal / ase.units.mol` |
| Å | Bohr | 1.8897259886 | `1.0 / ase.units.Bohr` |

## Quick Reference: Python Conversions

```python
# Env: base-agent
from ase import units

# Stress conversions
stress_GPa = stress_eV_per_A3 / units.GPa        # eV/Å³ → GPa
stress_eV_per_A3 = stress_GPa * units.GPa         # GPa → eV/Å³
stress_eV_per_A3 = stress_kB * 0.1 * units.GPa    # kB → eV/Å³

# Energy conversions
energy_kJ_per_mol = energy_eV * units.kJ / units.mol
energy_kcal_per_mol = energy_eV * units.kcal / units.mol
```

## Constraints

- **Never** mix unit systems within a single workflow.
- **Always** verify stress units when comparing MLIP predictions to DFT references.
- Training data JSON files must use eV/Å³ for stress — wrapper-internal conversion handles the rest.
- When reporting MAE in papers/docs, specify the unit explicitly (e.g., "Force MAE: 50 meV/Å").
---

**Author:** Bowen Deng
**Contact:** [GitHub @learningmatter-mit](https://github.com/learningmatter-mit)
