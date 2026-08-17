# budujemy - planer budowy/mieszkania/remontu

## Uruchomienie
1. pip install -r requirements.txt
2. python app.py
3. otwórz http://127.0.0.1:5000

WAŻNE: usuń domybudujesz.db przed uruchomieniem - struktura tabel się zmieniła
(nowe tabele Material, Task; nowa kolumna Item.will_change; inny domyślny
zestaw sekcji/pokoi).

## Co nowego w tej turze
- Typ projektu wpływa teraz na to, co się seeduje:
  - dom: pełne 5 sekcji (stan 0, surowy otwarty, surowy zamknięty, instalacje,
    wykończenie), instalacje = elektryczna+wodno-kanalizacyjna+ogrzewanie.
  - mieszkanie/remont: tylko 2 sekcje (instalacje, wykończenie), instalacje =
    tylko elektryczna+wodno-kanalizacyjna, bo reszta stanu budynku już istnieje.
- Instalacje w mieszkaniu/remoncie mają na starcie pytanie "czy będzie
  zmieniana?" (domyślnie nie). Jeśli przełączysz na "będzie zmieniana",
  podstrona dzieli się na 2 sekcje:
  - materiały: kafelki (nazwa, sklep, wycena, opis, link), edytowalne,
    usuwalne, z checkboxem "liczy się do budżetu" (3 kwadratowe przyciski:
    klucz/kosz/sakiewka, tak samo jak wszędzie indziej).
  - usługi: dokładnie ten sam mechanizm co w projekcie domu (materiał+
    robocizna, grupy, "wykonamy to sami") - budżet pozycji to materiały
    (zawsze sumowane) + widełki z usług (min-max alternatyw).
- Wykończenie: domyślne, stałe pokoje to teraz tylko strefa dzienna, kuchnia,
  łazienka (wcześniej było 5, w tym korytarz i sypialnia) - resztę dodaje
  użytkownik przez "+ dodaj więcej". Dotyczy to każdego typu projektu.
- Każdy pokój w wykończeniu ma teraz 4 sekcje: wykonawcy (jak wcześniej -
  usługi z tagami), zadania (prosta lista do zrobienia z checkboxem),
  produkty (kafelki materiałów - ten sam mechanizm co materiały w
  instalacjach), inspiracje (bez zmian).

## Model danych (dodatki)
- Item.will_change: None = nie dotyczy (dom, pokoje), False/True = instalacje
  w mieszkaniu/remoncie.
- Material: nazwa, sklep, wycena, opis, link, include_in_budget - używany
  zarówno w zakładce "materiały" instalacji jak i w "produktach" pokoju.
- Task: prosty tekst + done, per pokój.
- item_options()/segment_range() liczą teraz: materiały (zawsze sumowane) +
  widełki z usług (min/max alternatyw z grup wariantów), a dla pokoi:
  wyceny wykonawców + materiały.

## Czego jeszcze nie ma
- reklamy w wersji darmowej + subskrypcja 10 zł/mies bez reklam
- reset hasła przez e-mail, weryfikacja e-maila
- edycja nazwy/metrażu/budżetu domu po utworzeniu
- limit rozmiaru/kompresja uploadowanych zdjęć

## Poprawka: pokoje wg typu projektu
- dom: pełny, oryginalny zestaw 5 pokoi (korytarz, kuchnia, strefa dzienna,
  sypialnia, łazienka).
- mieszkanie/remont: 3 pokoje (strefa dzienna, kuchnia, łazienka).
- Kategorie inspiracji tworzą się automatycznie dopasowane do faktycznych
  pokoi danego typu (nie osobna, sztywna lista).
- "wykonamy to sami" usunięte z pozycji typu "urząd" (dokument+opłata) -
  tam nie ma sensu, zostaje przy materiał+wykonawca i szacowana wycena.

## Wdrożenie na Render

### Opcja A: jednym kliknięciem (Blueprint)
1. Wrzuć ten folder do repozytorium na GitHubie/GitLabie.
2. Na render.com: New → Blueprint → wskaż repo. Render odczyta `render.yaml`
   i sam utworzy usługę web + darmową bazę PostgreSQL, wygeneruje SECRET_KEY
   i podłączy DATABASE_URL automatycznie.
3. Kliknij Apply - gotowe.

### Opcja B: ręcznie
1. New → Web Service → wskaż repo.
2. Build Command: `pip install -r requirements.txt`
3. Start Command: `gunicorn app:app`
4. W Environment Variables ustaw:
   - `SECRET_KEY` - dowolny losowy ciąg znaków (np. `python -c "import secrets; print(secrets.token_hex(32))"`)
   - `DATABASE_URL` - jeśli chcesz trwałą bazę, dodaj osobno darmową bazę
     PostgreSQL w Render (New → PostgreSQL) i wklej tu jej "Internal
     Connection String". Bez tego apka spadnie na SQLite w lokalnym
     systemie plików kontenera - a ten **kasuje się przy każdym redeployu**,
     więc do produkcji koniecznie użyj PostgreSQL.

### Uwaga: zdjęcia inspiracji
Render ma efemeryczny system plików (poza płatnym "Persistent Disk") -
przesłane zdjęcia w `static/uploads/` znikną przy restarcie/redeployu
kontenera, nawet z podłączoną bazą PostgreSQL. Do trwałego przechowywania
zdjęć w produkcji docelowo warto podłączyć zewnętrzny storage (np. S3,
Cloudinary) - to nie jest jeszcze zrobione w tej wersji.

### Zmienne środowiskowe (podsumowanie)
- `SECRET_KEY` - wymagane w produkcji (inaczej sesje logowania są niebezpieczne)
- `DATABASE_URL` - opcjonalne; brak = SQLite lokalnie (nietrwałe na Render)
- `PORT` - Render ustawia automatycznie, nie trzeba nic robić
- `FLASK_DEBUG` - ustaw na `0` w produkcji (już w render.yaml)
