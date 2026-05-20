#!/usr/bin/env python3
import os
import json
import re
import shlex
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


MODEL_PATH = "models/BitNet-b1.58-2B-4T/ggml-model-i2_s.gguf"
BITNET_DIR = Path("/app/BitNet")
KNOWLEDGE_DIR = Path(os.environ.get("KNOWLEDGE_DIR", "/knowledge"))
N_PREDICT = os.environ.get("N_PREDICT", "512")
MAX_LIST_ITEMS = 200
MAX_FILE_BYTES = 12000
MAX_COMMAND_OUTPUT = 20000
COMMAND_TIMEOUT_SECONDS = 30
LLM_START_TIMEOUT_SECONDS = 180
LLM_RESPONSE_TIMEOUT_SECONDS = 240
LLM_HOST = os.environ.get("LLM_HOST", "127.0.0.1")
LLM_PORT = int(os.environ.get("LLM_PORT", "18080"))
LLM_URL = f"http://{LLM_HOST}:{LLM_PORT}"
LLM_LOG_PATH = Path(os.environ.get("LLM_LOG_PATH", "/tmp/bitnet-llm-server.log"))
SYSTEM_PROMPT = """You are BitNet running inside a Dockerized POSIX shell training image.
Answer normal user requests directly.
When asked to create shell programs, prefer strictly POSIX sh and terminal-size-aware code.
The wrapper around you can execute local tools such as ls, cat, find, and shell commands.
If a user asks for filesystem inspection or command execution, keep your answer brief because the wrapper will run the tool.
"""


def resolve_path(raw_path):
    path = raw_path.strip() if raw_path else "."
    expanded = os.path.expanduser(path)
    return Path(expanded).resolve()


def list_files(raw_path="."):
    path = resolve_path(raw_path)
    if not path.exists():
        return f"Path not found: {path}"
    if path.is_file():
        return f"{path} is a file."

    rows = []
    try:
        entries = sorted(path.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))
    except PermissionError:
        return f"Permission denied: {path}"

    for item in entries[:MAX_LIST_ITEMS]:
        suffix = "/" if item.is_dir() else ""
        rows.append(f"{item.name}{suffix}")

    if len(entries) > MAX_LIST_ITEMS:
        rows.append(f"... truncated, {len(entries) - MAX_LIST_ITEMS} more entries")

    header = f"Listing {path}:"
    return "\n".join([header, *rows]) if rows else f"{header}\n(empty)"


def read_text_file(raw_path):
    path = resolve_path(raw_path)
    if not path.exists():
        return f"Path not found: {path}"
    if not path.is_file():
        return f"Not a file: {path}"

    try:
        data = path.read_bytes()[:MAX_FILE_BYTES]
    except PermissionError:
        return f"Permission denied: {path}"

    text = data.decode("utf-8", errors="replace")
    if path.stat().st_size > MAX_FILE_BYTES:
        text += f"\n... truncated at {MAX_FILE_BYTES} bytes"
    return text


def find_files(name):
    needle = name.strip().lower()
    if not needle:
        return "Missing search term."

    matches = []
    for root, dirs, files in os.walk("."):
        dirs[:] = [item for item in dirs if item not in {".git", "__pycache__", ".cache"}]
        for item in dirs + files:
            if needle in item.lower():
                matches.append(str(Path(root, item)))
                if len(matches) >= MAX_LIST_ITEMS:
                    return "\n".join([*matches, "... truncated"])
    return "\n".join(matches) if matches else "No matches."


def run_shell_command(command):
    command = command.strip()
    if not command:
        return "Missing shell command."

    try:
        completed = subprocess.run(
            command,
            cwd=BITNET_DIR,
            shell=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=COMMAND_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        output = output[:MAX_COMMAND_OUTPUT]
        return f"Command timed out after {COMMAND_TIMEOUT_SECONDS} seconds.\n{output}".rstrip()

    output = completed.stdout or ""
    if len(output) > MAX_COMMAND_OUTPUT:
        output = output[:MAX_COMMAND_OUTPUT] + f"\n... truncated at {MAX_COMMAND_OUTPUT} characters"

    header = f"$ {command}\nexit code: {completed.returncode}"
    return f"{header}\n{output}".rstrip()


def read_posix_skill():
    skill_path = KNOWLEDGE_DIR / "skills" / "POSIX.md"
    if not skill_path.exists():
        return ""
    return skill_path.read_text(encoding="utf-8", errors="replace")[:8000]


def build_system_prompt():
    skill = read_posix_skill()
    if not skill:
        return SYSTEM_PROMPT
    return f"{SYSTEM_PROMPT}\nCompact POSIX shell knowledge loaded once at startup:\n{skill}"


class BitNetService:
    def __init__(self):
        self.process = None
        self.log_file = None

    def start(self):
        if self.process and self.process.poll() is None:
            return

        print("[llm] starting persistent BitNet server...", flush=True)
        try:
            LLM_LOG_PATH.unlink()
        except FileNotFoundError:
            pass
        self.log_file = LLM_LOG_PATH.open("ab")
        command = [
            "build/bin/llama-server",
            "-m",
            MODEL_PATH,
            "-c",
            "2048",
            "-t",
            "2",
            "-n",
            N_PREDICT,
            "-ngl",
            "0",
            "--temp",
            "0.8",
            "--host",
            LLM_HOST,
            "--port",
            str(LLM_PORT),
            "-cb",
            "-p",
            build_system_prompt(),
        ]
        self.process = subprocess.Popen(
            command,
            cwd=BITNET_DIR,
            stdin=subprocess.DEVNULL,
            stdout=self.log_file,
            stderr=subprocess.STDOUT,
            close_fds=True,
            start_new_session=True,
        )
        self._wait_for_health()
        self._poke()
        print("[llm] ready after health check and internal poke", flush=True)

    def ask(self, prompt):
        self.start()
        return self._completion(
            {
                "prompt": prompt,
                "n_predict": int(N_PREDICT),
                "temperature": 0.8,
                "stream": False,
            },
            timeout=LLM_RESPONSE_TIMEOUT_SECONDS,
        )

    def close(self):
        if self.process and self.process.poll() is None:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
                self.process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except OSError:
                    pass
        self.process = None
        if self.log_file:
            try:
                self.log_file.close()
            except OSError:
                pass
            self.log_file = None

    def _wait_for_health(self):
        deadline = time.monotonic() + LLM_START_TIMEOUT_SECONDS
        last_error = ""

        while time.monotonic() < deadline:
            if self.process and self.process.poll() is not None:
                raise RuntimeError(self._startup_failure("BitNet server exited during startup"))

            try:
                with urllib.request.urlopen(f"{LLM_URL}/health", timeout=2) as response:
                    if response.status == 200:
                        return
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                last_error = str(exc)
            time.sleep(1)

        raise RuntimeError(self._startup_failure(f"BitNet server health check timed out: {last_error}"))

    def _poke(self):
        content = self._completion(
            {
                "prompt": "Internal startup check. Reply with READY.",
                "n_predict": 8,
                "temperature": 0.1,
                "stream": False,
            },
            timeout=60,
        )
        if not content.strip():
            raise RuntimeError(self._startup_failure("BitNet server returned an empty startup poke"))

    def _completion(self, payload, timeout):
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{LLM_URL}/completion",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            raise RuntimeError(f"BitNet server request failed: {exc}") from exc

        parsed = json.loads(body)
        return str(parsed.get("content", "")).strip()

    def _startup_failure(self, message):
        log_tail = ""
        try:
            if LLM_LOG_PATH.exists():
                log_tail = LLM_LOG_PATH.read_text(encoding="utf-8", errors="replace")[-4000:]
        except OSError:
            pass
        if log_tail:
            return f"{message}.\nLast server log lines:\n{log_tail}"
        return message


def slugify_name(name):
    slug = re.sub(r"[^a-zA-Z0-9_.-]+", "-", name.strip().lower()).strip("-")
    return slug or "game"


def extract_shell_script(raw_text):
    fenced = re.findall(r"```(?:sh|shell)?\n(.*?)```", raw_text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        return fenced[-1].strip() + "\n"

    marker = raw_text.find("#!")
    if marker == -1:
        marker = raw_text.find("printf ")
    if marker == -1:
        return raw_text.strip() + "\n"

    script = raw_text[marker:]
    stop_markers = [
        "\nllama_perf_",
        "\nYou> ",
        "\nReferences:",
        "\nUse Case",
    ]
    stop = len(script)
    for stop_marker in stop_markers:
        index = script.find(stop_marker)
        if index != -1:
            stop = min(stop, index)
    return script[:stop].strip() + "\n"


def analyze_shell_script(script_path, raw_path):
    script = script_path.read_text(encoding="utf-8", errors="replace")
    findings = []

    checks = [
        ("bash shebang", r"^#!.*bash"),
        ("bash arrays", r"\w+=\(|\[[0-9@*]+\]"),
        ("double-bracket test", r"\[\["),
        ("read -n/-t", r"read\s+[^#\n]*-[^\n]*[nt]"),
        ("function keyword", r"(^|\n)\s*function\s+\w+"),
        ("local keyword", r"(^|\n)\s*local\s+\w+="),
        ("bash RANDOM", r"\$RANDOM"),
        ("process substitution", r"<\("),
        ("brace expansion", r"\{[0-9]+\.\.[0-9]+\}"),
    ]
    for label, pattern in checks:
        if re.search(pattern, script, flags=re.MULTILINE):
            findings.append(f"- {label}: found")

    syntax = subprocess.run(
        ["sh", "-n", str(script_path)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    syntax_result = syntax.stdout.strip() or "ok"
    if syntax.returncode != 0:
        findings.append("- POSIX syntax check failed")

    raw_size = raw_path.stat().st_size if raw_path.exists() else 0
    report = [
        f"# Analysis: {script_path.name}",
        "",
        f"- script: `{script_path}`",
        f"- raw capture: `{raw_path}`",
        f"- raw bytes: {raw_size}",
        f"- sh -n exit code: {syntax.returncode}",
        f"- sh -n output: `{syntax_result}`",
        "",
        "## Findings",
        *(findings or ["- no obvious POSIX portability blockers found"]),
        "",
        "## Minimal Remediation Hints",
        "- Prefer `#!/bin/sh` over Bash.",
        "- Replace arrays with space-separated `x,y` records.",
        "- Use `case` instead of `[[ ... ]]`.",
        "- Use `stty raw min 0 time 1` plus `dd bs=1 count=1` for non-blocking input.",
    ]
    return "\n".join(report) + "\n"


def generate_game(request, llm_service):
    parts = request.strip().split(maxsplit=1)
    name = slugify_name(parts[0] if parts else "game")
    description = parts[1] if len(parts) > 1 else name

    generated_dir = KNOWLEDGE_DIR / "generated"
    reports_dir = KNOWLEDGE_DIR / "reports"
    generated_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)

    prompt = f"""Create one complete terminal game in strictly POSIX sh.
Only output the shell script in one ```sh code block.
Game request: {description}
"""
    raw = llm_service.ask(prompt)
    raw_path = generated_dir / f"{name}.raw.txt"
    script_path = generated_dir / f"{name}.sh"
    report_path = reports_dir / f"{name}.md"

    raw_path.write_text(raw + "\n", encoding="utf-8")
    script = extract_shell_script(raw)
    script_path.write_text(script, encoding="utf-8")
    script_path.chmod(0o755)
    report_path.write_text(analyze_shell_script(script_path, raw_path), encoding="utf-8")

    return "\n".join(
        [
            f"Generated game request: {description}",
            f"script: {script_path}",
            f"raw capture: {raw_path}",
            f"analysis: {report_path}",
            "",
            report_path.read_text(encoding="utf-8", errors="replace"),
        ]
    )


def extract_path_after(text, markers, default="."):
    lowered = text.lower()
    for marker in markers:
        index = lowered.find(marker)
        if index != -1:
            value = text[index + len(marker):].strip()
            return value.strip("\"'` ") or default
    parts = shlex.split(text)
    for part in parts:
        if part.startswith("/") or part.startswith("."):
            return part
    return default


def handle_tool_request(user_text, llm_service):
    lowered = user_text.lower()

    if lowered.startswith("/game "):
        return "game", generate_game(user_text[6:], llm_service)

    for marker in ("crea un gioco ", "create a game ", "generate a game "):
        if lowered.startswith(marker):
            return "game", generate_game(user_text[len(marker):], llm_service)

    if lowered.startswith("/sh ") or lowered.startswith("!"):
        command = user_text[4:] if lowered.startswith("/sh ") else user_text[1:]
        return "shell", run_shell_command(command)

    for marker in ("esegui ", "esegui il comando ", "run ", "run command ", "execute "):
        if lowered.startswith(marker):
            return "shell", run_shell_command(user_text[len(marker):])

    if lowered in {"pwd", "where am i", "dove sono"} or "directory corrente" in lowered:
        return "pwd", str(Path.cwd())

    if lowered.startswith("/ls") or "lista dei file" in lowered or "list files" in lowered or "file nel disco" in lowered:
        path = "."
        if lowered.startswith("/ls"):
            path = user_text[3:].strip() or "."
        else:
            path = extract_path_after(user_text, [" in ", " dentro ", " di ", " at "], ".")
        return "list_files", list_files(path)

    if lowered.startswith("/cat") or lowered.startswith("/read") or "leggi il file" in lowered or "read file" in lowered:
        path = user_text.split(maxsplit=1)[1] if lowered.startswith(("/cat", "/read")) and len(user_text.split(maxsplit=1)) > 1 else ""
        if not path:
            path = extract_path_after(user_text, ["file", "path"], "")
        return "read_text_file", read_text_file(path)

    if lowered.startswith("/find") or "cerca file" in lowered or "find file" in lowered:
        term = user_text.split(maxsplit=1)[1] if lowered.startswith("/find") and len(user_text.split(maxsplit=1)) > 1 else user_text
        return "find_files", find_files(term)

    return None, None


def print_help():
    print("BitNet tool agent")
    print("BitNet runs as a health-checked persistent background service.")
    print("Ask normally, or use local tools:")
    print("  /ls [path]       list files")
    print("  /cat <path>      read a text file")
    print("  /find <name>     find files by name")
    print("  /sh <command>    run a shell command inside the container")
    print("  !<command>       shortcut for /sh")
    print("  /game <name>     generate, save, and analyze a POSIX sh game")
    print("  pwd              show current directory")
    print("  /exit            quit")


def main():
    os.chdir(BITNET_DIR)
    llm_service = BitNetService()
    try:
        llm_service.start()
        print_help()
        while True:
            try:
                user_text = input("\nYou> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return 0

            if not user_text:
                continue
            if user_text.lower() in {"/exit", "exit", "quit", "/quit"}:
                return 0
            if user_text.lower() in {"/help", "help"}:
                print_help()
                continue

            tool_name, tool_result = handle_tool_request(user_text, llm_service)
            if tool_name:
                print(f"\n[tool:{tool_name}]")
                print(tool_result)
                continue

            print("\nBitNet>")
            print(llm_service.ask(user_text))
    finally:
        llm_service.close()


if __name__ == "__main__":
    sys.exit(main())
