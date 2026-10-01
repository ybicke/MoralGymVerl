#!/bin/bash
# Print the commits a job runs on: code_version.sh <moralgym_dir> <sdpo_dir>
# "-dirty" marks uncommitted or untracked files.
rev() {
    local r
    r=$(git -C "$1" rev-parse --short HEAD 2>/dev/null) || r=unknown
    [ -n "$(git -C "$1" status --porcelain 2>/dev/null)" ] && r="${r}-dirty"
    echo "${r}"
}
echo "moralgym_verl=$(rev "$1") sdpo=$(rev "$2")"
