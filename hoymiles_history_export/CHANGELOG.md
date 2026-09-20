# Changelog

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
