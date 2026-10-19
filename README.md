# Invest Radar

Osobisty skaner pomysłów inwestycyjnych: ceny + fundamenty + newsy/geopolityka → ocena AI → weryfikacja → sygnał
(KUP / DOKUPUJ / TRZYMAJ / OGRANICZ / UNIKAJ) osobno dla horyzontu długiego (IKE) i średniego.

> To narzędzie pomocnicze, **nie porada inwestycyjna**. Darmowe dane bywają opóźnione i dziurawe, a model się myli.
> Nie ma tu składania zleceń - decyzje i transakcje (Erste, XTB) robisz ręcznie.

## Start

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                   # wpisz ANTHROPIC_API_KEY
streamlit run app.py                                   # kliknij "Odśwież dane i sygnały"
```

Bez klucza API też działa (tryb: technika + fundamenty). Skan z konsoli / crona: `python scan.py` (`--no-ai`, `--no-verify`).
Testy logiki: `python -m unittest discover -s tests -t .`

## Jak liczony jest sygnał

1. **Technika** (SMA50/200, momentum 3/6 mies., RSI) → −1…+1; do tego zmienność i max. obsunięcie jako flagi ryzyka.
2. **Fundamenty** (P/E, ROE, marża, wzrost, zadłużenie; banki bez D/E) → −1…+1. ETF-y nie mają fundamentów -
   to się nie liczy jako "brakujące dane": waga fundamentów po prostu nie wchodzi do mianownika pewności.
3. **Newsy** z Google News → Claude ocenia sentyment i pewność; do tego **makro/geopolityka** daje tilt sektora.
4. Składowe łączone wagami zależnymi od horyzontu (`config.yaml`). Pewność spada przy braku zgodności lub
   brakujących danych, których instrument normalnie powinien mieć (np. newsy); nadal trzeba mieć min. 2 składowe
   (dla ETF-ów: technikę i newsy/makro), żeby sygnał był czymś innym niż TRZYMAJ.
5. **Weryfikacja AI** (sceptyczny recenzent) może tylko obniżyć pewność lub zamienić sygnał na TRZYMAJ - nigdy go wzmocnić.
6. **Insiderzy** to nakładka na score (domyślnie ±0.15), nie osobna składowa - większość spółek nie ma świeżych transakcji.
   Zakupy ważą więcej niż sprzedaże (insider kupuje z jednego powodu, sprzedaje z wielu); klaster zakupów kilku osób to
   najmocniejszy sygnał. Gdy insiderzy przeczą kierunkowi sygnału, spada pewność.
7. Sygnał o niskiej pewności albo oparty na <2 źródłach spada do TRZYMAJ.

Nagłówki newsów traktowane są jako dane niezaufane (ochrona przed prompt injection), a odpowiedzi AI są walidowane i obcinane do zakresów.

## Insiderzy

- **USA:** formularze SEC Form 4 z EDGAR (darmowe). Tylko zakupy/sprzedaże na wolnym rynku (kody P/S). Ustaw w `.env`
  `SEC_USER_AGENT="Imię Nazwisko email@domena.pl"` - SEC wymaga identyfikacji; bez tego USA jest pomijane.
- **GPW:** wersja **przybliżona** - klasyfikacja nagłówków newsów o transakcjach osób zarządzających (art. 19 MAR).
  Nie jest to oficjalny rejestr, więc taki sygnał ma o połowę mniejszą wagę, a w dashboardzie jest tak oznaczony.
  Docelowo warto podpiąć oficjalne źródło (ESPI/EBI) po sprawdzeniu, co jest dostępne.
- Dane są opóźnione (Form 4: do 2 dni roboczych), więc to kontekst, nie przewaga czasowa. Wyłączysz: `insiders: false`.

## Alerty i automatyczny skan

`python scan.py --notify` robi skan i wysyła wiadomość **tylko gdy zmienił się sygnał** względem poprzedniego skanu
(nowy KUP/DOKUPUJ/OGRANICZ/UNIKAJ o pewności ≥ 40% albo wygaśnięcie takiego sygnału). Pierwszy skan niczego nie wysyła.
Kanały w `.env`: Telegram (bot z @BotFather + chat id) i/lub e-mail (Gmail: hasło aplikacji). Progi w `config.yaml → alerts`.

Harmonogram (raz dziennie rano, po sesji europejskiej lub przed otwarciem GPW - jak wolisz):

```bash
# Linux/macOS (crontab -e) - codziennie 8:30, pn-pt
30 8 * * 1-5  cd /sciezka/do/invest-radar && .venv/bin/python scan.py --notify >> data/scan.log 2>&1
```

```bat
:: Windows (Harmonogram zadań z wiersza poleceń) - codziennie 8:30
schtasks /Create /SC DAILY /ST 08:30 /TN "InvestRadar" /TR "cmd /c cd /d C:\sciezka\invest-radar && .venv\Scripts\python.exe scan.py --notify >> data\scan.log 2>&1"
```

Komputer musi być włączony o tej porze. Skan wykonuje wiele zapytań (i wywołań API Claude), więc na początek uruchom go ręcznie.

## Dostosuj

- `config.yaml` → watchlista (symbole Yahoo: `.WA` GPW, `.DE`, `.AS`, `.L`), wagi, zapytania makro. **Sprawdź dostępność
  instrumentów (zwłaszcza ETF-ów pod IKE) w XTB/Erste** - przykładowe tickery są poglądowe.
- Dane i historia sygnałów: `data/radar.db` (SQLite).

## Pomysły na dalej

Backtest sygnałów na historii, oficjalne źródło insiderów dla GPW, wielkość pozycji vs. portfel, import portfela z brokera.
