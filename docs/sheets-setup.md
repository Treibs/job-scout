# Google Sheets tracker setup

job-scout writes its ranked, deduplicated job tracker to a Google Sheet via a
**service account** (a non-human Google identity). This is a one-time setup. At
the end you'll have two secrets — `GOOGLE_SERVICE_ACCOUNT_JSON` and `SHEET_ID` —
that both local dev and GitHub Actions use to authenticate.

These steps mirror PROJECT.md §10. Plan on ~10 minutes.

---

## Overview

1. Create a Google Cloud project and enable the Google Sheets API.
2. Create a service account and download its JSON key.
3. Create the tracker Sheet and share it with the service-account email.
4. Set the `GOOGLE_SERVICE_ACCOUNT_JSON` and `SHEET_ID` secrets, then run
   `setup_sheet.py`.

---

## Step 1 — Create a GCP project and enable the Sheets API

1. Go to the [Google Cloud Console](https://console.cloud.google.com/).
2. In the project picker (top bar), click **New Project**. Name it anything
   (e.g. `job-scout`) and create it. Make sure it's selected afterward.
3. Open **APIs & Services → Library** (or go directly to
   <https://console.cloud.google.com/apis/library>).
4. Search for **Google Sheets API**, open it, and click **Enable**.

> You do *not* need the Google Drive API for the basic flow — job-scout writes
> to a Sheet you've already created and shared. (If you later have it create new
> spreadsheets from scratch, enable the Drive API too.)

---

## Step 2 — Create a service account and download its JSON key

1. Open **APIs & Services → Credentials**
   (<https://console.cloud.google.com/apis/credentials>).
2. Click **Create Credentials → Service account**.
3. Give it a name (e.g. `job-scout-writer`) and click **Create and Continue**.
   You can skip the optional "grant access" and "grant users" steps — click
   **Done**.
4. Back on the Credentials page, click your new service account, open the
   **Keys** tab.
5. **Add Key → Create new key → JSON → Create.** A `.json` file downloads.
   Keep it safe — it's a credential.

The JSON contains a field called **`client_email`** that looks like:

```
job-scout-writer@your-project-id.iam.gserviceaccount.com
```

You'll share the Sheet with that email in Step 3.

> **Never commit this file.** `service_account.json` and `.env` are already in
> `.gitignore`. In CI it lives only in a GitHub Secret.

---

## Step 3 — Create the tracker Sheet and share it with the service account

1. Create a new spreadsheet at <https://sheets.new> (or in Google Drive). Name
   it whatever you like, e.g. "Job Scout Tracker".
2. Click **Share** (top right).
3. Paste the service account's **`client_email`** (from Step 2) into the
   people field, set its role to **Editor**, and **Send / Share**.
   - Untick "Notify people" — it's a robot, it won't read email.
4. Copy the **Sheet ID** out of the URL. Given:

   ```
   https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789/edit#gid=0
   ```

   the Sheet ID is the long token between `/d/` and `/edit`:

   ```
   1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789
   ```

> If the service account isn't shared as Editor, every write fails with a
> `403 PERMISSION_DENIED`. This is the #1 setup mistake — double-check the
> share.

---

## Step 4 — Set the secrets and run `setup_sheet.py`

job-scout reads two values:

| Name                          | Value                                                      |
|-------------------------------|------------------------------------------------------------|
| `GOOGLE_SERVICE_ACCOUNT_JSON` | The **full contents** of the JSON key file (or a path to it in local dev) |
| `SHEET_ID`                    | The Sheet ID from Step 3                                    |

### Local dev (`.env`)

Copy `.env.example` to `.env` and fill it in:

```
GOOGLE_SERVICE_ACCOUNT_JSON=/absolute/path/to/service_account.json
SHEET_ID=1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789
```

For local dev you can point `GOOGLE_SERVICE_ACCOUNT_JSON` at the JSON **file
path** (easier) or paste the raw JSON blob — both are accepted.

### GitHub Actions (Secrets)

In your repo: **Settings → Secrets and variables → Actions → New repository
secret**. Add:

- `GOOGLE_SERVICE_ACCOUNT_JSON` — paste the **entire JSON blob** (open the key
  file, copy everything including the braces). In CI it can't be a file path, so
  it must be the raw JSON.
- `SHEET_ID` — the Sheet ID.

(You'll also add `ANTHROPIC_API_KEY` and optionally `PROXY_URLS` for the full
pipeline — see the README.)

### Run the one-time setup script

With the env in place, initialize the sheet:

```bash
python scripts/setup_sheet.py
```

This creates the tracker tab, writes the header row, and applies conditional
formatting on the `score` column. The columns it lays down are:

```
score | mission | comp | learning | wlb | prestige | title | company |
location | comp_estimate | source | date_posted | first_seen | apply_url |
status | rationale | red_flags
```

After that, normal runs (`python scripts/run.py --config config/search.yaml`)
**upsert** into the sheet: new roles are appended, existing rows have their
`status` / `last_seen` / score refreshed, and rows whose listing has disappeared
are marked stale.

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `403 PERMISSION_DENIED` on write | Sheet not shared with the service-account `client_email` as Editor (Step 3). |
| `API has not been used / is disabled` | Google Sheets API not enabled on the project (Step 1). |
| `Requested entity was not found` / 404 | Wrong `SHEET_ID`, or the secret has stray whitespace/quotes. |
| `invalid_grant` / JSON parse error | `GOOGLE_SERVICE_ACCOUNT_JSON` is truncated — in CI it must be the **complete** JSON blob, not a path. |
