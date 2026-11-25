# bash completion for policy_check.py: flags, then the project folders of the script's repo. Load: source this file (e.g. from ~/.bashrc).
_policy_check() {
  local cur=${COMP_WORDS[COMP_CWORD]} prev=${COMP_WORDS[COMP_CWORD-1]} script root f names=
  case $prev in --root|--exceptions) mapfile -t COMPREPLY < <(compgen -f -- "$cur"); return ;; esac
  if [[ $cur == -* ]]; then mapfile -t COMPREPLY < <(compgen -W '--root --exceptions --help' -- "$cur"); return; fi
  script=$(readlink -f "$(command -v "${COMP_WORDS[0]}" 2>/dev/null || echo "${COMP_WORDS[0]}")")
  root=$(dirname "$script")/../..
  for f in "$root"/*/docker-compose.yaml; do f=${f%/*}; names+=" ${f##*/}"; done
  mapfile -t COMPREPLY < <(compgen -W "$names" -- "$cur")
}
complete -F _policy_check policy_check.py
