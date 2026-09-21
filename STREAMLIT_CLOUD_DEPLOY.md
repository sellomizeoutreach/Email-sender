# Deploying Sellomize Reach on Streamlit Community Cloud

This guide walks you through deploying your **Sellomize Reach** web dashboard to **Streamlit Community Cloud** so you and your team can access the CRM, template engine, AI generator, and email review queue from any browser or device.

---

## 1. Prerequisites (GitHub)
Make sure your latest code is committed and pushed in GitHub Desktop:
1. In **GitHub Desktop**, click **Commit to main**.
2. Click **Push origin** (or **Publish repository**) to ensure all files are synced to GitHub.

---

## 2. Deploying on Streamlit Community Cloud (3 Clicks)

1. Go to **[share.streamlit.io](https://share.streamlit.io)** and log in with your GitHub account.
2. Click the **"New app"** (or **"Deploy an app"**) button.
3. Fill in the deployment form:
   - **Repository:** Select your repository (e.g., `YOUR_USERNAME/Email-sender` or `sellomize-reach`)
   - **Branch:** `main`
   - **Main file path:** `app.py`
   - **App URL:** (Optional custom URL, e.g. `sellomize-reach.streamlit.app`)
4. Click **Deploy!**

Streamlit Cloud will install dependencies from `requirements.txt` and launch your branded Sellomize dashboard in 1–2 minutes!

---

## 3. Cloud Architecture vs Local Outlook Dispatch

- **Streamlit Community Cloud (Web Dashboard):**
  - Cloud-hosted web interface for lead management, tagging, CSV imports, Spintax templates, LiteLLM generation, and draft review.
  - Accessible from anywhere (desktop, laptop, tablet).

- **Desktop Outlook Dispatcher (`scheduler.py`):**
  - Microsoft Outlook is a desktop Windows application. The scheduler process (`run_scheduler.bat` or `SellomizeSetup.exe`) runs on your Windows machine to dispatch approved emails directly through your logged-in Outlook account.