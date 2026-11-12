"""Trimiterea codurilor de verificare prin SMS.

Furnizorul se configurează în .env (SMS_FURNIZOR):
  - "smslink"  – SMSLink.ro: SMSLINK_CONNECTION_ID, SMSLINK_PASSWORD
  - "twilio"   – Twilio: TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM
  - "consola"  – (implicit, doar pentru demo) codul NU se trimite pe telefon; apare în jurnalul serverului
                 și în fișierul date/sms_demo.log. Nu oferă securitate reală.
Verificați documentația furnizorului ales înainte de punerea în producție.
"""
import logging
import os
import re

import requests
from django.conf import settings
from django.utils import timezone

log = logging.getLogger("platforma.sms")


def furnizor():
    return os.environ.get("SMS_FURNIZOR", "consola").lower()


def e_mod_demo():
    return furnizor() == "consola"


def normalizeaza(numar):
    n = re.sub(r"[^\d+]", "", numar or "")
    if n.startswith("07") and len(n) == 10:
        return n, "+4" + n
    if n.startswith("+407"):
        return "0" + n[3:], n
    return n, n


def ascunde(numar):
    local, _ = normalizeaza(numar)
    return local[:4] + "***" + local[-3:] if len(local) >= 7 else "***"


def _explica_smslink(raspuns):
    """Mesaj de eroare SMSLink fără date sensibile (SMSLink poate repeta parola în răspuns)."""
    text = raspuns.strip()
    for secret in (os.environ.get("SMSLINK_PASSWORD"), os.environ.get("SMSLINK_CONNECTION_ID")):
        if secret:
            text = text.replace(secret, "***")
    text = re.sub(r"'[^']*'", "'***'", text)
    if ";7;" in text or "not approved" in text.lower():
        return ("parola conexiunii nu este acceptată. În .env trebuie trecută parola conexiunii SMS Gateway "
                "(din SMSLink → SMS Gateway → Configuration & Settings), nu parola contului.")
    return text[:200]


def trimite(numar, text):
    """Trimite SMS-ul; ridică RuntimeError dacă furnizorul refuză."""
    local, international = normalizeaza(numar)
    f = furnizor()
    if f == "smslink" and not (os.environ.get("SMSLINK_CONNECTION_ID") and os.environ.get("SMSLINK_PASSWORD")):
        raise RuntimeError("SMSLink nu este configurat: completați SMSLINK_CONNECTION_ID și SMSLINK_PASSWORD în fișierul .env.")
    if f == "smslink":
        r = requests.get("https://secure.smslink.ro/sms/gateway/communicate/index.php", timeout=20, params={
            "connection_id": os.environ["SMSLINK_CONNECTION_ID"], "password": os.environ["SMSLINK_PASSWORD"],
            "to": local, "message": text})
        if r.status_code != 200 or "ERROR" in r.text.upper():
            raise RuntimeError("SMSLink a refuzat mesajul: " + _explica_smslink(r.text))
    elif f == "twilio":
        sid = os.environ["TWILIO_ACCOUNT_SID"]
        r = requests.post(f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json", timeout=20,
                          auth=(sid, os.environ["TWILIO_AUTH_TOKEN"]),
                          data={"To": international, "From": os.environ["TWILIO_FROM"], "Body": text})
        if r.status_code >= 300:
            raise RuntimeError(f"Twilio a refuzat mesajul: {r.text[:200]}")
    else:  # mod demo: fără SMS real
        linie = f"{timezone.now():%Y-%m-%d %H:%M:%S} SMS către {local}: {text}"
        log.warning(linie)
        print(linie, flush=True)
        try:
            with open(settings.BASE_DIR / "date" / "sms_demo.log", "a", encoding="utf-8") as fh:
                fh.write(linie + "\n")
        except OSError:
            pass
