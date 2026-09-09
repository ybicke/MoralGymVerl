#!/bin/bash
# =============================================================================
# Host-memory sampler for verl training jobs.
#
# Runs OUTSIDE the container on the batch node and prints one line per
# interval with the job cgroup's current/peak host-RAM use. Exists because
# `sacct MaxRSS` only sees the srun wrapper (reported 36 MB for a job that
# was OOM-killed at ~460 GB, 2026-08-25) — so host-memory sizing for large
# models was guesswork until this landed.
#
# Reads the JOB-level cgroup (the ancestor named job_<id>) rather than the
# batch step's own, so the srun step's workers are included. Falls back to
# /proc/meminfo node-wide figures if the cgroup files are unreadable; the
# node is exclusive to the job, so that is a close upper bound either way.
#
#   bash scripts/slurm/mem_sampler.sh [interval_seconds]   # default 10
# =============================================================================
INTERVAL="${1:-10}"

# Walk up from our own cgroup to the job-level one.
CG_DIR="/sys/fs/cgroup$(awk -F: '$1=="0"{print $3}' /proc/self/cgroup 2>/dev/null | head -1)"
while [ -n "${CG_DIR}" ] && [ "${CG_DIR}" != "/sys/fs/cgroup" ]; do
    case "$(basename "${CG_DIR}")" in job_*) break ;; esac
    CG_DIR="$(dirname "${CG_DIR}")"
done
[ -r "${CG_DIR}/memory.current" ] || CG_DIR=""
echo "[mem] sampling every ${INTERVAL}s; cgroup=${CG_DIR:-<unavailable, using /proc/meminfo>}"

gb() { awk -v b="${1:-0}" 'BEGIN{printf "%.1f", b/1073741824}'; }
max_node=0
while true; do
    node=$(awk '/MemTotal/{t=$2} /MemAvailable/{a=$2} END{printf "%.1f", (t-a)/1048576}' /proc/meminfo)
    awk -v n="${node}" -v m="${max_node}" 'BEGIN{exit !(n>m)}' && max_node="${node}"
    if [ -n "${CG_DIR}" ]; then
        cur=$(gb "$(cat "${CG_DIR}/memory.current" 2>/dev/null)")
        peak=$(gb "$(cat "${CG_DIR}/memory.peak" 2>/dev/null)")
        echo "[mem] $(date +%H:%M:%S) cgroup_now=${cur}GB cgroup_peak=${peak}GB node_now=${node}GB node_peak=${max_node}GB"
    else
        echo "[mem] $(date +%H:%M:%S) node_now=${node}GB node_peak=${max_node}GB"
    fi
    sleep "${INTERVAL}"
done
