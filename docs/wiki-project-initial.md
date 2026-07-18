# PrintVault

<!-- HERMES:PRINTVAULT:STATUS:START -->
## Aktueller Stand

**Status:** Implementierung läuft.

PrintVault ist eine selbst gehostete Webanwendung zur Organisation von 3D-Druck-Dateien. Geplant sind beschreibbare Modell-, Projekt- und Archiv-Bibliotheken, Metadaten/Tags/Suche, STL/OBJ/3MF-Vorschau, OIDC-only Zugriff und ein optionaler lokaler Slicer-Helper.
<!-- HERMES:PRINTVAULT:STATUS:END -->

## Sicherheitsmodell

- OIDC-only: kein lokaler Passwort-Login, keine Gastkonten.
- Rollen: `printvault_admin`, `printvault_editor`, `printvault_viewer`.
- Schreibzugriffe bleiben innerhalb explizit konfigurierter Bibliotheks-Roots.
- Archivieren vor endgültigem Löschen; sensible Aktionen erhalten Audit-Einträge.
- Zugangsdaten gehören in root-only Secret-Dateien/Docker-Secrets, nie in Git oder Wiki.

## Betrieb

- Deployment: Docker/Dockhand.
- Reverse Proxy erreicht Container über gemeinsames externes Docker-Netzwerk.
- Der App-Container veröffentlicht keine Host-Ports.
- Primäre Datenbank: MariaDB; SQLite bleibt für lokale Entwicklung und Tests unterstützt.

## Dokumentationsindex

Die versionierte Datei `docs/index.json` im Projekt enthält kurze, thematische Verweise auf Plan, Deployment, Betrieb, Sicherheit und Slicer-Helper. Sie erlaubt gezielten Dokumentenzugriff ohne vollständige Wiki-Seiten zu laden.

## Offene Umsetzungspakete

- Backend: Datenbank, OIDC-Session, RBAC, sichere Dateisystemoperationen, Indexer.
- Frontend: i18n-Locale-Dateien, Dark/Light/System Theme, Bibliotheks-UI, Viewer.
- Deployment: Dockhand-Stack, Reverse-Proxy-Route, OIDC-Client, End-to-End-Tests.
