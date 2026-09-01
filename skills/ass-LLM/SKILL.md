---
name: ass-LLM
description: |
  External LLM Proxy Skill. Forward tasks to external LLMs via zetatechs API.
  Claude acts as a pure relay — forward user's task and return the external LLM's response verbatim.

  Available models: gpt-5.6-sol, gpt-5.5, gemini-3.5-flash, gemini-3-flash-preview.

  Chinese triggers: "ass-LLM", "用外部模型", "调用外部LLM", "用 gpt", "用 gemini", "代理模式"
  English triggers: "ass-LLM", "use external LLM", "proxy mode", "delegate to", "forward to gpt", "forward to gemini"

  IMPORTANT: Only trigger when the user EXPLICITLY asks to delegate to an external LLM.
  Do NOT trigger for normal conversation.
---

# ass-LLM — External LLM Proxy

**When this skill is active, you are a RELAY, not an assistant.**

Your sole job: forward the user's task to an external LLM API and return its response verbatim.

## Core Rules

1. **Do NOT answer using your own knowledge.** Even if you know the answer, you MUST call the API.
2. **Do NOT add commentary, analysis, or corrections** to the external LLM's response.
3. **Do NOT summarize or paraphrase** — present the complete response as-is.
4. **After relaying the response, STOP.** Don't offer follow-ups.

## Workflow

1. Parse the user's message to extract:
   - **Model**: which model the user specified (e.g., "用 gpt-5.6-sol" → `gpt-5.6-sol`)
   - **Task**: the actual question/task they want forwarded
2. Confirm: `> [ass-LLM] Calling **{model}**...`
3. Run the Python script below with the extracted variables.
4. Present the response in this format:

```
---
**[ass-LLM] Response from {model}:**

{external LLM's response text — verbatim, unedited}

---
{model} | {input_tokens} in + {output_tokens} out = {total_tokens} total
```

5. **STOP.**

## Python Script Template

Replace `MODEL` and `USER_MESSAGE`, then execute with `python3`.

```python
import json, sys, urllib.request, urllib.error

API_KEY = "sk-EOfB9uDRyDkL1V1EXLEI3V0Cc271znqx9a7SrfISq04huxyR"
BASE_URL = "https://api.zetatechs.com"
MODEL = "gpt-5.6-sol"           # Replace with user-specified model
USER_MESSAGE = "..."         # Replace with user's task
SYSTEM_MESSAGE = "You are a helpful AI assistant. Answer the user's question thoroughly and accurately. Respond in the same language the user uses."

payload = {
    "model": MODEL,
    "messages": [
        {"role": "system", "content": SYSTEM_MESSAGE},
        {"role": "user", "content": USER_MESSAGE}
    ],
    "temperature": 0.7,
    "max_tokens": 8192
}

body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
url = f"{BASE_URL}/v1/chat/completions"

req = urllib.request.Request(
    url, data=body,
    headers={
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
)

print(f"[ass-LLM] Calling {MODEL}...", file=sys.stderr)

try:
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))
except urllib.error.HTTPError as e:
    error_body = e.read().decode("utf-8", errors="replace")
    print(f"HTTP_ERROR|{e.code}|{error_body}", file=sys.stderr)
    sys.exit(1)
except urllib.error.URLError as e:
    print(f"NETWORK_ERROR|{str(e.reason)}", file=sys.stderr)
    sys.exit(2)
except Exception as e:
    print(f"EXCEPTION|{type(e).__name__}|{str(e)}", file=sys.stderr)
    sys.exit(3)

choices = data.get("choices", [])
if not choices:
    print("EMPTY_RESPONSE|No choices in API response", file=sys.stderr)
    sys.exit(4)

content = choices[0].get("message", {}).get("content", "")
if not content:
    print("EMPTY_CONTENT|Assistant returned empty content", file=sys.stderr)
    sys.exit(5)

# stdout → relayed to user
print(content)

# stderr → metadata
usage = data.get("usage", {})
finish = choices[0].get("finish_reason", "unknown")
print(f"TOKENS|{usage.get('prompt_tokens', 0)}|{usage.get('completion_tokens', 0)}|{usage.get('total_tokens', 0)}", file=sys.stderr)
print(f"FINISH|{finish}", file=sys.stderr)
```

## Error Handling

If the script exits non-zero, parse stderr and report to user:

| Pattern | User Message |
|---------|-------------|
| `HTTP_ERROR\|401\|...` | [ass-LLM] API authentication failed (401). |
| `HTTP_ERROR\|429\|...` | [ass-LLM] Rate limited (429). Wait and retry. |
| `NETWORK_ERROR\|...` | [ass-LLM] Network error, check connection. |
| Other errors | [ass-LLM] Request failed: {details}. |

**On error: do NOT answer the question yourself.** Report the error and let the user decide next steps.
