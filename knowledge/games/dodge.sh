#!/bin/sh
oldstty=$(stty -g)
trap 'stty "$oldstty"; tput cnorm; clear; exit' INT TERM HUP EXIT
stty -echo raw min 0 time 1
tput civis

resize() {
  cols=$(tput cols 2>/dev/null || printf 80)
  lines=$(tput lines 2>/dev/null || printf 24)
  [ "$cols" -lt 30 ] && cols=30
  [ "$lines" -lt 12 ] && lines=12
  w=$((cols - 2))
  h=$((lines - 4))
  [ "$w" -gt 60 ] && w=60
  [ "$h" -gt 22 ] && h=22
}
trap 'resize; clear' WINCH

resize
px=$((w / 2))
rocks="$((w / 6)),2 $((w / 2)),5 $((w - w / 6)),8"
score=0
tick=0

draw() { tput cup "$2" "$1"; printf '%s' "$3"; }

rand_x() {
  awk -v max="$w" 'BEGIN{srand(); print int(rand()*max)+1}'
}

has_rock() {
  needle=$1
  for rock in $rocks; do
    [ "$rock" = "$needle" ] && return 0
  done
  return 1
}

move_rocks() {
  next=
  for rock in $rocks; do
    x=${rock%,*}
    y=${rock#*,}
    y=$((y + 1))
    if [ "$y" -gt "$h" ]; then
      y=1
      x=$(rand_x)
      score=$((score + 1))
    fi
    next="$next $x,$y"
  done
  rocks=$next
}

render() {
  tput cup 0 0
  printf 'DODGE  h/l or a/d move, q quit  score:%s  size:%sx%s\n' "$score" "$w" "$h"
  y=1
  while [ "$y" -le "$h" ]; do
    printf '|'
    x=1
    while [ "$x" -le "$w" ]; do
      if [ "$y" -eq "$h" ] && [ "$x" -eq "$px" ]; then
        printf 'A'
      elif has_rock "$x,$y"; then
        printf '*'
      else
        printf ' '
      fi
      x=$((x + 1))
    done
    printf '|\n'
    y=$((y + 1))
  done
  printf '+'
  x=1
  while [ "$x" -le "$w" ]; do printf '-'; x=$((x + 1)); done
  printf '+\n'
}

clear
while :; do
  key=$(dd bs=1 count=1 2>/dev/null)
  case "$key" in
    h|a) px=$((px - 1)) ;;
    l|d) px=$((px + 1)) ;;
    q) exit ;;
  esac
  [ "$px" -lt 1 ] && px=1
  [ "$px" -gt "$w" ] && px=$w

  tick=$((tick + 1))
  [ $((tick % 2)) -eq 0 ] && move_rocks

  if has_rock "$px,$h"; then
    render
    draw 0 $((h + 3)) "Game over. Score: $score"
    dd bs=1 count=1 2>/dev/null >/dev/null
    exit
  fi
  render
done
