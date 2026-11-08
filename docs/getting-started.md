# Getting started with Stealth

This guide is for setting Stealth up on a Windows PC, for yourself or for someone in your family.
It takes about 15 minutes. You don't need to know anything technical.

**What Stealth does.** It writes to companies that collect and sell people's details (data
brokers, people-search sites, marketing lists) and tells them, under UK data protection law, to
delete yours. It keeps track of who replied, chases the ones who didn't, and helps you complain to
the Information Commissioner's Office (ICO) when a company ignores the law. It runs on your own
computer: there's no account, and nothing about you goes anywhere except the letters you approve.

## Before you start

You need:

- **A Windows 10 or 11 PC.** Macs aren't supported yet.
- **A Gmail, Yahoo or iCloud email address** for the letters to come from. Outlook and Hotmail
  addresses don't work, because Microsoft no longer lets apps sign in with a password. If that's
  the only email you have, make a free Gmail just for Stealth. That has an upside: companies'
  replies go there and stay out of your main inbox. Put your main address in the **Details** step
  anyway, so companies can find the records they hold under it.
- **An app password for that email account.** This is a separate password just for Stealth, so
  it never sees your real one. Step 3 below shows how to make one.
- **Somewhere safe to write down a recovery code**: paper, or a password manager.

## 1. Download and install

1. Go to the [Releases page](https://github.com/Anish25012/stealth/releases) and open the newest
   release. Don't use one marked "broker-list": that's the company list, not the app.
2. Under **Assets**, download `Stealth-…-windows-setup.exe`.
3. Double-click it.

**Windows may say "Windows protected your PC".** Stealth is free and isn't code-signed (a signing
certificate costs hundreds of pounds a year), so Windows doesn't recognise the publisher yet.
Choose **More info**, check it says the app is `Stealth-…-windows-setup.exe`, then choose
**Run anyway**. [Installing Stealth](installing.md) explains why, and how to check the download is
genuine first.

**Norton, Avast or another antivirus may warn about it too.** See
[Stealth and antivirus](antivirus.md).

The installer doesn't need administrator rights. It puts Stealth in your Start menu and opens it.

## 2. Try the demo (optional)

The welcome screen offers **Try the demo**. It shows Stealth working with made-up people and
companies, and sends nothing anywhere. Choose **Leave the demo** at the top when you're done.

## 3. Set it up

On the welcome screen, choose **Get started**. There are six steps.

**Who it's for.** Yourself, or someone else. If it's someone else, Stealth records how they agreed
to it, because the letters are written in their name. Only set it up for someone who has agreed.

**Details.** Their full name, any other names they've used, every email address companies might
have (old ones too), home addresses (current and past, with postcodes) and phone numbers. Also
where they live: leave this as United Kingdom. The more you add, the more records companies can
find. These details stay on this computer and only go into letters you approve.

**Email.** Choose the provider and enter the app password. To make one for Gmail:

1. Turn on 2-Step Verification for the Google account, if it isn't on already.
2. Go to <https://myaccount.google.com/apppasswords>.
3. Type "Stealth" as the name and choose **Create**.
4. Copy the 16-letter password Google shows, and paste it into Stealth.

Stealth checks it can sign in and read the inbox before saving anything. The password is kept in
Windows Credential Manager, not in Stealth's own files.

**Recovery code.** Write it down or save it in a password manager, somewhere other than this
computer. If Windows is ever reinstalled, it's the only way to open Stealth's records again.
Nobody from Stealth will ever ask for it.

**Companies.** About 75 UK-relevant companies are ticked. You can untick any. "Also write to … more"
adds hundreds of mainly US companies where it's less clear UK law reaches them: it's off by
default, and turning it on shares only the person's name and email with them.

**Finish.** Stealth writes every letter, but **sends none of them yet**.

## 4. Approve the letters

Stealth opens **Waiting for your OK**. Read a few letters: they quote the law and give the company
a date to reply by. Choose **Approve and send** for the ones you're happy with.

Stealth sends at most 40 a day, spaced out, so the email account isn't flagged for sending in
bulk. The rest go on the following days by themselves.

A few companies only accept requests through their own website. These appear under **Needs you**,
with a link to the form. Fill it in, then choose **I've filled in their form** so Stealth starts
the clock.

## 5. Let it run

Once a day (9am, unless you change it in **Settings**), Stealth:

- reads new replies in the inbox (it only reads, and never deletes or moves your email);
- records clear answers, like "we've deleted your data", by itself;
- sends a formal complaint to any company that missed its one-month deadline;
- asks companies again every two or three months, because brokers re-list people.

It never sends a new letter without your OK. If the computer was off at 9am, it catches up soon
after it's next on. Turn on **Start with Windows** in Settings so this happens even if nobody opens
Stealth. Its icon sits in the system tray (by the clock), and it pops up a notice when something
needs you.

## 6. When something needs you

**Needs you** in the sidebar shows everything waiting for a person:

- **A reply Stealth couldn't be sure about.** It shows its best guess. Read the reply and confirm
  what it meant.
- **A company asking for ID.** Stealth drafts a polite reply explaining that ID isn't normally
  needed to stop marketing. You don't have to send a passport or driving licence.
- **Ready for the ICO.** A company had its deadline and a formal complaint and still didn't put it
  right. Choose **Download ICO complaint**, then submit it on the
  [ICO's website](https://ico.org.uk/make-a-complaint/) with the evidence Stealth saved.

## Everything else

- **Reports** has a monthly report you can save as a PDF, an exposure score, and the evidence for
  every company.
- **Scan** helps you search people-search sites for yourself. Stealth opens your own browser in a
  separate, empty window, you do the searching, and it saves what the page showed as evidence.
- **Phone view** (in Settings) lets you look at Stealth from a phone on the same Wi-Fi for 30
  minutes, read-only. The phone's browser warns about the certificate once; that's expected.
- **Updates.** Stealth checks GitHub once a day and tells you in Settings when a new version is
  out. Download the new installer from the Releases page and run it: your records are kept. The
  list of companies updates by itself, and only if it's signed by the project.
- **More than one person** can be set up on one computer, each kept separately: **Settings** ›
  **People**.

## If something goes wrong

- Error screens have a **Copy details** button. The details have personal information removed,
  so you can paste them to whoever is helping you, or into a
  [GitHub issue](https://github.com/Anish25012/stealth/issues).
- A company's address bounced? Its page has a link to report it, so the list gets fixed for
  everyone.
- Stealth's records live in `%LOCALAPPDATA%\Stealth`, encrypted. Uninstalling the app (Windows
  **Settings** › **Apps**) leaves them in place, so reinstalling picks up where you left off.

This isn't legal advice. Stealth helps you use rights you already have; read what it sends.
