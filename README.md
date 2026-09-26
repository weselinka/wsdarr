# wsdarr

**Webshare.cz jako indexer a download client pro *arr stack** (Sonarr, Radarr, Prowlarr).

wsdarr se vůči *arr aplikacím tváří jako dvě standardní věci, které umí nativně:

* **Newznab indexer** – Prowlarr ho přidá jako „Generic Newznab“ a sám ho rozdistribuuje do
  Sonarr/Radarr (nebo ho přidáš přímo do Sonarr/Radarr).
* **SABnzbd download client** – Sonarr/Radarr mu pošlou vybraný release, wsdarr ho stáhne
  z Webshare a Sonarr/Radarr ho přes *Completed Download Handling* automaticky naimportují.

Výběr nejlepšího releasu (quality profily, custom formaty, preferovaný jazyk…) tedy zůstává
plně na Sonarru/Radarru – wsdarr jen zajistí, aby výsledky z Webshare měly názvy, kterým rozumí.

```
Sonarr/Radarr ──(Newznab, přes Prowlarr nebo napřímo)──► wsdarr  /newznab/api?t=tvsearch&tvdbid=81189&season=1&ep=2
     wsdarr: ID ─► názvy (Sonarr/Radarr + TMDB cs-CZ) ─► Webshare hledání ─► filtr (správný díl/rok)
             ─► přejmenování na „scene“ název:  Breaking.Bad.S01E02.1080p.BluRay.x264.CZ-WS
Sonarr vybere release podle profilu ─► stáhne „NZB“ (obálka s Webshare identem) ─► pošle do „SABnzbd“ = wsdarr
wsdarr stáhne soubor do /downloads/complete/tv/<release>/<release>.mkv ─► Sonarr naimportuje a položku smaže
```

## Rychlý start (Docker)

1. Zkopíruj [`docker-compose.example.yml`](docker-compose.example.yml), vyplň Webshare účet a API
   klíče Sonarr/Radarr/Prowlarr (*Settings → General → API Key* v každé aplikaci).
2. V Prowlarru měj v *Settings → Apps* přidané Sonarr a Radarr (to je běžné nastavení Prowlarru).
3. `docker compose up -d`
4. S `AUTO_SETUP=true` se wsdarr při startu sám zaregistruje (viz níže). Jinak spusť
   `docker compose exec wsdarr wsdarr setup` nebo tlačítko *Spustit auto-setup* ve web UI
   (`http://<host>:9797/`).

Webshare **VIP účet** je silně doporučený – bez VIP je stahování pomalé a omezené.

### Auto-setup

`wsdarr setup` (nebo tlačítko v UI, nebo `AUTO_SETUP=true`) přes API aplikací:

1. v Sonarr i Radarr vytvoří download client **SABnzbd** jménem `wsdarr`
   (host/port z `PUBLIC_URL`, URL base `sabnzbd`, kategorie `tv` / `movies`),
2. s Prowlarrem: vytvoří v Prowlarru indexer **Generic Newznab** „Webshare (wsdarr)“, spustí
   synchronizaci aplikací a indexerům, které Prowlarr vytvoří v Sonarr/Radarr, nastaví
   *Download Client = wsdarr* – NZB obálky z Webshare tak nikdy neskončí ve skutečném SABnzbd/NZBGet,
3. bez Prowlarru: vytvoří Newznab indexer přímo v Sonarr/Radarr (opět navázaný na klienta wsdarr),
4. volitelně přidá klienta `wsdarr` i do Prowlarru (kategorie `prowlarr`) pro ruční grab z Prowlarru.

Je idempotentní – lze ho spouštět opakovaně, existující položky jen zkontroluje / opraví.

### Ruční nastavení

| Kde | Co |
| --- | --- |
| Prowlarr → Indexers → *Generic Newznab* (nebo Sonarr/Radarr → Indexers → *Newznab*) | URL `http://wsdarr:9797/newznab`, API Path `/api`, API Key = klíč wsdarr |
| Sonarr/Radarr → Download Clients → *SABnzbd* | Host `wsdarr`, Port `9797`, URL Base `sabnzbd`, API Key = klíč wsdarr, Category `tv` / `movies` |
| Sonarr/Radarr → indexer wsdarr → *Download Client* | `wsdarr` (hlavně pokud používáš i opravdový usenet) |

API klíč wsdarr: `WSDARR_API_KEY`, jinak se vygeneruje při prvním startu – zobrazí ho
`wsdarr apikey` nebo stránka *Přehled* ve web UI.

### Cesty

wsdarr hlásí stažené soubory v `DOWNLOAD_DIR/complete/<kategorie>/<release>/`. Sonarr/Radarr
musí tuto cestu vidět **pod stejnou cestou** (sdílený volume, jako v ukázkovém compose), nebo
nastav *Settings → Download Clients → Remote Path Mappings*.

## Jak wsdarr hledá a pojmenovává

* **Z ID na názvy.** Sonarr posílá `tvdbid` + `season` + `ep`, Radarr `tmdbid`/`imdbid`. wsdarr
  z knihovny Sonarr/Radarr vezme název, rok a alternativní názvy, z TMDB (volitelné,
  `TMDB_API_KEY`) doplní **české a slovenské názvy** – na Webshare je většina souborů pojmenovaná česky.
* **Hledání na Webshare.** Pro díl např. `Perníkový táta S01E02`, `Pernikovy tata 1x02`,
  `Breaking Bad S01E02` a samotné názvy (se stránkováním); výsledky se cachují.
* **Filtr.** Z názvu souboru se parsuje (guessit + česká pravidla) název, rok, série/díl, rozlišení,
  zdroj, kodeky a jazyk (`CZ dabing`, `CZ titulky`, `SK`, `CZtit` …). Soubory jiného dílu/roku,
  samply, heslem chráněné, ne-video a příliš malé soubory se zahodí; duplicitní uploady se sloučí.
* **Pojmenování.** Shodný soubor dostane název, který Sonarr/Radarr namapují zpět:

  | Soubor na Webshare | Release pro Sonarr/Radarr |
  | --- | --- |
  | `Perníkový táta S01E02 (CZ) 720p.avi` | `Breaking.Bad.S01E02.720p.CZ-WS` |
  | `Breaking.Bad.S01E02.720p.WEB-DL.CZ.titulky.mkv` | `Breaking.Bad.S01E02.720p.WEB-DL.CZ.SUBS-WS` |
  | `Pán prstenů - Návrat krále (2003) 1080p CZ dabing.mkv` | `The.Lord.of.the.Rings.The.Return.of.the.King.2003.1080p.CZ-WS` |
  | `Simpsonovi 12x05 CZ.avi` | `The.Simpsons.S12E05.SDTV.CZ-WS` |

  `CZ` / `SK` = český / slovenský zvuk (Sonarr/Radarr ho rozpoznají jako jazyk *Czech* / *Slovak*),
  `CZ.SUBS` = jen české titulky. Do Newznab atributů jde i `language`, `size`, `tvdbid`/`imdb`/`tmdbid`.
  Původní název souboru je v popisu releasu a v UI.
* **Soubory bez značek kvality** by Sonarr/Radarr vyhodnotily jako *Unknown* a výchozí profily je
  odmítnou. wsdarr jim proto (výchozí `UNKNOWN_QUALITY=extension`) přidá kvalitu, kterou by jim
  Sonarr/Radarr samy přiřadily podle přípony (`.mkv` → HDTV-720p, `.avi`/`.mp4` → SDTV, …).
  `UNKNOWN_QUALITY=keep` to vypne.

### Doporučené nastavení Sonarr/Radarr

* V quality profilu povol kvality, které na Webshare reálně jsou (často **SDTV**, **HDTV-720p/1080p**,
  **WEBDL**, **Bluray**).
* Pro preferenci českého dabingu vytvoř *Settings → Custom Formats → +* s podmínkou
  *Language: Czech* a dej mu v profilu kladné skóre (případně druhý formát *Language: Slovak*).
  V Radarru nastav v profilu *Language* na *Any*, jinak odmítne releasy, které nejsou v původním jazyce.
* *Completed Download Handling* nech zapnuté, u klienta wsdarr ideálně *Remove Completed*.

## Konfigurace

Proměnné prostředí (nebo `/config/config.yml` se stejnými klíči malými písmeny; YAML umožňuje
i více instancí, např. `sonarr: [{name: sonarr-4k, url: ..., api_key: ..., category: tv-4k}]`).

| Proměnná | Výchozí | Popis |
| --- | --- | --- |
| `WEBSHARE_USERNAME`, `WEBSHARE_PASSWORD` | – | Webshare účet |
| `PUBLIC_URL` | `http://wsdarr:9797` | adresa, na které wsdarr vidí Sonarr/Radarr/Prowlarr (NZB odkazy, auto-setup) |
| `WSDARR_API_KEY` | vygenerováno | API klíč pro Newznab i SABnzbd API |
| `SONARR_URL`, `SONARR_API_KEY` | – | metadata + auto-setup |
| `RADARR_URL`, `RADARR_API_KEY` | – | metadata + auto-setup |
| `PROWLARR_URL`, `PROWLARR_API_KEY` | – | auto-setup přes Prowlarr |
| `TMDB_API_KEY` / `TMDB_TOKEN` | – | české názvy z TMDB (doporučeno) |
| `TMDB_LANGUAGE` | `cs-CZ` | jazyk lokalizovaných názvů |
| `AUTO_SETUP` | `false` | auto-setup při každém startu |
| `DOWNLOAD_DIR` | `/downloads` | kořen pro `incomplete/` a `complete/` |
| `CONFIG_DIR` | `/config` | databáze, token, `config.yml` |
| `TV_CATEGORY`, `MOVIE_CATEGORY` | `tv`, `movies` | kategorie download clienta |
| `MAX_CONCURRENT_DOWNLOADS` | `2` | souběžná stahování |
| `SPEED_LIMIT_KBPS` | `0` | limit rychlosti (0 = bez limitu) |
| `DOWNLOAD_RETRIES` | `3` | pokusy před nahlášením chyby (Sonarr pak zkusí jiný release) |
| `MIN_FILE_SIZE_MB` | `50` | menší soubory se ignorují |
| `SEARCH_MAX_PAGES` | `3` | stránek (po 100) na jeden obecný dotaz |
| `MAX_ALT_TITLES` | `4` | kolik názvů se použije k hledání |
| `MATCH_THRESHOLD` | `88` | minimální shoda názvu (0–100) |
| `UNKNOWN_QUALITY` | `extension` | `extension` / `keep` (viz výše) |
| `RSS_MODE` | `recent` | `recent` = RSS vrací nejnovější videa z Webshare, `off` = nic |
| `UI_USERNAME`, `UI_PASSWORD` | – | basic auth pro web UI |
| `PUID`, `PGID`, `UMASK` | `1000`, `1000`, `002` | vlastník stažených souborů (Docker) |

## Web UI

`http://<host>:9797/` – přehled (Webshare účet a VIP dny, stav spojení, volné místo, údaje pro
ruční napojení), fronta a historie (pauza, smazání, opakování), ruční hledání (ukazuje i jak release
uvidí Sonarr/Radarr, s možností stáhnout do kategorie) a nastavení (auto-setup, test spojení).

## CLI

```
wsdarr serve              # web server (výchozí)
wsdarr setup              # auto-setup v Prowlarr/Sonarr/Radarr
wsdarr search "Breaking Bad S01E02" [--kind tv|movie]   # hledání jako Sonarr/Radarr (ladění)
wsdarr apikey             # vypíše API klíč
```

## Omezení a co ověřit na reálném účtu

* Denní (datumové) seriály a anime s absolutním číslováním zatím nejsou podporované.
* Webshare API dokumentace nebyla při vývoji dostupná, implementace vychází z existujících
  klientů. Na reálném účtu ověř: přihlášení (*Přehled* ukáže VIP dny), hledání (*Hledání* v UI),
  že test indexeru v Prowlarru/Sonarru projde (RSS dotaz bez hledaného textu musí něco vrátit),
  stažení a import jednoho dílu a navázání přerušeného stahování (restart kontejneru během stahování).
* Tvar dotazů na Webshare (`název S01E02` / `název 1x02` / samotný název) je potřeba doladit podle
  toho, jak Webshare vyhledávání reálně tokenizuje – příkaz `wsdarr search` ukáže, co se najde.

## Vývoj

```
pip install -e ".[dev]"
pytest            # unit testy + celý tok proti falešnému Webshare a falešným *arr API
ruff check src tests && ruff format --check src tests
```

`tests/fake_webshare.py` je spustitelný stub Webshare API (`python tests/fake_webshare.py`
a `WEBSHARE_BASE_URL=http://127.0.0.1:9999/api/`) pro lokální end-to-end zkoušky se skutečnými
Sonarr/Radarr/Prowlarr kontejnery.
