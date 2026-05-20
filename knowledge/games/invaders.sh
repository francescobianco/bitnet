#!/bin/sh
oldstty=$(stty -g)
trap 'stty "$oldstty"; tput cnorm; clear; exit' INT TERM HUP EXIT
stty -echo raw min 0 time 1
tput civis

resize() {
  cols=$(tput cols 2>/dev/null || printf 80)
  lines=$(tput lines 2>/dev/null || printf 24)
  [ "$cols" -lt 34 ] && cols=34
  [ "$lines" -lt 14 ] && lines=14
  w=$((cols - 2))
  h=$((lines - 4))
  [ "$w" -gt 64 ] && w=64
  [ "$h" -gt 24 ] && h=24
}
trap 'resize; clear' WINCH

resize
ship=$((w / 2))
shot=
aliens="$((w / 6)),2 $((w / 3)),2 $((w / 2)),2 $((w - w / 3)),2 $((w - w / 6)),2 $((w / 4)),4 $((w / 2)),4 $((w - w / 4)),4"
adir=1
score=0
tick=0

draw() { tput cup "$2" "$1"; printf '%s' "$3"; }

has_alien() {
  needle=$1
  for alien in $aliens; do
    [ "$alien" = "$needle" ] && return 0
  done
  return 1
}

remove_alien() {
  dead=$1
  next=
  for alien in $aliens; do
    [ "$alien" = "$dead" ] || next="$next $alien"
  done
  aliens=$next
}

move_aliens() {
  edge=0
  for alien in $aliens; do
    x=${alien%,*}
    nx=$((x + adir))
    [ "$nx" -le 1 ] && edge=1
    [ "$nx" -ge "$w" ] && edge=1
  done

  next=
  if [ "$edge" -eq 1 ]; then
    adir=$((0 - adir))
    for alien in $aliens; do
      x=${alien%,*}
      y=${alien#*,}
      y=$((y + 1))
      next="$next $x,$y"
    done
  else
    for alien in $aliens; do
      x=${alien%,*}
      y=${alien#*,}
      x=$((x + adir))
      next="$next $x,$y"
    done
  fi
  aliens=$next
}

move_shot() {
  [ -z "$shot" ] && return
  sx=${shot%,*}
  sy=${shot#*,}
  sy=$((sy - 1))
  if [ "$sy" -lt 1 ]; then
    shot=
    return
  fi
  if has_alien "$sx,$sy"; then
    remove_alien "$sx,$sy"
    score=$((score + 10))
    shot=
    return
  fi
  shot="$sx,$sy"
}

render() {
  tput cup 0 0
  printf 'INVADERS  h/l move, space fire, q quit  score:%s  size:%sx%s\n' "$score" "$w" "$h"
  y=1
  while [ "$y" -le "$h" ]; do
    printf '|'
    x=1
    while [ "$x" -le "$w" ]; do
      cell="$x,$y"
      if [ "$cell" = "$shot" ]; then
        printf '|'
      elif has_alien "$cell"; then
        printf 'W'
      elif [ "$y" -eq "$h" ] && [ "$x" -eq "$ship" ]; then
        printf 'A'
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
    h|a) ship=$((ship - 1)) ;;
    l|d) ship=$((ship + 1)) ;;
    ' ') [ -z "$shot" ] && shot="$ship,$((h - 1))" ;;
    q) exit ;;
  esac
  [ "$ship" -lt 1 ] && ship=1
  [ "$ship" -gt "$w" ] && ship=$w

  tick=$((tick + 1))
  move_shot
  [ $((tick % 5)) -eq 0 ] && move_aliens

  for alien in $aliens; do
    ay=${alien#*,}
    if [ "$ay" -ge "$h" ]; then
      render
      draw 0 $((h + 3)) "Game over. Score: $score"
      dd bs=1 count=1 2>/dev/null >/dev/null
      exit
    fi
  done

  if [ -z "$aliens" ]; then
    render
    draw 0 $((h + 3)) "You win. Score: $score"
    dd bs=1 count=1 2>/dev/null >/dev/null
    exit
  fi

  render
done
