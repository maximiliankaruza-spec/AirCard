# KI-Telefonassistent „Lena“ (Demo: Friseursalon)

Ein KI-Agent nimmt Anrufe an, prüft freie Termine, bucht, verschiebt und storniert.

```
Anrufer ──► Bird-Nummer ──► Bird SIP-Trunk ──► Retell AI (Stimme + KI)
                                                   │
                                                   ▼  während des Gesprächs
                                         Tool-Server (dieses Projekt)
                                         server/app.py → Kalender
```

| Teil | Wer | Status |
|---|---|---|
| Telefonie | **Bird** | Trunk `spt_01m50e418we1cv2fwnhy8jqyxz` vorhanden, Inbound noch aus, **noch keine Nummer** |
| Stimme + KI | **Retell AI** | Account fehlt noch |
| Gesprächslogik | `agent/prompt_salon_de.md` | fertig |
| Kalender / Buchung | `server/` | fertig, getestet (Demo-Kalender als JSON) |
| Salon-Daten | `config/salon_lena.json` | Beispieldaten, pro Kunde anpassen |

## Lokal testen

```bash
python3 -m unittest discover -s tests        # Tests
TOOL_TOKEN=geheim python3 server/app.py      # Server auf :8080
curl -XPOST 'localhost:8080/tools/check_availability?token=geheim' \
     -d '{"args":{"service":"Herrenhaarschnitt"}}'
```

## Live schalten: Schritt für Schritt

### 1. Tool-Server online stellen
Retell muss den Server über HTTPS erreichen. Einfachste Optionen: Railway, Render oder Fly.io (Python-App,
Startbefehl `python3 server/app.py`, Variablen `TOOL_TOKEN` und `PORT`). Für einen schnellen Test reicht
auch `cloudflared tunnel --url http://localhost:8080` auf deinem Rechner.

> Achtung: Der Demo-Kalender speichert in `data/bookings.json`. Auf Hosting ohne festen Speicher gehen
> Buchungen bei einem Neustart verloren. Für echte Kunden wird auf Google Calendar / Cal.com umgestellt.

### 2. Retell-Account
1. Auf retellai.com registrieren und einen API-Key erstellen.
2. Agent anlegen:
   ```bash
   export RETELL_API_KEY=...  TOOL_SERVER_URL=https://dein-server  TOOL_TOKEN=geheim
   python3 scripts/setup_retell.py                    # listet Stimmen auf
   export RETELL_VOICE_ID=...                         # deutsche Stimme wählen
   PHONE_NUMBER=+43... python3 scripts/setup_retell.py
   ```
3. Im Retell-Dashboard mit „Test Call“ im Browser ausprobieren, auch ohne Telefonnummer.

### 3. Bird: Nummer und Weiterleitung zu Retell
1. **Nummer kaufen.** Deutsche Nummern mit Sprachfunktion hat Bird gerade **nicht** im Angebot. Verfügbar
   sind z. B. österreichische (+43 680…) und niederländische Nummern, jeweils mit Eigentümer-Registrierung.
   Alternative: eine deutsche Nummer bei sipgate o. ä. und Weiterleitung auf die Bird- bzw. Retell-Nummer.
2. **Inbound am Trunk aktivieren** (`inbound_enabled: true`).
3. **Gateway anlegen:** `sip:sip.retellai.com`, Priorität 0, Zielformat E.164 (`+{number}`).
4. **Nummer auf den Trunk routen** (`route: trunk`, `trunk_id: spt_01m50e418we1cv2fwnhy8jqyxz`).
5. Die gleiche Nummer muss in Retell importiert sein (Schritt 2), sonst weiß Retell nicht, welcher Agent antwortet.

Die Schritte 2 bis 4 kann Claude direkt über die Bird-Verbindung erledigen.

### 4. Optional: ausgehende Anrufe / Weiterleitung an Menschen
Damit Retell über Bird hinaustelefonieren kann:
- Bird-API-Key mit Berechtigung `voice:write` erstellen und am Trunk unter „Allowed API keys“ eintragen.
- In Retell beim Import: `termination_uri` = Trunk-Domain, Benutzer `bird`, Passwort = Key-Secret
  (`BIRD_TRUNK_DOMAIN` / `BIRD_SIP_PASSWORD` im Setup-Skript).

## Neuen Kunden anlegen
1. `config/salon_lena.json` kopieren, Name, Öffnungszeiten, Leistungen, Mitarbeiter und FAQ anpassen.
2. Im Prompt den Namen und die Branche anpassen.
3. Tool-Server mit `BUSINESS_CONFIG=config/neuer_kunde.json` starten, Setup-Skript ausführen.

## Rechtliches (Kurzfassung)
- Der Agent stellt sich als **digitale Assistentin** vor (EU AI Act, Transparenzpflicht).
- Mit jedem Kunden einen **AVV** schließen, Datenschutzhinweis ergänzen.
- Gesprächsaufzeichnung nur mit Hinweis bzw. Einwilligung.
