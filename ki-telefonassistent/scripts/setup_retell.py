"""Legt den Agenten bei Retell AI an und verbindet die Bird-Nummer.

Benötigte Umgebungsvariablen:
  RETELL_API_KEY     API-Key aus dem Retell-Dashboard
  TOOL_SERVER_URL    Öffentliche URL des Tool-Servers, z. B. https://lena-tools.example.com
  TOOL_TOKEN         Gleicher Token wie beim Tool-Server
Optional:
  RETELL_VOICE_ID    Stimme (ohne Angabe werden deutsche Stimmen aufgelistet und das Skript stoppt)
  RETELL_MODEL       LLM-Modell bei Retell (Standard: gpt-4.1)
  PHONE_NUMBER       Bird-Nummer im E.164-Format, z. B. +4368054014095 -> wird in Retell importiert
  BIRD_TRUNK_DOMAIN  SIP-Domain des Bird-Trunks (für ausgehende Anrufe / Weiterleitungen)
  BIRD_SIP_PASSWORD  Secret eines Bird-API-Keys mit voice:write, der am Trunk erlaubt ist

Hinweis: Die Endpunkte entsprechen der Retell-API (create-retell-llm,
create-agent, import-phone-number). Retell ändert Felder gelegentlich -
bei einem 4xx-Fehler gibt das Skript die Antwort von Retell aus.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests

API = "https://api.retellai.com"
ROOT = Path(__file__).resolve().parent.parent


def env(name: str, required: bool = True, default: str | None = None) -> str | None:
    value = os.environ.get(name, default)
    if required and not value:
        sys.exit(f"Umgebungsvariable {name} fehlt.")
    return value


def call(method: str, path: str, payload: dict | None = None) -> dict | list:
    r = requests.request(method, API + path, json=payload, timeout=30,
                         headers={"Authorization": f"Bearer {env('RETELL_API_KEY')}"})
    if r.status_code >= 400:
        sys.exit(f"Retell {method} {path} -> {r.status_code}: {r.text}")
    return r.json() if r.text else {}


def tool(name: str, description: str, properties: dict, required: list[str], speak: bool = True) -> dict:
    base = env("TOOL_SERVER_URL").rstrip("/")
    return {
        "type": "custom",
        "name": name,
        "description": description,
        "url": f"{base}/tools/{name}?token={env('TOOL_TOKEN')}",
        "speak_during_execution": speak,
        "speak_after_execution": True,
        "parameters": {"type": "object", "properties": properties, "required": required},
    }


def tools() -> list[dict]:
    s = {"type": "string"}
    return [
        tool("business_info", "Öffnungszeiten, Adresse, Leistungen, Preise, Parken, Zahlung, FAQ und heutiges Datum.",
             {}, [], speak=False),
        tool("check_availability", "Freie Startzeiten für eine Leistung finden. Ohne date: nächster freier Tag.",
             {"service": {**s, "description": "Gewünschte Leistung, z. B. Herrenhaarschnitt"},
              "date": {**s, "description": "Datum JJJJ-MM-TT (optional)"},
              "time_from": {**s, "description": "Frühester Beginn HH:MM (optional)"},
              "time_to": {**s, "description": "Spätester Beginn HH:MM (optional)"},
              "staff": {**s, "description": "Wunsch-Mitarbeiter (optional)"}},
             ["service"]),
        tool("book_appointment", "Termin verbindlich buchen. NUR nach ausdrücklicher Bestätigung des Anrufers.",
             {"service": s, "date": {**s, "description": "JJJJ-MM-TT"}, "time": {**s, "description": "HH:MM"},
              "customer_name": {**s, "description": "Vor- und Nachname"},
              "customer_phone": {**s, "description": "Nur angeben, wenn abweichend von der Anrufernummer"},
              "staff": s, "notes": s},
             ["service", "date", "time", "customer_name"]),
        tool("find_appointments", "Bestehende zukünftige Termine des Anrufers finden.",
             {"customer_phone": {**s, "description": "Nur wenn abweichend von der Anrufernummer"}}, []),
        tool("cancel_appointment", "Einen Termin stornieren. NUR nach Bestätigung.",
             {"booking_id": s, "date": {**s, "description": "JJJJ-MM-TT"},
              "customer_phone": {**s, "description": "Nur wenn abweichend von der Anrufernummer"}}, []),
    ]


def main() -> None:
    cfg = json.loads((ROOT / "config" / "salon_lena.json").read_text(encoding="utf-8"))
    voice = env("RETELL_VOICE_ID", required=False)
    if not voice:
        voices = call("GET", "/list-voices")
        print("Keine RETELL_VOICE_ID gesetzt. Verfügbare Stimmen (Auswahl):")
        for v in voices:
            print(f"  {v.get('voice_id'):40} {v.get('voice_name', ''):20} {v.get('provider', '')} {v.get('accent', '')}")
        sys.exit("Stimme auswählen, als RETELL_VOICE_ID setzen und erneut starten.")

    prompt = (ROOT / "agent" / "prompt_salon_de.md").read_text(encoding="utf-8")
    llm = call("POST", "/create-retell-llm", {
        "model": env("RETELL_MODEL", required=False, default="gpt-4.1"),
        "general_prompt": prompt,
        "begin_message": f"{cfg['business_name']}, hier ist {cfg['assistant_name']}, die digitale Assistentin. "
                         "Wie kann ich Ihnen helfen?",
        "general_tools": tools(),
    })
    print("LLM angelegt:", llm["llm_id"])

    agent = call("POST", "/create-agent", {
        "agent_name": f"{cfg['business_name']} Telefonassistent",
        "response_engine": {"type": "retell-llm", "llm_id": llm["llm_id"]},
        "voice_id": voice,
        "language": "de-DE",
        "interruption_sensitivity": 0.8,
        "enable_backchannel": True,
        "end_call_after_silence_ms": 30000,
        "max_call_duration_ms": 900000,
    })
    print("Agent angelegt:", agent["agent_id"])

    number = env("PHONE_NUMBER", required=False)
    if number:
        payload = {"phone_number": number, "inbound_agent_id": agent["agent_id"],
                   "nickname": cfg["business_name"]}
        trunk = env("BIRD_TRUNK_DOMAIN", required=False)
        if trunk:
            payload["termination_uri"] = trunk
            payload["sip_trunk_auth_username"] = "bird"
            payload["sip_trunk_auth_password"] = env("BIRD_SIP_PASSWORD")
            payload["outbound_agent_id"] = agent["agent_id"]
        call("POST", "/import-phone-number", payload)
        print("Nummer in Retell importiert:", number)

    print("\nFertig. Als Nächstes in Bird: Inbound am Trunk aktivieren, Gateway sip:sip.retellai.com anlegen,"
          " Nummer auf den Trunk routen (siehe README).")


if __name__ == "__main__":
    main()
