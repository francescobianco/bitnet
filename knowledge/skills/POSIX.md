# POSIX Shell Game Patterns

Goal: generate small terminal games that run with `sh`, not Bash.

Rules:
- Use `#!/bin/sh`.
- Do not use arrays, `[[ ... ]]`, `function name`, `$RANDOM`, `read -n`, `read -t`, `select`, `local`, process substitution, or brace expansion.
- Store lists as space-separated records, e.g. `body="10,5 9,5 8,5"`.
- Use `case`, `set --`, `IFS`, `expr`, `printf`, `stty`, `tput`, `trap`.
- Keep game state simple: `x`, `y`, `dx`, `dy`, `score`, `lives`, `tick`.
- Never hardcode the game board size. Read terminal size and clamp it.

Terminal setup:
```sh
oldstty=$(stty -g)
trap 'stty "$oldstty"; tput cnorm; clear; exit' INT TERM HUP EXIT
stty -echo raw min 0 time 1
tput civis
clear
```

Terminal size:
```sh
cols=$(tput cols 2>/dev/null || printf 80)
lines=$(tput lines 2>/dev/null || printf 24)
[ "$cols" -lt 30 ] && cols=30
[ "$lines" -lt 12 ] && lines=12
w=$((cols - 2))
h=$((lines - 4))
[ "$w" -gt 70 ] && w=70
[ "$h" -gt 24 ] && h=24
```

Always reserve lines for HUD and border:
```sh
hud_y=0
board_y=1
footer_y=$((h + 2))
```

Non-blocking key read:
```sh
key=$(dd bs=1 count=1 2>/dev/null)
case "$key" in
  h|a) dx=-1; dy=0 ;;
  l|d) dx=1; dy=0 ;;
  k|w) dx=0; dy=-1 ;;
  j|s) dx=0; dy=1 ;;
  q) exit ;;
esac
```

Draw at coordinate:
```sh
draw() { tput cup "$2" "$1"; printf '%s' "$3"; }
```

Clamp position:
```sh
[ "$x" -lt 1 ] && x=1
[ "$x" -gt "$maxx" ] && x=$maxx
[ "$y" -lt 1 ] && y=1
[ "$y" -gt "$maxy" ] && y=$maxy
```

Terminal resize handling:
```sh
resize() {
  cols=$(tput cols 2>/dev/null || printf 80)
  lines=$(tput lines 2>/dev/null || printf 24)
  [ "$cols" -lt 30 ] && cols=30
  [ "$lines" -lt 12 ] && lines=12
  w=$((cols - 2))
  h=$((lines - 4))
  [ "$w" -gt 70 ] && w=70
  [ "$h" -gt 24 ] && h=24
}
trap 'resize; clear' WINCH
```

List membership with `x,y` records:
```sh
contains_cell() {
  needle=$1
  for cell in $cells; do
    [ "$cell" = "$needle" ] && return 0
  done
  return 1
}
```

Render full frame when simple; it avoids stale pixels:
```sh
tput cup 0 0
printf '+--------------------+\n'
```

Use `awk` for simple random numbers:
```sh
rand_int() { awk -v max="$1" 'BEGIN{srand(); print int(rand()*max)+1}'; }
```

Portable timing: `stty time 1` makes `dd` wait about 0.1s. Avoid relying on `sleep 0.05`.

Minimal loop:
```sh
while :; do
  key=$(dd bs=1 count=1 2>/dev/null)
  handle_key "$key"
  update_state
  render
done
```
