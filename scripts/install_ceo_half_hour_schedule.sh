#!/bin/zsh
echo "deprecated: use install_ceo_hourly_schedule.sh" >&2
exec "${0:A:h}/install_ceo_hourly_schedule.sh" "$@"
