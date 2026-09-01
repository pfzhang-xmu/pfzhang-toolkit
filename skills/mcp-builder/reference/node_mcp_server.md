# Node/TypeScript MCP Server Implementation Guide

## Overview

This document provides Node/TypeScript-specific best practices and examples for implementing MCP servers.

## Quick Reference

### Key Imports
```typescript
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import express from "express";
import { z } from "zod";
```

### Server Initialization
```typescript
const server = new McpServer({ name: "service-mcp-server", version: "1.0.0" });
```

### Tool Registration Pattern
```typescript
server.registerTool("tool_name", {
  title: "Tool Display Name",
  description: "What the tool does",
  inputSchema: { param: z.string() },
}, async ({ param }) => {
  return { content: [{ type: "text", text: `Result: ${param}` }] };
});
```

## Server Naming Convention

- **Format**: `{service}-mcp-server` (lowercase with hyphens)
- **Examples**: `github-mcp-server`, `jira-mcp-server`, `stripe-mcp-server`

## Project Structure

```
{service}-mcp-server/
├── package.json
├── tsconfig.json
├── src/
│   ├── index.ts          # Main entry point
│   ├── types.ts          # TypeScript type definitions
│   ├── tools/            # Tool implementations
│   ├── services/         # API clients and utilities
│   ├── schemas/          # Zod validation schemas
│   └── constants.ts      # Shared constants
└── dist/                 # Built JavaScript files
```

## Tool Naming

Use snake_case with service prefix:
- `slack_send_message` instead of `send_message`
- `github_create_issue` instead of `create_issue`

## Zod Schemas for Input Validation

```typescript
import { z } from "zod";

const CreateUserSchema = z.object({
  name: z.string().min(1).max(100),
  email: z.string().email(),
  age: z.number().int().min(0).max(150)
}).strict();
```

## Package Configuration

### package.json
```json
{
  "name": "{service}-mcp-server",
  "version": "1.0.0",
  "type": "module",
  "main": "dist/index.js",
  "scripts": {
    "start": "node dist/index.js",
    "dev": "tsx watch src/index.ts",
    "build": "tsc"
  },
  "dependencies": {
    "@modelcontextprotocol/sdk": "^1.6.1",
    "axios": "^1.7.9",
    "zod": "^3.23.8"
  },
  "devDependencies": {
    "@types/node": "^22.10.0",
    "tsx": "^4.19.2",
    "typescript": "^5.7.2"
  }
}
```

## Tool Annotations

| Annotation | Type | Default | Description |
|-----------|------|---------|-------------|
| `readOnlyHint` | boolean | false | Tool does not modify its environment |
| `destructiveHint` | boolean | true | Tool may perform destructive updates |
| `idempotentHint` | boolean | false | Repeated calls have no additional effect |
| `openWorldHint` | boolean | true | Tool interacts with external entities |

## Quality Checklist

- [ ] All tools registered using `registerTool` with complete configuration
- [ ] All tools include `title`, `description`, `inputSchema`, and `annotations`
- [ ] Annotations correctly set
- [ ] All tools use Zod schemas with `.strict()` enforcement
- [ ] Error messages are clear and actionable
- [ ] `npm run build` completes successfully
- [ ] Server name follows format: `{service}-mcp-server`
