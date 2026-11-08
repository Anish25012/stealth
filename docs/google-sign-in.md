# Signing in with Google

Stealth can connect to a Gmail account in two ways:

- **Sign in with Google** (recommended). Your browser opens Google's own sign-in page, so Stealth
  never sees your Google password and there's no app password to make. Mail goes over Google's
  Gmail API, over HTTPS, which antivirus such as Norton doesn't block.
- **An app password**, over IMAP and SMTP. Still there for Yahoo, iCloud and other providers, and
  as a fallback for Gmail.

## What Stealth can and can't do with your Gmail

Stealth asks Google for two permissions only:

| Permission | Why |
|---|---|
| Send email on your behalf (`gmail.send`) | To send the letters you approve |
| View your email messages and settings (`gmail.readonly`) | To spot companies' replies |

It doesn't ask to change, label, move or delete anything, so it can't. Its sign-in (a "refresh
token") is kept in Windows Credential Manager on your computer and never leaves it, except to
Google. To remove Stealth's access at any time, go to
[myaccount.google.com/connections](https://myaccount.google.com/connections).

Google will need you to sign in again if you change your Google password, remove Stealth's access,
or don't use Stealth for six months. Stealth tells you when, and **Settings** › **Email** ›
**Sign in with Google again** fixes it.

## The "Google hasn't verified this app" screen

Google shows this for apps that haven't been through its paid review. Reading Gmail is a
"restricted" permission, and Google's review for those includes an annual security assessment by
an outside firm. That isn't worth it for a free tool shared with family and friends. Google allows
this for personal use with fewer than 100 people
([Google's rules](https://support.google.com/cloud/answer/13464323)).

To carry on: choose **Advanced**, then **Go to Stealth (unsafe)**, then tick both permission boxes
and choose **Continue**. You only see this when signing in.

## For maintainers: setting up the Google Cloud project

Do this once, in a Google account you're happy to show as the app's contact: its email address
appears on the sign-in screen. Use a separate Google account if you'd rather not show your own.

1. **Create a project.** Go to <https://console.cloud.google.com/>, choose the project picker at
   the top, then **New project**. Call it "Stealth" and choose **Create**. Make sure it's selected.
2. **Turn on the Gmail API.** Go to **APIs & Services** › **Library**, search for "Gmail API",
   open it and choose **Enable**.
3. **Branding.** Go to **Google Auth Platform** (<https://console.cloud.google.com/auth/overview>)
   and choose **Get started**. Enter:
   - App name: `Stealth`
   - User support email: your address
   - Audience: **External**
   - Contact information: your address
   Agree to the policy and choose **Create**. Don't upload a logo: a logo needs Google's brand
   review before it shows, and the app works without one. You can add the home page
   (`https://github.com/Anish25012/stealth`) and privacy policy links on the **Branding** page.
4. **Permissions.** Go to **Data Access** › **Add or remove scopes**. Paste these two in "Manually
   add scopes", choose **Add to table**, then **Update** and **Save**:
   ```
   https://www.googleapis.com/auth/gmail.send
   https://www.googleapis.com/auth/gmail.readonly
   ```
5. **Publish it.** Go to **Audience** and choose **Publish app**, then **Confirm**. It will say
   the app needs verification: **don't submit it for verification**. Publishing matters: while
   it's in "Testing", Google ends every sign-in after 7 days.
6. **Create the desktop client.** Go to **Clients** › **Create client**. Application type:
   **Desktop app**. Name: `Stealth desktop`. Choose **Create**, then copy the **Client ID** and
   **Client secret**.
7. **Put them in Stealth.** Set `CLIENT_ID` and `CLIENT_SECRET` in
   `src/stealth/mail/google_oauth.py`. Google treats a desktop app's client secret as not secret
   (anyone can read it out of the installed app), so it's fine in the public repository. It lets
   nobody into anyone's mail: only a person's own sign-in does that.

To try a client without changing the code, set `STEALTH_GOOGLE_CLIENT_ID` and
`STEALTH_GOOGLE_CLIENT_SECRET` before starting Stealth.

### Limits to know about

- **100 people, ever.** An unverified app can be signed in to by 100 different Google accounts in
  total. Plenty for family and friends; past that, the app would need Google's verification.
- **100 sign-ins per person.** Each Google account keeps at most 100 live sign-ins for this app.
  Past that, Google ends the oldest. Stealth only uses the newest, so this never causes trouble.
