# setup-rclone Action

This action installs and configures `rclone` for Google Drive usage in GitHub Actions.

## Usage

```yaml
- name: Setup rclone
  uses: firstsun-dev/.github/actions/setup-rclone@main
  with:
    gdrive_client_id: ${{ secrets.GDRIVE_CLIENT_ID }}
    gdrive_client_secret: ${{ secrets.GDRIVE_CLIENT_SECRET }}
    gdrive_token: ${{ secrets.GDRIVE_TOKEN }}
    gdrive_folder_id: ${{ secrets.GDRIVE_FOLDER_ID }} # Optional
    remote_name: 'gdrive' # Optional, default is 'gdrive'
```

## Inputs

| Name | Description | Required | Default |
|------|-------------|----------|---------|
| `gdrive_client_id` | Google Drive Client ID | Yes | - |
| `gdrive_client_secret` | Google Drive Client Secret | Yes | - |
| `gdrive_token` | Google Drive Token | Yes | - |
| `gdrive_folder_id` | Google Drive Root Folder ID | No | - |
| `remote_name` | Rclone remote name | No | `gdrive` |

## Supported Platforms

- Linux (Ubuntu)
- macOS
