# PrintVault Helper: Einrichtung

Der Helper lädt einen von PrintVault freigegebenen Einmaljob herunter und startet ihn nach lokaler Bestätigung im konfigurierten Slicer. Er erhält weder Serverpfade noch Shell-Befehle.

## Windows: Schritt für Schritt

1. In **PrintVault → Einstellungen → Helper** auf **Helper für Windows herunterladen** klicken.
2. ZIP in einen privaten Ordner entpacken, z. B. `C:\Users\<Name>\PrintVaultHelper`.
3. Python 3.10+ installieren. Der Release-Helper benötigt Python; auf dem PrintVault-Testclient wurde Python 3.12 geprüft.
4. `config.example.json` als `config.json` kopieren.
5. In PrintVault auf **Kopplungscode erzeugen** klicken. Der Code läuft ab.
6. `setup-windows.bat` per Doppelklick starten. Es startet den Setup-Assistenten mit der dafür nötigen lokalen PowerShell-Ausführungsrichtlinie. Keine globale Policy-Änderung nötig.

7. Wenn du die Registrierung manuell ausführen willst, PowerShell im entpackten Ordner öffnen:

   ```powershell
   .\printvault-helper.bat --register --origin https://DEIN-PRINTVAULT --pairing-code DEIN-CODE --device-name Mein-PC
   ```

8. Die einmalige Ausgabe sicher lokal ablegen. Sie enthält `user_id`, `device_id` und `device_credential`. PrintVault zeigt das Credential später nicht erneut.
8. `config.json` bearbeiten:
   - `origin` auf deine HTTPS-PrintVault-Adresse setzen.
   - `user_id` und `device_id` aus Schritt 7 eintragen.
   - Für OrcaSlicer den vorhandenen Pfad setzen: `C:\Program Files\OrcaSlicer\orca-slicer.exe`.
9. Credential nur in der aktuellen PowerShell-Session setzen, niemals in ein Ticket, einen Chat oder das Git-Repository kopieren:

   ```powershell
   $env:PRINTVAULT_HELPER_TOKEN = 'DEVICE-CREDENTIAL-AUS-SCHRITT-7'
   ```

10. Wenn PrintVault eine `printvault://open?...`-Adresse oder eine `request_id:profil_id` ausgibt, ausführen:

   ```powershell
   .\printvault-helper.bat 'request-id:orca'
   ```

11. Der Helper zeigt die lokale Startbestätigung. Nur mit `LAUNCH` bestätigen. Danach startet OrcaSlicer mit einer privat zwischengespeicherten, SHA-256-geprüften Kopie.

## Linux

1. Unter **Einstellungen → Helper** das Linux-ZIP laden und entpacken.
2. `config.example.json` nach `config.json` kopieren und die Werte wie in Schritten 5–9 oben konfigurieren.
3. `chmod +x printvault-helper` ausführen.
4. Token nur für die aktuelle Shell exportieren und dann starten:

   ```bash
   export PRINTVAULT_HELPER_TOKEN='DEVICE-CREDENTIAL-AUS-SCHRITT-7'
   ./printvault-helper 'request-id:orca'
   ```

## Sicherheit und Fehlerbehebung

- Akzeptiert werden nur HTTPS-Origins und Jobs, die an den registrierten User, das Gerät und das Slicerprofil gebunden sind.
- Der Job ist einmalig und maximal fünf Minuten gültig.
- Bei fehlendem oder abgelaufenem Token: neues Gerät registrieren oder in PrintVault unter **Helper** das alte Gerät widerrufen und neu koppeln.
- Ohne Helper bleibt der normale authentifizierte Datei-Download verfügbar.
