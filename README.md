# WatchDog Cloud

WatchDog Cloud is a GitHub Actions based web page monitoring system.

It checks web pages every 30 minutes, even when your PC is turned off.

## What It Does

- Runs on GitHub Actions every 30 minutes
- Monitors multiple URLs from `monitors.yaml`
- Saves the latest HTML snapshot
- Compares the current HTML hash with the previous hash
- Sends a Telegram alert when HTML changes
- Sends a Telegram alert when a keyword appears for the first time
- Prevents duplicate keyword alerts
- Uses only Python, `requests`, and `beautifulsoup4`
- Does not use Playwright

## Project Structure

```text
WatchDog Cloud/
├─ monitor.py
├─ monitors.yaml
├─ requirements.txt
├─ README.md
├─ data/
│  ├─ hashes.json
│  ├─ keyword_alerts.json
│  └─ html/
└─ .github/
   └─ workflows/
      └─ monitor.yml
```

The `data` folder is created automatically after the first run.

## Step 1. Create a GitHub Repository

1. Go to [GitHub](https://github.com).
2. Click **New repository**.
3. Enter a repository name, for example:

   ```text
   watchdog-cloud
   ```

4. Create the repository.
5. Upload all files from this project to the repository.

Important: `monitor.py`, `monitors.yaml`, and `requirements.txt` must be in the repository root.

## Step 2. Create a Telegram Bot

1. Open Telegram.
2. Search for `BotFather`.
3. Send:

   ```text
   /newbot
   ```

4. Follow the instructions.
5. Copy the bot token.

The token looks like this:

```text
1234567890:ABCDEF_your_token_here
```

## Step 3. Find Your Telegram Chat ID

One simple way:

1. Send a message to your new bot.
2. Open this URL in your browser:

   ```text
   https://api.telegram.org/botYOUR_BOT_TOKEN/getUpdates
   ```

3. Find `chat`.
4. Copy the `id` value.

## Step 4. Add GitHub Secrets

In your GitHub repository:

1. Go to **Settings**.
2. Go to **Secrets and variables**.
3. Click **Actions**.
4. Click **New repository secret**.
5. Add this secret:

   ```text
   TELEGRAM_BOT_TOKEN
   ```

6. Paste your Telegram bot token.
7. Add another secret:

   ```text
   TELEGRAM_CHAT_ID
   ```

8. Paste your Telegram chat ID.

## Step 5. Enable GitHub Actions

1. Go to the **Actions** tab in your repository.
2. If GitHub asks you to enable workflows, click **I understand my workflows, go ahead and enable them**.
3. Open **WatchDog Cloud Monitor**.
4. Click **Run workflow** to test it manually.

After that, it will run every 30 minutes automatically.

## Step 6. Add or Edit Monitors

Open `monitors.yaml`.

Example:

```yaml
- name: KSASF
  url: https://ksasf.ksa.hs.kr/?action=BD0000M&pagecode=P000000023&language=KR
  keywords:
    - 본선 진출팀
    - 2026 연구발표 본선 진출팀 발표

- name: Example
  url: https://example.com/
  keywords:
    - 발표
```

Rules:

- `name` is the monitor name shown in Telegram.
- `url` is the page to monitor.
- `keywords` is a list of words or sentences to detect.
- Put each keyword on a new line starting with `-`.

## How Alerts Work

### HTML Change Alert

The first run saves a baseline.

From the second run, WatchDog Cloud compares the new HTML hash with the previous hash.

If the hash changes, it sends a Telegram alert.

### Keyword Alert

If a keyword appears on the page for the first time, it sends a Telegram alert.

After a keyword alert is sent, it is saved in:

```text
data/keyword_alerts.json
```

This prevents duplicate keyword alerts.

## Reset Keyword Alerts

If you want to receive keyword alerts again:

1. Open `data/keyword_alerts.json`.
2. Delete the saved keyword entry.
3. Commit and push the file.

You can also delete the whole `data/keyword_alerts.json` file.

## Run Locally

Install dependencies:

```bash
pip install -r requirements.txt
```

Run:

```bash
python monitor.py
```

For local Telegram testing, set environment variables first.

PowerShell:

```powershell
$env:TELEGRAM_BOT_TOKEN="your_bot_token"
$env:TELEGRAM_CHAT_ID="your_chat_id"
python monitor.py
```

## Notes

- GitHub Actions scheduled workflows may not run at the exact minute every time.
- Private repositories may have GitHub Actions minute limits.
- This project does not render JavaScript pages because Playwright is intentionally not used.
