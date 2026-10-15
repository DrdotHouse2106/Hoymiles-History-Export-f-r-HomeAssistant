# Changelog

## 2.2.0

- Watchdog im Live-Gateway: Macht die Hauptschleife länger als ein
  Poll-Intervall + 10 Minuten keinen Fortschritt oder kommt mindestens eine
  Stunde (bzw. 4 Poll-Intervalle) lang nichts bei MQTT an, schreibt das
  Add-on die Stacktraces aller Threads ins Log und startet den Gateway
  neu. Ein Hänger kostet so nur noch einen Neustart statt tagelang
  fehlender Daten, und das Log zeigt, *wo* es gehangen hat.
- `run.sh` startet den Gateway-Prozess nach jedem Ende automatisch neu
  (ohne den Backfill zu wiederholen).
- MQTT: Verbindungsaufbau und Reconnects laufen nur noch im
  paho-Netzwerk-Thread. Vorher rief die Hauptschleife nach einem
  Disconnect erneut `connect()` auf, während paho im Hintergrund selbst
  reconnectete, und beide griffen auf denselben Socket zu.
- MQTT: Publishes warten (max. 15 s) auf das tatsächliche Senden und melden
  einen Fehler, statt still in eine tote Verbindung zu queuen. paho-Logging
  ist aktiv, damit Fehler in Callbacks nicht mehr lautlos den
  Netzwerk-Thread beenden.
- Discovery-Configs werden nach jedem MQTT-Reconnect erneut gesendet.
- `faulthandler` aktiv: Auch harte Abstürze in C-Erweiterungen (ssl,
  argon2) hinterlassen einen Stacktrace.
- paho-mqtt auf 2.1.0 gepinnt.

## 2.1.0

- MQTT-Discovery-Konfiguration für die Plant-Sensoren ergänzt (retained
  veröffentlicht beim Start des Live-Gateways). Ohne sie blieben die
  Entitäten nach einem Home-Assistant-Neustart dauerhaft "unavailable",
  obwohl die Rohdaten nachweislich weiter ankamen - Home Assistant wusste
  schlicht nicht mehr, wie es die Nachrichten den Entitäten zuordnen soll,
  weil diese Konfiguration normalerweise nur einmalig (retained) vom
  ursprünglichen Add-on gesetzt wurde und verloren ging.

## 2.0.0

- Neuer optionaler Live-Gateway-Modus (`enable_live_gateway`) als Ersatz
  für das fehlerhafte Original-"HoyMiles Solar Gateway"-Add-on (fehlendes
  Netzwerk-Timeout, 16+ Stunden Fehlschlaf bei MQTT-Auth-Fehlern)

## 1.1.0

- Deutsche und englische Feld-Beschriftungen/Hilfetexte ergänzt (statt
  roher Konfigurationsschlüssel wie `hoymiles_user`)
- Format-Validierung für `start_date`/`end_date` (JJJJ-MM-TT) ergänzt, um
  Tippfehler zu verhindern

## 1.0.0

- Erste Veröffentlichung: Backfill fehlender Hoymiles-Solar-Historie über
  die S-Miles Cloud, mit automatischer Geräte-Erkennung und automatischem
  Anschluss an bestehende Daten
