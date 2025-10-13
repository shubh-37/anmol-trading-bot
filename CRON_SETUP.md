# XTS Token Refresh Cron Job Setup

This guide explains how to set up automated daily XTS token refresh using cron.

## Overview

The XTS API requires authentication tokens that expire. To ensure uninterrupted trading operations, this system automatically refreshes the XTS token daily at 9:00 AM.

### Components

1. **xts_token_refresh.py** - Python script that performs the token refresh
2. **refresh_xts_token.sh** - Shell wrapper for cron compatibility
3. **xts_store_token.json** - Token storage file (auto-generated)
4. **xts_token_refresh.log** - Detailed log of token refresh operations
5. **cron_output.log** - Cron job execution log

## Prerequisites

1. **Environment Variables**: Ensure your `.env` file contains:
   ```bash
   XTS_USER_ID=your_xts_user_id
   XTS_INTERACTIVE_API_KEY=your_xts_api_key
   XTS_INTERACTIVE_API_SECRET=your_xts_api_secret
   XTS_API_SOURCE=WEBAPI
   XTS_API_ROOT=https://api.xts.com

   # Optional: For Telegram notifications
   TELEGRAM_TOKEN=your_telegram_bot_token
   TELEGRAM_CHAT_ID=your_telegram_chat_id
   ```

2. **Python Dependencies**: Install required packages
   ```bash
   pip install -r requirements.txt
   ```

3. **File Permissions**: Ensure scripts are executable
   ```bash
   chmod +x refresh_xts_token.sh
   chmod +x xts_token_refresh.py
   ```

## Manual Testing

Before setting up the cron job, test the scripts manually:

### Test Python Script Directly
```bash
cd /path/to/sha
python3 xts_token_refresh.py
```

Expected output:
```
==============================================================
XTS Token Refresh Cron Job Started
Execution Time: 2024-10-13 09:00:00
==============================================================
Starting XTS token refresh process...
✓ XTS token refreshed successfully!
✓ Token saved to: ./xts_store_token.json
```

### Test Shell Wrapper
```bash
cd /path/to/sha
./refresh_xts_token.sh
```

Expected output:
```
==================================================
XTS Token Refresh - 2024-10-13 09:00:00
Working Directory: /path/to/sha
==================================================
✓ Virtual environment activated
Python version: Python 3.x.x
✓ Required packages found

Running XTS token refresh script...
--------------------------------------------------
[Python script output]
--------------------------------------------------
✓ Token refresh completed successfully (exit code: 0)
==================================================
```

## Cron Setup

### 1. Get Absolute Path
First, get the absolute path to your project:
```bash
cd /path/to/sha
pwd
```
Copy this path, you'll need it for the crontab entry.

### 2. Edit Crontab
Open your crontab editor:
```bash
crontab -e
```

### 3. Add Cron Entry

Add one of the following entries based on your preference:

#### Option A: Daily at 9:00 AM (Recommended)
```bash
0 9 * * * /path/to/sha/refresh_xts_token.sh >> /path/to/sha/cron_output.log 2>&1
```

#### Option B: Daily at 9:00 AM with email notifications
```bash
MAILTO=your-email@example.com
0 9 * * * /path/to/sha/refresh_xts_token.sh >> /path/to/sha/cron_output.log 2>&1
```

#### Option C: Multiple times per day (every 6 hours)
```bash
0 9,15,21,3 * * * /path/to/sha/refresh_xts_token.sh >> /path/to/sha/cron_output.log 2>&1
```

#### Option D: Weekdays only at 9:00 AM
```bash
0 9 * * 1-5 /path/to/sha/refresh_xts_token.sh >> /path/to/sha/cron_output.log 2>&1
```

### 4. Save and Exit
- For `vi`/`vim`: Press `Esc`, then type `:wq` and press Enter
- For `nano`: Press `Ctrl+X`, then `Y`, then Enter

### 5. Verify Cron Entry
```bash
crontab -l
```

You should see your newly added cron job listed.

## Cron Time Format Reference

```
* * * * * command
│ │ │ │ │
│ │ │ │ └─── Day of week (0-7, Sunday=0 or 7)
│ │ │ └───── Month (1-12)
│ │ └─────── Day of month (1-31)
│ └───────── Hour (0-23)
└─────────── Minute (0-59)
```

### Common Schedules
```bash
0 9 * * *        # Daily at 9:00 AM
0 */6 * * *      # Every 6 hours
30 8 * * 1-5     # Weekdays at 8:30 AM
0 0 * * 0        # Every Sunday at midnight
*/30 * * * *     # Every 30 minutes
```

## Monitoring

### Check Cron Execution
View the last few cron executions:
```bash
tail -50 /path/to/sha/cron_output.log
```

### Check Token Refresh Details
View detailed token refresh logs:
```bash
tail -100 /path/to/sha/xts_token_refresh.log
```

### Check Token File
Verify the token was updated:
```bash
cat /path/to/sha/xts_store_token.json
```

Expected format:
```json
{
    "token": "your_xts_token_here",
    "userID": "your_user_id",
    "timestamp": "2024-10-13T09:00:00.123456",
    "refreshed_at": "2024-10-13 09:00:00"
}
```

### Watch Logs in Real-Time
```bash
tail -f /path/to/sha/cron_output.log
```

## Troubleshooting

### Cron Job Not Running

1. **Check cron service status**:
   ```bash
   # macOS
   sudo launchctl list | grep cron

   # Linux
   sudo systemctl status cron
   # or
   sudo service cron status
   ```

2. **Check system logs**:
   ```bash
   # macOS
   log show --predicate 'process == "cron"' --last 1h

   # Linux
   sudo grep CRON /var/log/syslog
   # or
   sudo journalctl -u cron
   ```

3. **Verify crontab entry**:
   ```bash
   crontab -l
   ```

### Script Fails

1. **Check environment variables**:
   ```bash
   cd /path/to/sha
   python3 -c "from dotenv import load_dotenv; import os; load_dotenv(); print('XTS_API_KEY:', os.getenv('XTS_INTERACTIVE_API_KEY')[:10] + '...')"
   ```

2. **Check Python/dependencies**:
   ```bash
   which python3
   python3 --version
   python3 -c "import requests, dotenv; print('Dependencies OK')"
   ```

3. **Test manually**:
   ```bash
   cd /path/to/sha
   ./refresh_xts_token.sh
   ```

4. **Check permissions**:
   ```bash
   ls -la /path/to/sha/refresh_xts_token.sh
   ls -la /path/to/sha/xts_token_refresh.py
   ```
   Both should have execute permissions (`-rwxr-xr-x`).

### Token Refresh Fails

1. **Check API credentials**: Verify XTS credentials in `.env` file
2. **Check network connectivity**: Ensure server can reach `https://api.xts.com`
3. **Check API status**: XTS API might be down - check their status page
4. **Check logs**: Review `xts_token_refresh.log` for detailed error messages

### No Telegram Notifications

1. **Check Telegram credentials**:
   ```bash
   python3 -c "from dotenv import load_dotenv; import os; load_dotenv(); print('Bot Token:', os.getenv('TELEGRAM_TOKEN')[:10] + '...')"
   ```

2. **Test Telegram manually**:
   ```bash
   curl -X POST "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/sendMessage" \
     -H "Content-Type: application/json" \
     -d '{"chat_id": "<YOUR_CHAT_ID>", "text": "Test message"}'
   ```

## Log Rotation

To prevent log files from growing too large, set up log rotation:

### Create logrotate config
```bash
sudo nano /etc/logrotate.d/xts-trading
```

Add:
```
/path/to/sha/cron_output.log /path/to/sha/xts_token_refresh.log {
    daily
    rotate 30
    compress
    delaycompress
    missingok
    notifempty
    create 0644 your_username your_group
}
```

### Test logrotate
```bash
sudo logrotate -d /etc/logrotate.d/xts-trading
```

## Disable/Remove Cron Job

### Temporarily Disable
Edit crontab and comment out the line:
```bash
crontab -e
```
Add `#` at the beginning:
```bash
# 0 9 * * * /path/to/sha/refresh_xts_token.sh >> /path/to/sha/cron_output.log 2>&1
```

### Permanently Remove
```bash
crontab -e
```
Delete the entire line and save.

### Verify Removal
```bash
crontab -l
```

## Best Practices

1. **Monitor regularly**: Check logs weekly to ensure tokens are refreshing
2. **Set up alerts**: Configure email notifications for cron failures
3. **Backup tokens**: Keep a backup of working token files
4. **Test after changes**: Always test manually after modifying scripts
5. **Keep credentials secure**: Never commit `.env` file to version control
6. **Use absolute paths**: Always use absolute paths in cron entries
7. **Log rotation**: Implement log rotation to prevent disk space issues

## Security Notes

- `xts_store_token.json` has restricted permissions (0600) - only owner can read/write
- Never share or commit token files to version control
- Store `.env` file securely with appropriate permissions (0600)
- Regularly rotate API credentials
- Monitor logs for unauthorized access attempts

## Support

If you encounter issues:

1. Check this documentation first
2. Review log files for detailed error messages
3. Test scripts manually to isolate the problem
4. Verify all prerequisites are met
5. Check XTS API documentation for any changes

## Additional Resources

- [Crontab Guru](https://crontab.guru/) - Cron schedule expression editor
- [XTS API Documentation](https://api.xts.com/docs) - Official XTS API docs
- Project README - General project setup and configuration
