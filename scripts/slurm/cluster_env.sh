# Load cluster settings from scripts/slurm/cluster.env, if present.
# Usage (sourced): . scripts/slurm/cluster_env.sh <repo_root>
# The file holds plain KEY=VALUE lines (see cluster.env.example); every key
# is exported, and $USER, $HOME and $SCRATCH are expanded.
_moralgym_cluster_env="${1:?cluster_env.sh needs the repo root}/scripts/slurm/cluster.env"
if [ -f "${_moralgym_cluster_env}" ]; then
    set -a
    # shellcheck disable=SC1090
    . "${_moralgym_cluster_env}"
    set +a
fi
unset _moralgym_cluster_env
