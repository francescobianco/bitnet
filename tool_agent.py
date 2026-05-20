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
LLM_IDLE_SECONDS = float(os.environ.get("LLM_IDLE_SECONDS", "1.5"))
MAX_REFERENCE_GAME_BYTES = 2500
MAX_REFERENCE_GAMES = 2

SYSTEM_PROMPT = """You are BitNet running inside a Dockerized POSIX shell training image.
Answer normal user requests directly.
When asked to create shell programs, prefer strictly POSIX sh and terminal-size-aware code.
The wrapper around you can execute local tools such as ls, cat, find, and shell commands.
If a user asks for filesystem inspection or command execution, keep your answer brief because the wrapper will run the tool.
"""

# Deterministic mapping from analysis finding labels to skill snippets.
# Only entries whose text is not already present in POSIX.md are auto-added.
FINDING_SKILL_HINTS = {
    "function keyword": (
        "### Function syntax\n"
        "- `function name() {}` is Bash only. Use `name() { ... }` in POSIX sh.\n"
    ),
    "local keyword": (
        "### No local variables\n"
        "- `local var=value` is not POSIX sh. Use distinct global names or `unset` after use.\n"
    ),
    "process substitution": (
        "### Process substitution\n"
        "- `<(...)` is Bash only. Use a temp file or pipeline instead.\n"
    ),
    "brace expansion": (
        "### Brace expansion\n"
        "- `{1..10}` is Bash only. Use `seq 1 10` or a `while` counter.\n"
    ),
    "posix syntax check failed": (
        "### Syntax errors\n"
        "- Verify scripts with `sh -n` before shipping.\n"
        "- Common causes: unclosed quotes, missing `fi`/`done`/`esac`, stray Bash syntax.\n"
    ),
    "bash shebang": (
        "### Shebang\n"
        "- Always use `#!/bin/sh`. Never use `#!/bin/bash` in a POSIX sh game.\n"
    ),
    "bash random": (
        "### Random numbers\n"
        "- `$RANDOM` is Bash only.\n"
        "- Use `awk -v max=\"$n\" 'BEGIN{srand(); print int(rand()*max)+1}'`.\n"
    ),
    "read -n/-t": (
        "### Non-blocking input\n"
        "- `read -n` and `read -t` are Bash only.\n"
        "- Use `stty raw min 0 time 1` then `key=$(dd bs=1 count=1 2>/dev/null)`.\n"
    ),
    "bash arrays": (
        "### No arrays\n"
        "- Arrays `arr=(...)` are Bash only.\n"
        "- Store lists as space-separated `x,y` records in a plain variable.\n"
    ),
    "double-bracket test": (
        "### Conditional test\n"
        "- `[[ ... ]]` is Bash only. Use `[ ... ]` or `case` for conditions.\n"
    ),
}

_GAME_PROMPT_PREFIX = """\
Create one complete terminal game in strictly POSIX sh.
Mandatory rules:
- Shebang: #!/bin/sh
- No Bash: no arrays arr=(), no [[ ]], no read -n, no read -t, \
no $RANDOM, no function keyword, no local, no <(...), no {1..N}
- Terminal size: read with tput cols/lines, clamp, never hardcode
- Setup: oldstty=$(stty -g); trap 'stty "$oldstty"; tput cnorm; clear; exit' INT TERM HUP EXIT; \
stty -echo raw min 0 time 1; tput civis
- Input: key=$(dd bs=1 count=1 2>/dev/null)
- Lists: space-separated x,y records, not arrays
- Random: awk -v max="$n" 'BEGIN{srand(); print int(rand()*max)+1}'
Only output the shell script in one ```sh code block.
Game request: """


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


def load_reference_games():
    games_dir = KNOWLEDGE_DIR / "games"
    if not games_dir.exists():
        return ""
    chunks = []
    for game_file in sorted(games_dir.glob("*.sh"))[:MAX_REFERENCE_GAMES]:
        content = game_file.read_text(encoding="utf-8", errors="replace")[:MAX_REFERENCE_GAME_BYTES]
        chunks.append(f"Reference game ({game_file.name}):\n```sh\n{content}\n```")
    if not chunks:
        return ""
    return "\nWorking reference games (use as style guidance):\n" + "\n\n".join(chunks)


def build_system_prompt():
    skill = read_posix_skill()
    reference = load_reference_games()
    parts = [SYSTEM_PROMPT]
    if skill:
        parts.append(f"Compact POSIX shell knowledge loaded once at startup:\n{skill}")
    if reference:
        parts.append(reference)
    return "\n".join(parts)


def add_skill_entry(text):
    skill_path = KNOWLEDGE_DIR / "skills" / "POSIX.md"
    existing = skill_path.read_text(encoding="utf-8", errors="replace") if skill_path.exists() else ""
    normalized = text.strip()
    if normalized in existing:
        return f"skill already present in {skill_path}"
    skill_path.parent.mkdir(parents=True, exist_ok=True)
    with open(skill_path, "a", encoding="utf-8") as f:
        f.write(f"\n{normalized}\n")
    return f"skill added to {skill_path}"


def suggest_new_skills(findings):
    """Return (key, hint) pairs for findings not yet covered by POSIX.md."""
    skill_path = KNOWLEDGE_DIR / "skills" / "POSIX.md"
    existing = skill_path.read_text(encoding="utf-8", errors="replace") if skill_path.exists() else ""
    new_skills = []
    seen_keys = set()
    for finding in findings:
        label = finding.lstrip("- ").split(":")[0].strip().lower()
        for key, hint in FINDING_SKILL_HINTS.items():
            if key in label and key not in seen_keys and hint.strip() not in existing:
                new_skills.append((key, hint))
                seen_keys.add(key)
                break
    return new_skills


class BitNetService:
    def __init__(self):
        self.process = None
        self.master_fd = None

    def start(self):
        if self.process and self.process.poll() is None:
            return

        print("[llm] starting persistent run_inference.py process...", flush=True)
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
        self._wait_for_interactive_prompt()
        self._poke()
        print("[llm] ready after run_inference.py startup poke", flush=True)

    def ask(self, prompt, ensure_started=True, stream=False):
        if ensure_started:
            self.start()
        os.write(self.master_fd, f"{prompt.rstrip()}\n".encode("utf-8"))
        raw = self._read_until_idle(LLM_RESPONSE_TIMEOUT_SECONDS, stream=stream)
        return self._clean_response(raw, prompt)

    def _poke(self):
        content = self.ask("Reply with exactly READY.", ensure_started=False)
        if not content.strip():
            raise RuntimeError("BitNet run_inference.py returned an empty startup poke")

    def _wait_for_interactive_prompt(self):
        output = self._read_until_prompt(LLM_START_TIMEOUT_SECONDS)
        if not output:
            raise RuntimeError("BitNet run_inference.py did not produce startup output")

    def _read_until_prompt(self, timeout):
        deadline = time.monotonic() + timeout
        chunks = []
        while time.monotonic() < deadline:
            self._raise_if_exited(chunks)
            data = self._read_available(0.2)
            if not data:
                continue
            chunks.append(data)
            text = self._normalize("".join(chunks))
            if text.endswith("\n> ") or re.search(r"(?m)^> $", text):
                return text
        raise RuntimeError("Timed out waiting for run_inference.py interactive prompt")

    def _read_until_idle(self, timeout, stream=False):
        deadline = time.monotonic() + timeout
        idle_deadline = None
        chunks = []
        stream_buf = ""
        _noisy = (
            "warning:", "build:", "main:", "llama_", "llm_", "common_",
            "sampler ", "generate:", "system_info:", "== Running in interactive mode.",
            " - Press ", "System:",
        )
        while time.monotonic() < deadline:
            self._raise_if_exited(chunks)
            data = self._read_available(0.2)
            if data:
                chunks.append(data)
                idle_deadline = time.monotonic() + LLM_IDLE_SECONDS
                if stream:
                    clean = self._normalize(data)
                    clean = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", clean)
                    stream_buf += clean
                    while "\n" in stream_buf:
                        line, stream_buf = stream_buf.split("\n", 1)
                        s = line.strip()
                        if not s or s in (">", "> "):
                            continue
                        if any(s.startswith(p) for p in _noisy):
                            continue
                        sys.stdout.write(line + "\n")
                        sys.stdout.flush()
                continue
            if idle_deadline and time.monotonic() >= idle_deadline:
                if stream and stream_buf.strip():
                    s = stream_buf.strip()
                    if s not in (">", "> ") and not any(s.startswith(p) for p in _noisy):
                        sys.stdout.write(s + "\n")
                        sys.stdout.flush()
                return "".join(chunks)
        return "".join(chunks)

    def _read_available(self, timeout):
        readable, _, _ = select.select([self.master_fd], [], [], timeout)
        if not readable:
            return ""
        try:
            data = os.read(self.master_fd, 4096)
        except BlockingIOError:
            return ""
        if not data:
            return ""
        return data.decode("utf-8", errors="replace")

    def _raise_if_exited(self, chunks):
        if self.process and self.process.poll() is not None:
            output = self._normalize("".join(chunks))
            raise RuntimeError(f"BitNet run_inference.py exited with code {self.process.returncode}.\n{output}")

    def _clean_response(self, raw, prompt):
        text = self._normalize(raw)
        text = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text)
        text = re.sub(r"(?m)^>\s?", "", text)
        text = text.replace(prompt, "", 1).strip()
        noisy_prefixes = (
            "warning:",
            "build:",
            "main:",
            "llama_",
            "llm_",
            "common_",
            "sampler ",
            "generate:",
            "system_info:",
            "== Running in interactive mode.",
            " - Press ",
            "System:",
        )
        lines = [line for line in text.splitlines() if not line.strip().startswith(noisy_prefixes)]
        return "\n".join(lines).strip()

    def _normalize(self, text):
        return text.replace("\r\n", "\n").replace("\r", "\n")

    def restart(self):
        self.close()
        self.start()

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
    """Return (findings_list, report_text)."""
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
    return findings, "\n".join(report) + "\n"


def generate_game(request, llm_service):
    parts = request.strip().split(maxsplit=1)
    name = slugify_name(parts[0] if parts else "game")
    description = parts[1] if len(parts) > 1 else name

    generated_dir = KNOWLEDGE_DIR / "generated"
    reports_dir = KNOWLEDGE_DIR / "reports"
    generated_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)

    prompt = _GAME_PROMPT_PREFIX + description + "\n"

    print("[game] generating first pass...", flush=True)
    raw = llm_service.ask(prompt)
    raw_path = generated_dir / f"{name}.raw.txt"
    script_path = generated_dir / f"{name}.sh"
    report_path = reports_dir / f"{name}.md"

    raw_path.write_text(raw + "\n", encoding="utf-8")
    script = extract_shell_script(raw)
    script_path.write_text(script, encoding="utf-8")
    script_path.chmod(0o755)

    findings, report_text = analyze_shell_script(script_path, raw_path)

    # Self-correction pass when portability issues remain
    if findings:
        findings_summary = "\n".join(findings)
        fix_prompt = (
            f"This POSIX sh game script has portability issues:\n{findings_summary}\n\n"
            f"Fix all issues. Output only the corrected script in one ```sh code block.\n"
            f"Script:\n```sh\n{script[:4000]}\n```\n"
        )
        print(f"[game] {len(findings)} issue(s) found, running correction pass...", flush=True)
        raw2 = llm_service.ask(fix_prompt)
        script2 = extract_shell_script(raw2)
        if script2 and script2.strip() != script.strip():
            script_path.write_text(script2, encoding="utf-8")
            script_path.chmod(0o755)
            findings, report_text = analyze_shell_script(script_path, raw_path)
            print("[game] correction pass complete", flush=True)

    report_path.write_text(report_text, encoding="utf-8")

    # Auto-add skill snippets for findings not already in POSIX.md, then reload service
    new_skills = suggest_new_skills(findings)
    skill_msgs = []
    for _key, hint in new_skills:
        msg = add_skill_entry(hint)
        skill_msgs.append(msg)

    if skill_msgs:
        print(f"[skill] {len(skill_msgs)} new skill(s) added — reloading BitNet service...", flush=True)
        llm_service.restart()
        print("[skill] service reloaded with updated knowledge", flush=True)

    output_parts = [
        f"Generated game request: {description}",
        f"script: {script_path}",
        f"raw capture: {raw_path}",
        f"analysis: {report_path}",
        "",
        report_text,
    ]
    if skill_msgs:
        output_parts.append("Skills auto-added and service reloaded:")
        output_parts.extend(f"  {m}" for m in skill_msgs)

    return "\n".join(output_parts)


def learn_from_report(game_name, llm_service):
    reports_dir = KNOWLEDGE_DIR / "reports"
    if game_name:
        slug = slugify_name(game_name)
        report_path = reports_dir / f"{slug}.md"
    else:
        reports = sorted(reports_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True) if reports_dir.exists() else []
        if not reports:
            return "No reports found in knowledge/reports/."
        report_path = reports[0]

    if not report_path.exists():
        return f"Report not found: {report_path}"

    report_text = report_path.read_text(encoding="utf-8", errors="replace")
    findings = re.findall(r"^- .+$", report_text, flags=re.MULTILINE)
    blockers = [f for f in findings if "found" in f or "failed" in f]

    if not blockers:
        return f"No portability issues in {report_path.name}. Nothing to learn."

    new_skills = suggest_new_skills(blockers)
    if not new_skills:
        return f"All issues from {report_path.name} are already covered in POSIX.md."

    skill_msgs = []
    for _key, hint in new_skills:
        msg = add_skill_entry(hint)
        skill_msgs.append(f"{hint.strip()}\n  → {msg}")

    if any("added" in m for m in skill_msgs):
        llm_service.restart()
        skill_msgs.append("Service reloaded with updated knowledge.")

    return f"Learned from {report_path.name}:\n\n" + "\n\n".join(skill_msgs)


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

    if lowered.startswith("/skill "):
        text = user_text[7:].strip()
        if not text:
            return "skill", "Usage: /skill <text to add to POSIX.md>"
        msg = add_skill_entry(text)
        if "added" in msg:
            llm_service.restart()
            msg += "\nService reloaded with updated knowledge."
        return "skill", msg

    if lowered.startswith("/learn"):
        parts = user_text.split(maxsplit=1)
        game_name = parts[1].strip() if len(parts) > 1 else None
        return "learn", learn_from_report(game_name, llm_service)

    if lowered in {"/restart", "restart agent", "riavvia agente"}:
        llm_service.restart()
        return "restart", "BitNet service restarted with current knowledge."

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
    print("BitNet runs as a persistent spawned run_inference.py process.")
    print("Ask normally, or use local tools:")
    print("  /ls [path]       list files")
    print("  /cat <path>      read a text file")
    print("  /find <name>     find files by name")
    print("  /sh <command>    run a shell command inside the container")
    print("  !<command>       shortcut for /sh")
    print("  /game <name> [description]")
    print("                   generate, save, analyze, and auto-improve a POSIX sh game")
    print("  /skill <text>    append a skill entry to knowledge/skills/POSIX.md and reload")
    print("  /learn [game]    extract skill lessons from a report and reload")
    print("  /restart         reload BitNet service with current knowledge")
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

            sys.stdout.write("\nBitNet>\n")
            sys.stdout.flush()
            llm_service.ask(user_text, stream=True)
    finally:
        llm_service.close()


if __name__ == "__main__":
    sys.exit(main())