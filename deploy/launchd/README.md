# Local LaunchAgents

LaunchAgent files contain absolute executable and working-directory paths, so
portable static plist files are intentionally not checked in. Generate them
with `deploy/install_local_scheduler.sh`; that installer delegates XML creation
to the `plistlib`-based `scripts/install_launch_agent.py`.

Use `--render-only --output-dir DIR` to inspect generated files without calling
`launchctl`. The frontend agent is omitted unless `--with-ui` is supplied.
