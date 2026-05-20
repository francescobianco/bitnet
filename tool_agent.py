#!/usr/bin/env python3
import os
import pty
import re
import select
import shlex
import signal
import subprocess
import sys
import time
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


def strip_ansi(text):
    return re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text)


class BitNetService:
    def __init__(self):
        self.master_fd = None
        self.process = None

    def start(self):
        if self.process and self.process.poll() is None:
            return

        print("[llm] starting persistent BitNet service...", flush=True)
        master_fd, slave_fd = pty.openpty()
        command = [
            "python",
            "run_inference.py",
            "-m",
            MODEL_PATH,
            "-n",
            N_PREDICT,
            "-p",
            build_system_prompt(),
            "-cnv",
        ]
        self.process = subprocess.Popen(
            command,
            cwd=BITNET_DIR,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True,
            start_new_session=True,
        )
        os.close(slave_fd)
        self.master_fd = master_fd
        os.set_blocking(self.master_fd, False)
        self._read_until_prompt(LLM_START_TIMEOUT_SECONDS)
        print("[llm] ready", flush=True)

    def ask(self, prompt):
        self.start()
        os.write(self.master_fd, f"{prompt.rstrip()}\n".encode("utf-8"))
        raw = self._read_until_prompt(LLM_RESPONSE_TIMEOUT_SECONDS)
        return self._clean_response(raw, prompt)

    def close(self):
        if self.master_fd is not None:
            try:
                os.close(self.master_fd)
            except OSError:
                pass
            self.master_fd = None

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

    def _read_until_prompt(self, timeout):
        deadline = time.monotonic() + timeout
        chunks = []

        while time.monotonic() < deadline:
            if self.process and self.process.poll() is not None:
                output = "".join(chunks)
                raise RuntimeError(f"BitNet service exited with code {self.process.returncode}.\n{output}")

            readable, _, _ = select.select([self.master_fd], [], [], 0.2)
            if not readable:
                continue

            try:
                data = os.read(self.master_fd, 4096)
            except BlockingIOError:
                continue
            except OSError:
                break

            if not data:
                break

            chunks.append(data.decode("utf-8", errors="replace"))
            text = "".join(chunks).replace("\r\n", "\n").replace("\r", "\n")
            if text.endswith("\n> ") or re.search(r"(?m)^> $", text):
                return text

        return "".join(chunks)

    def _clean_response(self, raw, prompt):
        text = strip_ansi(raw).replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"\n?>\s*$", "", text).strip()

        if prompt in text:
            text = text.split(prompt, 1)[1].strip()

        noisy_prefixes = (
            "llama_",
            "sampler ",
            "generate:",
            "system_info:",
            "main:",
        )
        lines = [line for line in text.splitlines() if not line.strip().startswith(noisy_prefixes)]
        return "\n".join(lines).strip()


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
    print("BitNet runs as a persistent spawned service on the first LLM request.")
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
    print_help()
    try:
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
