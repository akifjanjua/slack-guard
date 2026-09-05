# Bot Token Scopes

Add these under your Slack app's **OAuth & Permissions** → **Bot Token Scopes**, then **Install to Workspace** (or **Reinstall to Workspace** if the app is already installed — Slack requires a reinstall for a newly added scope to take effect, not just Save).

| Scope | Needed for |
|---|---|
| `team:read` | `get_team_info` |
| `users:read`, `users:read.email` | `list_users`, `get_user_info` |
| `channels:read`, `groups:read` | `list_channels`, `get_channel_info`, `list_channel_members` |
| `channels:history`, `groups:history` | `get_channel_history`, `get_thread_replies` |
| `usergroups:read` | `list_usergroups` |
| `files:read` | `list_files` |
| `dnd:read` | `get_dnd_status` |
| `chat:write` | `post_message`, `post_ephemeral`, `schedule_message`, `cancel_scheduled_message` |
| `reactions:write` | `add_reaction`, `remove_reaction` |
| `pins:write` | `add_pin`, `remove_pin` |
| `bookmarks:write` | `add_bookmark`, `edit_bookmark`, `remove_bookmark` |
| `reminders:write` | `add_reminder`, `complete_reminder` |
| `channels:manage`, `groups:write` | `set_channel_topic`, `set_channel_purpose`, `create_channel`, `rename_channel`, `archive_channel`, `unarchive_channel`, `kick_user_from_channel`, `invite_to_channel` |
| `channels:join` | `join_channel` |
| `files:write` | `delete_file`, `share_file_publicly` |
| `usergroups:write` | `update_usergroup_members` |

`chat:write` (already listed above) also covers `delete_message` (`chat.delete`) — note that a bot token can only delete messages it posted itself; deleting another user's message needs an admin-level scope this module doesn't request. `uninstall_app` and `revoke_token` need no Bot Token Scope at all — `uninstall_app` instead needs `SLACK_CLIENT_ID`/`SLACK_CLIENT_SECRET` configured on the same Integrations card (from your Slack app's Basic Information page).

Only add the scopes for commands you actually plan to use — an unused scope is unused blast radius. A command that fails with `Slack rejected the request: missing_scope` names the specific missing permission; check it against this table rather than guessing.
