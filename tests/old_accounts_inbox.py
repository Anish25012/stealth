"""A fixture inbox for "Find my old accounts": sender, subject, and the right answer.

The answer is the company domain a sign-up email should be credited to, or None for anything
that isn't one. Senders are written the way real companies send (subdomains, mailing services,
encoded headers, "The X Team"); the person is made up and their own domain is example.org.
"""

from __future__ import annotations

from datetime import date, timedelta

from stealth.oldaccounts.classify import Header

OWN_DOMAINS = frozenset({"example.org"})

SIGNUPS: list[tuple[str, str, str | None]] = [
    ("Spotify <no-reply@spotify.com>", "Welcome to Spotify", "spotify.com"),
    ("Netflix <info@mailer.netflix.com>", "Welcome to Netflix, Sam!", "netflix.com"),
    ('"Deliveroo" <noreply@t.deliveroo.co.uk>', "Confirm your email address", "deliveroo.co.uk"),
    ("ASOS <asos@info.asos.com>", "Welcome to ASOS", "asos.com"),
    ("Airbnb <automated@airbnb.com>", "Please confirm your email address", "airbnb.com"),
    ("Duolingo <hello@duolingo.com>", "Verify your email", "duolingo.com"),
    ("Reddit <noreply@redditmail.com>", "Verify your Reddit email address", "redditmail.com"),
    ("Strava <no-reply@strava.com>", "Thanks for signing up for Strava!", "strava.com"),
    ("Monzo <help@monzo.com>", "Verify your email address", "monzo.com"),
    ("Etsy <transaction@etsy.com>", "Welcome to Etsy!", "etsy.com"),
    ("Canva <start@engage.canva.com>", "Welcome to Canva \U0001f389", "canva.com"),
    (
        "Trainline <noreply@info.thetrainline.com>",
        "Thanks for registering with Trainline",
        "thetrainline.com",
    ),
    ("Uber <uber@uber.com>", "Welcome to Uber", "uber.com"),
    (
        "LinkedIn <security-noreply@linkedin.com>",
        "Please confirm your email address",
        "linkedin.com",
    ),
    ("Pinterest <noreply@account.pinterest.com>", "Confirm your email", "pinterest.com"),
    ("Zoopla <noreply@zoopla.co.uk>", "Activate your account", "zoopla.co.uk"),
    ("Just Eat <noreply@just-eat.co.uk>", "Your account has been created", "just-eat.co.uk"),
    ("Vinted <no-reply@vinted.co.uk>", "Confirm your registration", "vinted.co.uk"),
    ("Depop <hello@depop.com>", "Welcome to Depop", "depop.com"),
    ("Rightmove <noreply@rightmove.co.uk>", "Complete your registration", "rightmove.co.uk"),
    ("Boots <boots@e.boots.com>", "Welcome to Boots.com", "boots.com"),
    ("Tesco <customer.service@tesco.co.uk>", "Your new Tesco account", "tesco.co.uk"),
    ("Ocado <customerservices@ocado.com>", "Thank you for registering with Ocado", "ocado.com"),
    ("Eventbrite <noreply@order.eventbrite.com>", "Welcome to Eventbrite", "eventbrite.com"),
    ("Meetup <info@meetup.com>", "Finish setting up your account", "meetup.com"),
    ("The Notion Team <team@makenotion.com>", "Welcome to Notion!", "makenotion.com"),
    ("Dropbox <no-reply@dropbox.com>", "Verify your email address", "dropbox.com"),
    ("Gumtree <noreply@gumtree.com>", "Activate your Gumtree account", "gumtree.com"),
    ("Booking.com <noreply@booking.com>", "Welcome to Booking.com", "booking.com"),
    ("Wise <noreply@wise.com>", "Confirm your email", "wise.com"),
    ("Headspace <hello@headspace.com>", "Welcome to Headspace", "headspace.com"),
    (
        "=?UTF-8?B?WmFsYW5kbw==?= <info@service-mail.zalando.co.uk>",
        "=?UTF-8?B?V2VsY29tZSB0byBaYWxhbmRv?=",  # "Welcome to Zalando"
        "zalando.co.uk",
    ),
    ("Mumsnet <noreply@mumsnet.com>", "Thanks for joining Mumsnet", "mumsnet.com"),
    # A real sign-up, sent through a mailing service: it can't be traced to the company, so the
    # classifier should (and does) miss it. Counted as a miss below, to keep the numbers honest.
    ("Pottery Club <bounce@em.sendgrid.net>", "Welcome to Pottery Club", "potteryclub"),
]

LOOKALIKES: list[tuple[str, str, str | None]] = [
    ("Spotify <no-reply@spotify.com>", "Your Spotify receipt", None),
    ("Netflix <info@account.netflix.com>", "New sign-in to your account", None),
    ("Amazon.co.uk <auto-confirm@amazon.co.uk>", "Your Amazon.co.uk order of Kettle", None),
    ("Monzo <help@monzo.com>", "Reset your password", None),
    ("ASOS <asos@info.asos.com>", "Welcome to the weekend: 20% off everything", None),
    ("Deliveroo <noreply@t.deliveroo.co.uk>", "Your order is on its way", None),
    ("Alex Friend <alex.friend@gmail.com>", "Welcome to the new house!", None),
    ("Mum <mum@btinternet.com>", "Welcome home love", None),
    ("Google <no-reply@accounts.google.com>", "Security alert", None),
    ("Google <no-reply@accounts.google.com>", "Welcome to your new Google Account", None),
    (
        "The Guardian <newsletters@theguardian.com>",
        "Confirm your newsletter subscription",
        None,
    ),
    ("Eventbrite <noreply@order.eventbrite.com>", "Your tickets for Jazz Night", None),
    ("Trainline <noreply@info.thetrainline.com>", "Your e-ticket and booking confirmation", None),
    ("LinkedIn <notifications-noreply@linkedin.com>", "You appeared in 5 searches", None),
    ("Booking.com <noreply@booking.com>", "Confirm your booking at Seaview B&B", None),
    ("Uber <uber@uber.com>", "Your Thursday evening trip with Uber", None),
    ("Etsy <transaction@etsy.com>", "Welcome back! Here's what's new", None),
    ("Dropbox <no-reply@dropbox.com>", "Verify your new device", None),
    ("Wise <noreply@wise.com>", "Your verification code is 482913", None),
    ("Sam Sample <sam.sample@example.org>", "Welcome to the team", None),
    ("Headspace <hello@headspace.com>", "Welcome to 2027: a fresh start offer", None),
    ("Royal Mail <no-reply@royalmail.com>", "Your delivery is on its way", None),
]


def inbox(start: date = date(2014, 1, 6)) -> list[tuple[Header, str | None]]:
    """Every message, a week apart, with the right answer."""
    rows = SIGNUPS + LOOKALIKES
    return [
        (Header(sender, subject, start + timedelta(days=7 * n)), answer)
        for n, (sender, subject, answer) in enumerate(rows)
    ]
