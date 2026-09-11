# Codex instructions

This repository is a generic Codex–Obsidian literature manager. When a user message contains one or more PDF files, automatically process them unless the user explicitly asks not to. Read `00_系统/处理协议.md`, `00_系统/审查清单.md`, and the three templates before writing. Create/update one Paper Note, one ML/AI Workflow Note, and only justified atomic knowledge cards. Preserve human text, use DOI then normalized title/year matching, add page/section anchors and short quotations, mark uncertain facts `待核实`, and keep `review_status: needs-review`.

After writing notes, run `python3 scripts/research_search.py index`. If embeddings are unavailable, preserve notes and report that indexing was not updated. Return a concise processing report.
