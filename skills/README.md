# Skills Catalog

This directory contains reusable skills maintained for personal workflows.

## General-purpose skills

| Skill | Purpose | Links |
| --- | --- | --- |
| [`grilling`](./grilling) | Stress-test plans, decisions, and ideas through rounds of structured questions. | [Browse](./grilling) · [Raw SKILL.md](https://raw.githubusercontent.com/pfzhang-xmu/pfzhang-toolkit/main/skills/grilling/SKILL.md) |

### Installing a skill

Clone this repository and copy the skill directory into the skills directory used by your agent:

```bash
git clone https://github.com/pfzhang-xmu/pfzhang-toolkit.git
cp -R pfzhang-toolkit/skills/grilling <your-agent-skills-directory>/
```

The `grilling` package includes `SKILL.md`, `agents/openai.yaml`, and an MIT license.

## Office and document skills

The `office/` category groups skills for creating, editing, converting, and validating common office and document formats:

| Skill | Purpose |
| --- | --- |
| [`docx`](./office/docx) | Create, edit, analyze, and redline Word documents. |
| [`pptx`](./office/pptx) | Create, edit, analyze, and validate PowerPoint presentations. |
| [`xlsx`](./office/xlsx) | Create, edit, analyze, and recalculate spreadsheets. |
| [`pdf`](./office/pdf) | Read, create, inspect, render, and work with PDF files and forms. |
| [`markitdown`](./office/markitdown) | Convert office files and other documents into Markdown or plain text. |

Each skill keeps its own `SKILL.md`, supporting scripts, references, templates, and license files. Read the skill's `SKILL.md` before using it.
