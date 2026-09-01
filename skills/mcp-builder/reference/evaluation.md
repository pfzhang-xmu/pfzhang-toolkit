# MCP Server Evaluation Guide

## Overview

This document provides guidance on creating comprehensive evaluations for MCP servers.

## Quick Reference

### Evaluation Requirements
- Create 10 human-readable questions
- Questions must be READ-ONLY, INDEPENDENT, NON-DESTRUCTIVE
- Each question requires multiple tool calls
- Answers must be single, verifiable values
- Answers must be STABLE (won't change over time)

### Output Format
```xml
<evaluation>
   <qa_pair>
      <question>Your question here</question>
      <answer>Single verifiable answer</answer>
   </qa_pair>
</evaluation>
```

## Question Guidelines

1. **Questions MUST be independent** - Not dependent on answers to other questions
2. **Questions MUST require ONLY NON-DESTRUCTIVE AND IDEMPOTENT tool use**
3. **Questions must be REALISTIC, CLEAR, CONCISE, and COMPLEX**
4. **Questions must require deep exploration** - Multi-hop questions requiring sequential tool calls
5. **Questions may require extensive paging** through multiple pages of results
6. **Questions must require deep understanding** rather than surface-level knowledge
7. **Questions must not be solvable with straightforward keyword search**
8. **Questions should stress-test tool return values**
9. **Questions should MOSTLY reflect real human use cases**
10. **Questions may require dozens of tool calls**
11. **Include ambiguous questions** that force difficult decisions
12. **Questions must be designed so the answer DOES NOT CHANGE**

## Answer Guidelines

1. **Answers must be VERIFIABLE via direct string comparison**
2. **Answers should generally prefer HUMAN-READABLE formats**
3. **Answers must be STABLE/STATIONARY** - Based on "closed" concepts
4. **Answers must be CLEAR and UNAMBIGUOUS**
5. **Answers must be DIVERSE** in modalities and formats
6. **Answers must NOT be complex structures** unless easily verifiable

## Evaluation Process

1. **Documentation Inspection**: Read API docs to understand endpoints
2. **Tool Inspection**: List available tools, understand schemas
3. **Developing Understanding**: Iterate until you have good grasp
4. **Read-Only Content Inspection**: Use tools to explore data
5. **Task Generation**: Create 10 human-readable questions

## Output Format

```xml
<evaluation>
   <qa_pair>
      <question>Find the project created in Q2 2024 with the highest number of completed tasks. What is the project name?</question>
      <answer>Website Redesign</answer>
   </qa_pair>
</evaluation>
```

## Running Evaluations

```bash
# Install dependencies
pip install anthropic mcp

# Run evaluation (stdio)
python scripts/evaluation.py -t stdio -c python -a my_server.py evaluation.xml

# Run evaluation (HTTP)
python scripts/evaluation.py -t http -u https://example.com/mcp evaluation.xml
```
