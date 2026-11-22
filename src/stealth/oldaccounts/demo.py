"""Made-up sign-up emails for the demo's "Find my old accounts". Every company is a `.test` one."""

from __future__ import annotations

from datetime import date, timedelta

from stealth.oldaccounts.classify import Header


def demo_messages(today: date) -> list[Header]:
    def ago(days: int) -> date:
        return today - timedelta(days=days)

    return [
        Header('"ShopWise" <hello@mail.shopwise.test>', "Welcome to ShopWise, Alex!", ago(3100)),
        Header(
            '"ShopWise" <hello@mail.shopwise.test>', "Your ShopWise order has shipped", ago(900)
        ),
        Header("PhotoBox Demo <noreply@photobox-demo.test>", "Please verify your email", ago(2700)),
        Header('"FitTrack" <team@fittrack.test>', "Thanks for signing up to FitTrack", ago(1500)),
        Header('"FitTrack" <team@fittrack.test>', "Your weekly FitTrack digest", ago(30)),
        Header("Gamely <accounts@gamely.test>", "Confirm your Gamely account", ago(4200)),
        Header("Old Forum <admin@oldforum.test>", "Your account has been created", ago(5000)),
        Header("QuietList <news@quietlist.test>", "Welcome to the weekend sale!", ago(60)),
        Header("Travelr <hi@travelr.test>", "Activate your account", ago(800)),
        Header("Travelr <hi@travelr.test>", "Your booking confirmation", ago(700)),
    ]
