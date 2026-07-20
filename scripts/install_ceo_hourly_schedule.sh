#!/bin/zsh
echo "install_ceo_hourly_schedule.sh is retained as a compatibility alias; installing the 1800s schedule" >&2
exec "${0:A:h}/install_ceo_half_hour_schedule.sh" "$@"
