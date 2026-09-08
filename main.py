import shlex
import sys
from datetime import datetime

import anthropic
import subprocess

CONTAINER = "agent-sbx"
TIMEOUT = 60
KEEP_RECENT = 4

def truncate(text: str, limit: int = 4000) -> str:
    if len(text) <= limit:
        return text
    return f"[... {len(text)-limit} chars omitted ...]\n{text[-limit:]}"

def compress(messages, keep=KEEP_RECENT):
    idxs = [
        i for i, m in enumerate(messages)
        if m["role"] == "user"
        and isinstance(m["content"], list)
        and any(b.get("type") == "tool_result" for b in m["content"])
    ]
    for i in idxs[:-keep]:
        for b in messages[i]["content"]:
            if b.get("type") == "tool_result" and b["content"] != "[truncated]":
                b["content"] = "[truncated]"
    return messages

def read_multiline_prompt():
    print("\n프롬프트 입력 (END로 완료, exit으로 종료)")

    lines = []

    while True:
        line = input("> ")

        if line == "END":
            break

        lines.append(line)

    return "\n".join(lines)

def print_text(text):
    print(text)
    return text

def run_shell(cmd: str) -> str:
    try:
        p = subprocess.run(
            ["docker", "exec", "-w", "/workspace", CONTAINER, "sh", "-c", cmd],
            capture_output=True, text=True, timeout=TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return f"exit_code: -1\nstderr:\ntimeout after {TIMEOUT}s"

    return (
        f"exit_code: {p.returncode}\n"
        f"stdout:\n{truncate(p.stdout)}\n"
        f"stderr:\n{truncate(p.stderr)}"
    )

def _read(path: str) -> str:
    p = subprocess.run(
        ["docker", "exec", CONTAINER, "cat", path],
        capture_output=True, text=True, timeout=TIMEOUT,
    )
    if p.returncode != 0:
        raise FileNotFoundError(p.stderr.strip())
    return p.stdout

def _write(path: str, content: str) -> None:
    p = subprocess.run(
        ["docker", "exec", "-i", CONTAINER, "sh", "-c", f"cat > {shlex.quote(path)}"],
        input=content, capture_output=True, text=True, timeout=TIMEOUT,
    )
    if p.returncode != 0:
        raise IOError(p.stderr.strip())

def str_replace(path: str, old_str: str, new_str: str) -> str:
    try:
        content = _read(path)
    except FileNotFoundError as e:
        return f"error: cannot read {path}: {e}"

    n = content.count(old_str)

    if n == 0:
        first = old_str.splitlines()[0] if old_str else ""
        hint = ("the first line WAS found — check indentation, trailing spaces, or newlines"
                if first and first in content
                else "the first line was not found either — check the path and the text")
        return f"error: old_str not found in {path}. {hint}"

    if n > 1:
        return f"error: old_str appears {n} times in {path}; it must be unique. Include more surrounding lines."

    try:
        _write(path, content.replace(old_str, new_str))
    except IOError as e:
        return f"error: cannot write {path}: {e}"

    return f"ok: replaced 1 occurrence in {path}"

if __name__ == '__main__':
    tool_function_mapping = {
        "print": print_text,
        "get_current_time": lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        # "run_shell": run_shell,
        "read_file": _read,
        "write_file": _write,
        "str_replace": str_replace,
    }
    tools = [
        {
            "name": "print",
            "description": "Prints the input to the console",
            "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},

        },
        {
            "name": "get_current_time",
            "description": "Returns the current time",
            "input_schema": {"type": "object", "properties": {}, "required": []},
        },
        # {
        #     "name": "run_shell",
        #     "description": (
        #         "Run a shell command in the project workspace and return "
        #         "its exit code, stdout and stderr. "
        #         "Working directory is /workspace. There is NO network access."
        #     ),
        #     "input_schema": {
        #         "type": "object",
        #         "properties": {
        #             "cmd": {"type": "string", "description": "The shell command to run."}
        #         },
        #         "required": ["cmd"],
        #     },
        # },
        {
            "name": "read_file",
            "description": "Read a file from the project workspace and return its content. ",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"}
                }
            },
        },
        {
            "name": "write_file",
            "description": "Write a file to the project workspace. ",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"}
                }
            }
        },
        {
            "name": "str_replace",
            "description": (
                "Replace an exact string in a file. "
                "old_str must match the file content exactly, including whitespace and "
                "newlines, and must appear exactly once. "
                "Read the file first to get the exact text."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_str": {"type": "string"},
                    "new_str": {"type": "string"},
                },
                "required": ["path", "old_str", "new_str"],
            },
        }
    ]

    messages=[]

    while(True):
        prompt = read_multiline_prompt()

        if prompt == "exit":
            break

        if not prompt.strip():
            continue

        messages.append({"role": "user", "content": prompt})

        for step in range(10):
            messages = compress(messages)
            response = anthropic.Anthropic().messages.create(
                model="claude-haiku-4-5",
                tools=tools,
                max_tokens=1024,
                messages=messages,
                extra_headers={"anthropic-workspace-id": "wrkspc_011gsTnX6eaZbgyruh9yBpfi"}
            )
            print(f"--- step {step}: {response}")
            print(f"step {step}: in={response.usage.input_tokens} out={response.usage.output_tokens}")

            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason != "tool_use":
                break

            results = []
            for b in response.content:
                if b.type != "tool_use":
                    continue
                fn = tool_function_mapping[b.name]
                result = fn(**b.input)
                results.append({
                    "type": "tool_result",
                    "tool_use_id": b.id,
                    "content": str(result)
                })
            messages.append({"role": "user", "content": results})