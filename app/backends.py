"""Adapters for logged-in agent CLIs. Codex remains the default backend."""
import json
from pathlib import Path
import shutil
import subprocess

LABELS = {"codex": "Codex", "claude": "Claude Code", "grok": "Grok Build", "cursor": "Cursor"}


def backend(env):
    name = (env.get("SPECTRA_BACKEND") or "codex").strip().lower()
    if name not in LABELS:
        raise ValueError("Unsupported SPECTRA_BACKEND")
    return name


def full_access(env):
    return env.get("SPECTRA_FULL_ACCESS", env.get("SPECTRA_CODEX_FULL_ACCESS", "0")) == "1"


def model(env):
    name = backend(env)
    return env.get(f"SPECTRA_{name.upper()}_MODEL") or (env.get("SPECTRA_MODEL", "") if name == "codex" else "")


def effort(env):
    name = backend(env)
    return env.get(f"SPECTRA_{name.upper()}_REASONING_EFFORT") or (
        env.get("SPECTRA_REASONING_EFFORT", "") if name == "codex" else "")


def _is_grok(path):
    resolved = Path(path).resolve()
    return ".grok" in resolved.parts or "grok" in resolved.name.lower()


def executable(env, name):
    configured = env.get(f"SPECTRA_{name.upper()}_COMMAND")
    if configured:
        command = configured
    elif name == "cursor":
        # Grok Build also installs an `agent` alias. Never treat that as Cursor.
        command = shutil.which("cursor-agent", path=env.get("PATH")) or shutil.which("agent", path=env.get("PATH")) or "cursor-agent"
    else:
        command = {"codex": "codex", "claude": "claude", "grok": "grok"}[name]
    resolved = shutil.which(command, path=env.get("PATH"))
    if name == "cursor" and resolved and _is_grok(resolved):
        raise ValueError("SPECTRA_CURSOR_COMMAND points to Grok, not Cursor")
    return command


def decode_output(raw, schema):
    """Unwrap CLI envelopes, then leave strict response validation to the caller."""
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Invalid CLI output")
    if value.get("is_error") or value.get("error") or value.get("errors"):
        raise ValueError("CLI returned an error")
    if set(value) == set(schema["required"]):
        return value
    for key in ("structured_output", "structuredOutput", "result", "text"):
        if key not in value:
            continue
        result = value[key]
        if isinstance(result, dict):
            return result
        if isinstance(result, str):
            text = result.strip()
            if text.startswith("```json\n") and text.endswith("\n```"):
                text = text[8:-4]
            elif text.startswith("```\n") and text.endswith("\n```"):
                text = text[4:-4]
            return json.loads(text)
    raise ValueError("Missing structured result")


def run_cli(env, prompt, schema, folder, root, child_env):
    name = backend(env)
    access = full_access(env)
    command = [executable(env, name)]
    selected_model, reasoning = model(env), effort(env)
    if name == "claude":
        command += ["--print", "--output-format", "json", "--json-schema", json.dumps(schema),
                    "--no-session-persistence", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
                    "--permission-mode", "dontAsk"]
        # Explicit allowlisting works for unattended root deployments too; bypass mode may reject root.
        tools = "Bash,Read,Write,Edit,Glob,Grep" if access else ""
        command += ["--tools", tools]
        if access:
            command += ["--allowedTools", tools]
        if reasoning:
            if reasoning not in ("low", "medium", "high", "max"):
                raise ValueError("Invalid Claude reasoning effort")
            command += ["--effort", reasoning]
        stdin = prompt
    elif name == "grok":
        path = Path(folder) / "prompt.txt"
        path.write_text(prompt, encoding="utf-8")
        command += ["--prompt-file", str(path), "--output-format", "json", "--json-schema", json.dumps(schema),
                    "--no-subagents", "--no-auto-update"]
        if access:
            command += ["--always-approve", "--sandbox", "off"]
        else:
            command += ["--tools", "", "--deny", "*", "--disable-web-search"]
        if reasoning:
            if reasoning not in ("none", "minimal", "low", "medium", "high", "xhigh", "max"):
                raise ValueError("Invalid Grok reasoning effort")
            command += ["--reasoning-effort", reasoning]
        stdin = ""
    elif name == "cursor":
        path = Path(folder) / "prompt.txt"
        path.write_text(prompt + "\nResponse JSON schema:\n" + json.dumps(schema), encoding="utf-8")
        # Context is read from a file: transcript text never enters process arguments.
        command += ["--print", "--output-format", "json", "--trust"]
        if access:
            command += ["--force", "--sandbox", "disabled"]
        else:
            command += ["--mode", "ask", "--sandbox", "enabled"]
        command += [f"Read @{path} for the complete Spectra request and response schema. Follow that request and return only the specified JSON object."]
        if reasoning:
            raise ValueError("Cursor reasoning effort is selected through its model, not a CLI effort flag")
        stdin = ""
    else:
        raise ValueError("Codex uses its existing native adapter")
    if selected_model:
        command += ["--model", selected_model]
    output = Path(folder) / "cli-output.json"
    with output.open("w", encoding="utf-8") as stream:
        result = subprocess.run(command, input=stdin, text=True, stdout=stream,
                                stderr=subprocess.DEVNULL, cwd=root if access else folder,
                                env=child_env, timeout=180)
    if result.returncode or output.stat().st_size > 2_000_000:
        raise ValueError("CLI failed or returned too much output")
    return decode_output(output.read_text(encoding="utf-8"), schema)
