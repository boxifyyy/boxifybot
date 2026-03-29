# Deploy Toxic Casino Bot on Render.com

## Step 1: Push code to GitHub

1. Create a **new private repo** on GitHub: https://github.com/new
   - Name it `toxic-casino-bot` (or whatever you like)
   - Set it to **Private**
   - Do NOT initialize with README

2. Upload the files from this folder (`bot.py`, `requirements.txt`, `render.yaml`, `.gitignore`) to that repo. You can drag-and-drop them on GitHub or use git:
   ```
   git remote add origin https://github.com/YOUR_USERNAME/toxic-casino-bot.git
   git push -u origin main
   ```

## Step 2: Deploy on Render

1. Go to https://render.com and sign up / log in (you can sign in with GitHub).

2. Click **New +** > **Background Worker** (NOT Web Service — your bot uses polling, not webhooks).

3. Connect your GitHub repo (`toxic-casino-bot`).

4. Configure:
   - **Name**: `toxic-casino-bot`
   - **Runtime**: `Python`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python bot.py`

5. Add these **Environment Variables** (click "Add Environment Variable" for each):

   | Key | Value |
   |-----|-------|
   | `TELEGRAM_TOKEN` | `8480249301:AAGP9XIs8z00ebs61xxsfhyT0-C4NiR7wGc` |
   | `OXAPAY_MERCHANT_API_KEY` | `PFKC4E-NGPGXZ-H2ECUA-MU6VPU` |
   | `OXAPAY_PAYOUT_API_KEY` | `HQ0DXT-SP80D6-WX5D6E-OHJOHI` |
   | `OXAPAY_GENERAL_API_KEY` | `HK9CQX-YBJRF1-TOL01T-OH0PAD` |
   | `SLOTS_WEBAPP_URL` | `https://real-slot-games-app-eak9l6hl.devinapps.com` |
   | `PRIVATE_LOG_GROUP_ID` | `0` (or your actual group chat ID) |

6. Click **Create Background Worker**.

7. Render will build and start your bot. Check the **Logs** tab to confirm it says "Toxic Casino Bot is starting..."

## Notes

- Render's **free tier** includes Background Workers but they spin down after inactivity. For always-on, use the **Starter plan** ($7/month).
- Your `balances.json` file will reset on each deploy since Render uses ephemeral storage. For persistent data, consider using a database (e.g., Render PostgreSQL) in the future.
- The `render.yaml` file is included for Render's "Blueprint" auto-deploy feature — it pre-configures the service if you use "New > Blueprint Instance" instead.
