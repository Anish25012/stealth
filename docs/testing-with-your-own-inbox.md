# Testing with your own inbox

This sends one real letter to an address of your own, so you can confirm your mailbox works before
writing to any company. No company is contacted: `--only` sends to the one id you name and leaves
everything else alone.

You need a **second email address** for the test, because Stealth ignores replies from your own
sending address.

**1. Use a test folder, so your real records stay clean.**

Windows (Command Prompt):

```
set STEALTH_HOME=%USERPROFILE%\stealth-test
```

macOS or Linux:

```
export STEALTH_HOME=~/stealth-test
```

Keep using this same terminal window for the rest of these steps.

**2. Set up a test profile.**

```
stealth init
```

**3. Add yourself as a broker.** Save this as `brokers_extra.json` in the test folder. Replace the
`email` with your second address, and `domain` with that address's domain.

```json
{
  "version": 1,
  "brokers": [
    {
      "id": "me-test",
      "name": "My Test Inbox",
      "domain": "outlook.com",
      "region": "UK",
      "category": "other",
      "method": "email",
      "email": "YOUR.SECOND.ADDRESS@outlook.com",
      "form_url": null,
      "notes": "",
      "source": "me",
      "last_verified": "2026-11-01",
      "laws": ["uk-gdpr"],
      "tier": "core",
      "confidence": 1
    }
  ]
}
```

**4. Preview, then send.**

```
stealth send --only me-test
stealth send --live --only me-test
```

Check it arrives at your second address, and that a BCC copy reaches your sending address.

**5. Reply and detect it.** Reply from your second address, then:

```
stealth inbox
stealth status
```

The case should say `replied`. Then record what the reply meant:

```
stealth confirm me-test removed
```

**6. Clean up.** Delete the `stealth-test` folder and close the terminal window.
