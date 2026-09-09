from flask import Flask, render_template, request, redirect, url_for, abort
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_wtf import CSRFProtect
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from markupsafe import Markup
import os, re, unicodedata, uuid
from icon_data import ICON_PATHS

basedir = os.path.abspath(os.path.dirname(__file__))
UPLOAD_DIR = os.path.join(basedir, "static", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)
ALLOWED_PHOTO_EXT = {"png", "jpg", "jpeg", "gif", "webp"}

basedir = os.path.abspath(os.path.dirname(__file__))
app = Flask(__name__)

database_url = os.environ.get("DATABASE_URL")
if database_url:
    # Render (i Heroku) czasem podaja "postgres://", SQLAlchemy 2.x wymaga "postgresql://"
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    app.config["SQLALCHEMY_DATABASE_URI"] = database_url
else:
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + os.path.join(basedir, "domybudujesz.db")

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-klucz-zmien-w-produkcji")

# zabezpieczenia ciasteczek sesji
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("FLASK_DEBUG", "1") != "1"  # tylko HTTPS w produkcji

# limit rozmiaru requestu/uploadu (10 MB) - chroni przed zapchaniem dysku/pamieci
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024

db = SQLAlchemy(app)
csrf = CSRFProtect(app)

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

limiter = Limiter(get_remote_address, app=app, default_limits=["200 per hour"], storage_uri="memory://")

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"
login_manager.login_message = "zaloguj się, żeby zobaczyć tę stronę"


LEGACY_ICON_ALIASES = {"brick": "wall", "radiator": "temperature"}


def icon(name, size=18, cls=""):
    """Zwraca inline SVG (bez zewnetrznych plikow/fontow) dla danej ikony Tabler."""
    if name and name.startswith("ti-"):
        name = name[3:]  # kompatybilnosc ze starymi rekordami sprzed przejscia na inline SVG
    name = LEGACY_ICON_ALIASES.get(name, name)  # kompatybilnosc z ikonami zmienionymi po drodze (np. brick->wall)
    paths = ICON_PATHS.get(name, "")
    class_attr = f' class="{cls}"' if cls else ""
    return Markup(
        f'<svg{class_attr} xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
        f'viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
        f'stroke-linecap="round" stroke-linejoin="round" style="display:inline-block;vertical-align:-4px;">'
        f'{paths}</svg>'
    )


app.jinja_env.globals["icon"] = icon


def clamp_number(raw_value, min_val=0.0, max_val=100_000_000.0, default=0.0):
    """Bezpiecznie parsuje liczbe z formularza: nieprawidlowe/puste -> default,
    ujemne/absurdalnie duze -> przycina do zakresu. Chroni przed 500 na smieciowych
    danych i przed ujemnymi/gigantycznymi wartosciami omijajacymi walidacje HTML."""
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        return default
    if value != value:  # NaN
        return default
    if value < min_val:
        return min_val
    if value > max_val:
        return max_val
    return value


def clamp_int(raw_value, min_val=0, max_val=100000, default=0):
    try:
        value = int(float(raw_value))
    except (TypeError, ValueError):
        return default
    return max(min_val, min(max_val, value))


def format_tys(value):
    """Formatuje kwote w zl jako tysiace z 2 miejscami po przecinku, styl polski (przecinek)."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        value = 0
    return f"{value/1000:.2f}".replace(".", ",")


app.jinja_env.filters["tys"] = format_tys


def current_saved_products():
    if not current_user.is_authenticated:
        return []
    return SavedProduct.query.filter_by(user_id=current_user.id).all()


app.jinja_env.globals["current_saved_products"] = current_saved_products


def current_saved_inspirations():
    if not current_user.is_authenticated:
        return []
    return SavedInspiration.query.filter_by(user_id=current_user.id).all()


app.jinja_env.globals["current_saved_inspirations"] = current_saved_inspirations


DEFAULT_SEGMENTS = [
    ("dzialka", "działka", "map"),
    ("stan0", "stan 0", "shovel"),
    ("surowy_otwarty", "stan surowy otwarty", "wall"),
    ("surowy_zamkniety", "stan surowy zamknięty", "window"),
    ("instalacje", "instalacje", "plug"),
    ("wykonczenie", "wykończenie", "paint"),
]

# szablon 1 = material + wykonawca, 2 = szacowana wycena + wykonawca + info, 3 = urzad: dokument + oplata + info,
# 4 = prosty koszt: kwota + link + opis (dzialka, dodatkowe koszty)
DEFAULT_ITEMS = {
    "dzialka": [
        ("koszt działki", 4),
    ],
    "stan0": [
        ("przyłącze: woda", 2), ("przyłącze: prąd", 2), ("przyłącze: gaz", 2), ("przyłącze: szambo", 2),
        ("architekt", 2), ("urząd", 3), ("geodeta", 2), ("ogrodzenie", 1), ("fundamenty", 1),
    ],
    "surowy_otwarty": [
        ("ściany zewnętrzne", 1), ("stropy", 1), ("schody", 1),
        ("więźba dachowa", 1), ("komin", 1), ("pokrycie dachu", 1),
    ],
    "surowy_zamkniety": [
        ("okna", 1), ("drzwi", 1), ("ścianki działowe", 1),
    ],
    "instalacje": [
        ("elektryczna", 1), ("wodno-kanalizacyjna", 1), ("ogrzewanie", 1),
    ],
    "wykonczenie": [
        ("korytarz", 1), ("kuchnia", 1), ("strefa dzienna", 1), ("sypialnia", 1), ("łazienka", 1),
    ],
}

# dla mieszkania/remontu: tylko instalacje (elektryczna, wodno-kanal.) + wykonczenie,
# bo stan budynku juz istnieje (kupione od dewelopera / w trakcie remontu)
DEFAULT_ITEMS_MIESZKANIE = {
    "instalacje": [
        ("elektryczna", 1), ("wodno-kanalizacyjna", 1),
    ],
    "wykonczenie": [
        ("strefa dzienna", 1), ("kuchnia", 1), ("łazienka", 1),
    ],
}


def segments_for_type(project_type):
    if project_type in ("mieszkanie", "remont"):
        return [s for s in DEFAULT_SEGMENTS if s[0] in ("instalacje", "wykonczenie")]
    return DEFAULT_SEGMENTS


def default_items_for_type(project_type):
    if project_type in ("mieszkanie", "remont"):
        return DEFAULT_ITEMS_MIESZKANIE
    return DEFAULT_ITEMS


PALETTE = [
    ("#888780", "var(--gray-bg)", "var(--gray-dark)"),
    ("#1D9E75", "var(--teal-bg)", "var(--teal-dark)"),
    ("#D85A30", "var(--coral-bg)", "var(--coral-dark)"),
    ("#D4537E", "var(--pink-bg)", "var(--pink-dark)"),
    ("#7F77DD", "var(--purple-bg)", "var(--purple-dark)"),
    ("#639922", "var(--green-bg)", "var(--green-dark)"),
    ("#BA7517", "var(--amber-bg)", "var(--amber-dark)"),
]


def save_photo(file_storage):
    if not file_storage or not file_storage.filename:
        return None
    ext = file_storage.filename.rsplit(".", 1)[-1].lower() if "." in file_storage.filename else ""
    if ext not in ALLOWED_PHOTO_EXT:
        return None
    filename = f"{uuid.uuid4().hex}.{ext}"
    file_storage.save(os.path.join(UPLOAD_DIR, filename))
    return filename


ALLOWED_DOC_EXT = ALLOWED_PHOTO_EXT | {"pdf"}


def save_document(file_storage):
    """Zapisuje PDF albo zdjecie, zwraca (filename, file_type) albo (None, None)."""
    if not file_storage or not file_storage.filename:
        return None, None
    ext = file_storage.filename.rsplit(".", 1)[-1].lower() if "." in file_storage.filename else ""
    if ext not in ALLOWED_DOC_EXT:
        return None, None
    filename = f"{uuid.uuid4().hex}.{ext}"
    file_storage.save(os.path.join(UPLOAD_DIR, filename))
    file_type = "pdf" if ext == "pdf" else "image"
    return filename, file_type


def slugify(name):
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9]+", "_", s).strip("_").lower()
    return s or "sekcja"


PROJECT_TYPES = [
    ("dom", "buduję dom", "building-cottage"),
    ("mieszkanie", "kupuję mieszkanie od dewelopera", "building-skyscraper"),
    ("remont", "planuję remont", "hammer"),
]
PROJECT_TYPE_ICONS = {k: icon for k, name, icon in PROJECT_TYPES}
PROJECT_TYPE_NAMES = {k: name for k, name, icon in PROJECT_TYPES}

ITEM_ICONS = {
    "ogrodzenie": "fence", "geodeta": "map-pin", "urząd": "building-bank",
    "fundamenty": "shovel", "ściany zewnętrzne": "wall", "stropy": "home",
    "schody": "stairs", "więźba dachowa": "triangle", "komin": "flame",
    "pokrycie dachu": "home", "okna": "window", "drzwi": "door",
    "ścianki działowe": "columns", "elektryczna": "bolt",
    "wodno-kanalizacyjna": "droplet", "ogrzewanie": "temperature",
}


def get_item_icon(item):
    name = (item.name or "").lower()
    if "przyłącze" in name:
        if "gaz" in name:
            return "flame"
        if "prąd" in name:
            return "bolt"
        if "szambo" in name:
            return "tank"
        if "woda" in name:
            return "droplet"
        return "droplet"
    for key, icon_name in ITEM_ICONS.items():
        if key in name:
            return icon_name
    return "list-check"


# DIY placeholder - "wykonamy to sami" quick-add
DIY_LABEL = "wykonamy to sami"


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(200), unique=True, nullable=False)
    password_hash = db.Column(db.String(300), nullable=False)
    houses = db.relationship("House", backref="owner", cascade="all, delete-orphan")
    saved_products = db.relationship("SavedProduct", backref="owner", cascade="all, delete-orphan")
    saved_services = db.relationship("SavedService", backref="owner", cascade="all, delete-orphan")
    saved_inspirations = db.relationship("SavedInspiration", backref="owner", cascade="all, delete-orphan")
    collaborations = db.relationship("HouseCollaborator", backref="user", cascade="all, delete-orphan",
                                      foreign_keys="HouseCollaborator.user_id")
    sent_invites = db.relationship("HouseInvite", backref="invited_by", cascade="all, delete-orphan",
                                    foreign_keys="HouseInvite.invited_by_id")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


def get_owned_house(house_id):
    house = House.query.get_or_404(house_id)
    if house.user_id == current_user.id:
        return house
    is_collaborator = HouseCollaborator.query.filter_by(house_id=house_id, user_id=current_user.id).first()
    if is_collaborator:
        return house
    abort(404)


def is_house_owner(house):
    return house.user_id == current_user.id


def get_owned_item(house_id, item_id):
    get_owned_house(house_id)
    item = Item.query.get_or_404(item_id)
    if item.house_id != house_id:
        abort(404)
    return item


def get_owned_variant(house_id, variant_id):
    get_owned_house(house_id)
    v = Variant.query.get_or_404(variant_id)
    if v.item.house_id != house_id:
        abort(404)
    return v


def get_owned_quote(house_id, quote_id):
    get_owned_house(house_id)
    q = RoomQuote.query.get_or_404(quote_id)
    if q.item.house_id != house_id:
        abort(404)
    return q


def get_owned_material(house_id, material_id):
    get_owned_house(house_id)
    m = Material.query.get_or_404(material_id)
    if m.item.house_id != house_id:
        abort(404)
    return m


def get_owned_task(house_id, task_id):
    get_owned_house(house_id)
    t = Task.query.get_or_404(task_id)
    if t.item.house_id != house_id:
        abort(404)
    return t


def get_owned_service(house_id, service_id):
    get_owned_house(house_id)
    s = Service.query.get_or_404(service_id)
    if s.house_id != house_id:
        abort(404)
    return s


def get_owned_category(house_id, category_id):
    get_owned_house(house_id)
    cat = InspirationCategory.query.get_or_404(category_id)
    if cat.house_id != house_id:
        abort(404)
    return cat


def get_owned_tile(house_id, tile_id):
    get_owned_house(house_id)
    t = InspirationTile.query.get_or_404(tile_id)
    if t.category.house_id != house_id:
        abort(404)
    return t


def get_owned_document(house_id, doc_id):
    get_owned_house(house_id)
    d = Document.query.get_or_404(doc_id)
    if d.house_id != house_id:
        abort(404)
    return d


def get_owned_saved_product(pid):
    p = SavedProduct.query.get_or_404(pid)
    if p.user_id != current_user.id:
        abort(404)
    return p


def get_owned_saved_service(sid):
    s = SavedService.query.get_or_404(sid)
    if s.user_id != current_user.id:
        abort(404)
    return s


def get_owned_saved_inspiration(iid):
    i = SavedInspiration.query.get_or_404(iid)
    if i.user_id != current_user.id:
        abort(404)
    return i


class House(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    name = db.Column(db.String(200), nullable=False)
    project_types = db.Column(db.String(100), default="dom")  # comma-separated: dom,mieszkanie,remont
    budget_total = db.Column(db.Float, nullable=True)

    @property
    def type_list(self):
        return [t for t in (self.project_types or "").split(",") if t]
    link = db.Column(db.String(400))
    area_m2 = db.Column(db.Float)
    rooms = db.Column(db.Integer)
    segments = db.relationship("Segment", backref="house", cascade="all, delete-orphan", order_by="Segment.order")
    items = db.relationship("Item", backref="house", cascade="all, delete-orphan")
    inspiration_categories = db.relationship("InspirationCategory", backref="house", cascade="all, delete-orphan")
    collaborators = db.relationship("HouseCollaborator", backref="house", cascade="all, delete-orphan")
    invites = db.relationship("HouseInvite", backref="house", cascade="all, delete-orphan")


class HouseCollaborator(db.Model):
    """Wspolpracownik projektu (nie wlasciciel) - pelny dostep do odczytu/edycji domu."""
    id = db.Column(db.Integer, primary_key=True)
    house_id = db.Column(db.Integer, db.ForeignKey("house.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)


class HouseInvite(db.Model):
    """Oczekujace zaproszenie do wspolpracy nad domem - istnieje tylko dopoki jest 'pending';
    zaakceptowanie tworzy HouseCollaborator i kasuje zaproszenie, odrzucenie po prostu kasuje."""
    id = db.Column(db.Integer, primary_key=True)
    house_id = db.Column(db.Integer, db.ForeignKey("house.id"), nullable=False)
    invited_email = db.Column(db.String(200), nullable=False)
    invited_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)


class Segment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    house_id = db.Column(db.Integer, db.ForeignKey("house.id"), nullable=False)
    key = db.Column(db.String(80), nullable=False)
    name = db.Column(db.String(200), nullable=False)
    icon = db.Column(db.String(50), default="folder")
    order = db.Column(db.Integer, default=0)


class Item(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    house_id = db.Column(db.Integer, db.ForeignKey("house.id"), nullable=False)
    segment_key = db.Column(db.String(80), nullable=False)
    name = db.Column(db.String(200), nullable=False)
    template = db.Column(db.Integer, default=1)
    excluded = db.Column(db.Boolean, default=False)
    is_room = db.Column(db.Boolean, default=False)  # pokoje w wykonczeniu - tylko inspiracje, bez wyceny
    is_custom = db.Column(db.Boolean, default=False)  # dodane przez uzytkownika (moga byc usuniete)
    will_change = db.Column(db.Boolean, default=None)  # None=nie dotyczy, False/True dla instalacji w mieszkaniu/remoncie
    room_tag = db.Column(db.String(100))  # gabinet/sypialnia/pokoj dziecięcy/wlasny, tylko dla is_room
    room_area = db.Column(db.Float)  # m2
    room_shape = db.Column(db.String(20), default="irregular")  # "rectangular" | "irregular"
    room_side_a = db.Column(db.Float)
    room_side_b = db.Column(db.Float)
    room_height = db.Column(db.Float)       # wysokosc scian (pokoj plaski)
    room_height_low = db.Column(db.Float)   # wysokosc niska (pokoj ze skosem)
    room_height_high = db.Column(db.Float)  # wysokosc wysoka/kalenica (pokoj ze skosem)
    room_perimeter = db.Column(db.Float)    # obwod - dla pokoi nieregularnych, do wyliczenia scian
    variants = db.relationship("Variant", backref="item", cascade="all, delete-orphan")
    room_quotes = db.relationship("RoomQuote", backref="item", cascade="all, delete-orphan")
    materials = db.relationship("Material", backref="item", cascade="all, delete-orphan")
    tasks = db.relationship("Task", backref="item", cascade="all, delete-orphan")

    @property
    def icon(self):
        return get_item_icon(self)

    @property
    def computed_area(self):
        if self.room_shape in ("rectangular", "sloped") and self.room_side_a and self.room_side_b:
            return round(self.room_side_a * self.room_side_b, 2)
        return self.room_area

    @property
    def wall_area(self):
        """Powierzchnia scian do malowania/tynkowania itp. Skos: obwod x srednia wysokosc
        (niska+wysoka)/2 - przyblizenie powszechnie uzywane przez wykonawcow."""
        if self.room_shape == "sloped" and self.room_side_a and self.room_side_b \
                and self.room_height_low and self.room_height_high:
            perimeter = 2 * (self.room_side_a + self.room_side_b)
            avg_h = (self.room_height_low + self.room_height_high) / 2
            return round(perimeter * avg_h, 2)
        if self.room_shape == "rectangular" and self.room_side_a and self.room_side_b and self.room_height:
            perimeter = 2 * (self.room_side_a + self.room_side_b)
            return round(perimeter * self.room_height, 2)
        if self.room_perimeter and self.room_height:
            return round(self.room_perimeter * self.room_height, 2)
        return None

    @property
    def budget_options(self):
        return item_options(self)


class Variant(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    material_name = db.Column(db.String(200))
    material_company = db.Column(db.String(200))
    material_price = db.Column(db.Float, default=0)
    labor_contractor = db.Column(db.String(200))
    labor_price = db.Column(db.Float, default=0)
    labor_included = db.Column(db.Boolean, default=False)
    link = db.Column(db.String(400))
    note = db.Column(db.Text)
    selected = db.Column(db.Boolean, default=False)
    include_in_budget = db.Column(db.Boolean, default=True)
    group_name = db.Column(db.String(100))

    @property
    def total(self):
        if self.labor_included:
            return self.material_price or 0
        return (self.material_price or 0) + (self.labor_price or 0)


class SavedProduct(db.Model):
    """Robocze - zapisany szablon produktu, do pozniejszego dodania do materialow/produktow w projekcie."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    name = db.Column(db.String(200), nullable=False)
    shop = db.Column(db.String(200))
    price = db.Column(db.Float, default=0)
    description = db.Column(db.Text)
    link = db.Column(db.String(400))


class SavedService(db.Model):
    """Robocze - zapisany szablon uslugi/firmy, do pozniejszego dodania w sekcji uslugi wykonczenia."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    company = db.Column(db.String(200), nullable=False)
    tags = db.Column(db.String(400), default="")
    link = db.Column(db.String(400))
    description = db.Column(db.Text)

    @property
    def tag_list(self):
        return [t for t in (self.tags or "").split(",") if t]


class SavedInspiration(db.Model):
    """Robocze - zapisana inspiracja, do pozniejszego dodania do konkretnego pokoju."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    link = db.Column(db.String(400))
    photo_filename = db.Column(db.String(300))


SERVICE_TAGS = ["tynki", "posadzki", "płyty G-K", "ocieplenia", "glazurnik", "parkiety", "panele",
                "malarz", "tapicer", "stolarz", "elektryk (gniazdka)", "hydraulik (rurowanie)"]


class Service(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    house_id = db.Column(db.Integer, db.ForeignKey("house.id"), nullable=False)
    company = db.Column(db.String(200))
    link = db.Column(db.String(400))
    description = db.Column(db.Text)
    tags = db.Column(db.String(400), default="")

    @property
    def tag_list(self):
        return [t for t in (self.tags or "").split(",") if t]


class RoomQuote(db.Model):
    """Wycena wykonania czegos w pokoju, oparta o wybrana usluge + tag (nie formularz materialu)."""
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    service_id = db.Column(db.Integer, db.ForeignKey("service.id"))
    service = db.relationship("Service")
    tag = db.Column(db.String(100))
    price = db.Column(db.Float, default=0)
    note = db.Column(db.Text)
    include_in_budget = db.Column(db.Boolean, default=True)


class Material(db.Model):
    """Kafelek materialu - uzywany zarowno w zakladce 'materialy' instalacji (mieszkanie/remont)
    jak i w liscie produktow pokoju (wykonczenie). Edytowalny i usuwalny."""
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    name = db.Column(db.String(200), nullable=False)
    shop = db.Column(db.String(200))
    price = db.Column(db.Float, default=0)
    description = db.Column(db.Text)
    link = db.Column(db.String(400))
    include_in_budget = db.Column(db.Boolean, default=True)
    price_per_m2 = db.Column(db.Float)   # jesli ustawione: price = price_per_m2 * zastosowana powierzchnia
    surface_type = db.Column(db.String(20))  # "floor" | "wall" - jaka powierzchnia pokoju uzyto do wyliczenia


class Task(db.Model):
    """Zadanie na liscie 'do zrobienia' per pokoj."""
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    text = db.Column(db.String(300), nullable=False)
    done = db.Column(db.Boolean, default=False)


class Document(db.Model):
    """Dokument (PDF lub zdjecie) - albo przypiety do konkretnej pozycji (np. architekt),
    albo do calej sekcji (np. dzialka - akt notarialny) gdy item_id jest puste."""
    id = db.Column(db.Integer, primary_key=True)
    house_id = db.Column(db.Integer, db.ForeignKey("house.id"), nullable=False)
    segment_key = db.Column(db.String(80), nullable=False)
    item_id = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    filename = db.Column(db.String(300))
    file_type = db.Column(db.String(10))  # "pdf" | "image"


class InspirationCategory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    house_id = db.Column(db.Integer, db.ForeignKey("house.id"), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    tiles = db.relationship("InspirationTile", backref="category", cascade="all, delete-orphan")


class InspirationTile(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    category_id = db.Column(db.Integer, db.ForeignKey("inspiration_category.id"), nullable=False)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    link = db.Column(db.String(400))
    photo_filename = db.Column(db.String(300))


def item_options(item):
    """Lista cen alternatywnych 'opcji' dla pozycji: kazdy niepogrupowany wariant to
    osobna opcja, a warianty z tym samym group_name sumuja sie w JEDNA opcje (np.
    gniazdka+bezpieczniki+okablowanie jako DIY vs. jedna wycena elektryka)."""
    included = [v for v in item.variants if v.include_in_budget]
    if item.template in (3, 4):
        base = [sum(v.total for v in included)] if included else []
    else:
        groups = {}
        standalone = []
        for v in included:
            if v.group_name:
                groups.setdefault(v.group_name, []).append(v.total)
            else:
                standalone.append(v.total)
        group_sums = [sum(vals) for vals in groups.values()]
        base = standalone + group_sums

    materials_sum = sum(m.price for m in item.materials if m.include_in_budget)
    if materials_sum:
        base = [b + materials_sum for b in base] if base else [materials_sum]
    return base


def segment_range(house_id, segment_key):
    items = Item.query.filter_by(house_id=house_id, segment_key=segment_key, excluded=False).all()
    lo = hi = 0
    for it in items:
        if it.is_room:
            s = sum(q.price for q in it.room_quotes if q.include_in_budget)
            s += sum(m.price for m in it.materials if m.include_in_budget)
            lo += s
            hi += s
        else:
            opts = item_options(it)
            if opts:
                lo += min(opts)
                hi += max(opts)
    return lo, hi


def house_totals(house_id):
    lo_total = hi_total = 0
    breakdown = []
    segments = Segment.query.filter_by(house_id=house_id).order_by(Segment.order).all()
    for i, seg in enumerate(segments):
        lo, hi = segment_range(house_id, seg.key)
        color, bg, fg = PALETTE[i % len(PALETTE)]
        item_count = Item.query.filter_by(house_id=house_id, segment_key=seg.key).count()
        breakdown.append({"key": seg.key, "name": seg.name, "icon": seg.icon,
                           "lo": lo, "hi": hi, "color": color, "bg": bg, "fg": fg,
                           "item_count": item_count})
        lo_total += lo
        hi_total += hi
    return lo_total, hi_total, breakdown


def seed_house_defaults(house):
    project_type = house.type_list[0] if house.type_list else "dom"
    segments = segments_for_type(project_type)
    items_map = default_items_for_type(project_type)
    for i, (key, name, icon) in enumerate(segments):
        db.session.add(Segment(house_id=house.id, key=key, name=name, icon=icon, order=i))
        for item_name, template in items_map.get(key, []):
            is_installation = key == "instalacje" and project_type in ("mieszkanie", "remont")
            db.session.add(Item(house_id=house.id, segment_key=key, name=item_name, template=template,
                                 is_room=(key == "wykonczenie"),
                                 will_change=(False if is_installation else None)))
    room_names = [name for name, template in items_map.get("wykonczenie", [])]
    for cat in room_names:
        db.session.add(InspirationCategory(house_id=house.id, name=cat))
    db.session.commit()


@app.route("/")
def landing():
    return render_template("landing.html")


@app.route("/projects")
@login_required
def index():
    owned = House.query.filter_by(user_id=current_user.id).all()
    collab_ids = [c.house_id for c in HouseCollaborator.query.filter_by(user_id=current_user.id).all()]
    collab_houses = House.query.filter(House.id.in_(collab_ids)).all() if collab_ids else []
    houses = owned + collab_houses
    pending_invites = HouseInvite.query.filter_by(invited_email=current_user.email).all()
    return render_template("index.html", houses=houses, type_icons=PROJECT_TYPE_ICONS, type_names=PROJECT_TYPE_NAMES,
                            owned_ids={h.id for h in owned}, pending_invites=pending_invites)


@app.route("/house/new", methods=["GET", "POST"])
@login_required
def house_new():
    if request.method == "POST":
        project_type = request.form.get("project_type") or "dom"
        if project_type not in ("dom", "mieszkanie", "remont"):
            project_type = "dom"
        name = ((request.form.get("name") or "")[:200] or "").strip()[:200]
        if not name:
            return render_template("house_new.html", project_types=PROJECT_TYPES)
        budget_raw = request.form.get("budget_total")
        h = House(
            user_id=current_user.id,
            name=name,
            link=(request.form.get("link") or "")[:400],
            area_m2=clamp_number(request.form.get("area_m2"), min_val=0, max_val=100000),
            rooms=clamp_int(request.form.get("rooms"), min_val=0, max_val=1000),
            project_types=project_type,
            budget_total=clamp_number(budget_raw, min_val=0, max_val=999999999, default=None) if budget_raw else None,
        )
        db.session.add(h)
        db.session.commit()
        seed_house_defaults(h)
        return redirect(url_for("dashboard", house_id=h.id))
    return render_template("house_new.html", project_types=PROJECT_TYPES)


@app.route("/house/<int:house_id>")
@login_required
def dashboard(house_id):
    house = get_owned_house(house_id)
    ensure_dzialka_segment(house)
    lo_total, hi_total, breakdown = house_totals(house_id)

    circumference = 2 * 3.14159265 * 78
    slices = [{"key": b["key"], "name": b["name"], "avg": (b["lo"] + b["hi"]) / 2,
               "color": b["color"], "lo": b["lo"], "hi": b["hi"],
               "url": url_for("segment_view", house_id=house_id, segment_key=b["key"])} for b in breakdown]

    remaining = None
    if house.budget_total:
        remaining = max(house.budget_total - hi_total, 0)
        if remaining > 0:
            slices.append({"key": "__remaining__", "name": "pozostały budżet", "avg": remaining,
                            "color": "var(--track)", "lo": remaining, "hi": remaining, "url": None})

    total_avg = sum(s["avg"] for s in slices) or 1
    offset = 0.0
    for s in slices:
        length = (s["avg"] / total_avg) * circumference
        s["dasharray"] = f"{length:.1f} {circumference - length:.1f}"
        s["dashoffset"] = f"-{offset:.1f}"
        offset += length

    center_label = "tys zł pozostało" if house.budget_total else "tys zł łącznie"

    return render_template("dashboard.html", house=house, lo_total=lo_total, hi_total=hi_total,
                            breakdown=breakdown, slices=slices, remaining=remaining, center_label=center_label)


ROOM_TAGS = ["gabinet", "sypialnia", "pokój dziecięcy"]


def ensure_dzialka_segment(house):
    """Dogania istniejace domy (utworzone zanim dodalismy sekcje 'dzialka') - dodaje ja
    automatycznie, jesli jej brakuje, zamiast wymagac ponownego tworzenia projektu."""
    if "dom" not in house.type_list:
        return
    if Segment.query.filter_by(house_id=house.id, key="dzialka").first():
        return
    min_order = db.session.query(db.func.min(Segment.order)).filter_by(house_id=house.id).scalar() or 0
    db.session.add(Segment(house_id=house.id, key="dzialka", name="działka", icon="map", order=min_order - 1))
    db.session.add(Item(house_id=house.id, segment_key="dzialka", name="koszt działki", template=4))
    db.session.commit()


def sync_house_from_rooms(house_id):
    """Aktualizuje metraz calkowity (suma metrazy pokoi) i liczbe pokoi (sypialnie +
    pokoje dziecięce + gabinety + wszystkie dodane recznie przez uzytkownika)."""
    house = db.session.get(House, house_id)
    rooms = Item.query.filter_by(house_id=house_id, segment_key="wykonczenie", is_room=True).all()

    total_area = sum(r.computed_area or 0 for r in rooms)
    if total_area > 0:
        house.area_m2 = round(total_area, 2)

    count = sum(
        1 for r in rooms
        if r.is_custom or (r.name or "").strip().lower() == "sypialnia" or (r.room_tag in ROOM_TAGS)
    )
    house.rooms = count
    db.session.commit()


@app.route("/house/<int:house_id>/metraz")
@login_required
def metraz(house_id):
    house = get_owned_house(house_id)
    rooms = Item.query.filter_by(house_id=house_id, segment_key="wykonczenie", is_room=True).all()
    return render_template("metraz.html", house=house, rooms=rooms, room_tags=ROOM_TAGS)


@app.route("/house/<int:house_id>/metraz/update", methods=["POST"])
@login_required
def metraz_update(house_id):
    house = get_owned_house(house_id)
    house.area_m2 = clamp_number(request.form.get("area_m2"), min_val=0, max_val=100000)
    house.rooms = clamp_int(request.form.get("rooms"), min_val=0, max_val=1000)
    db.session.commit()
    return redirect(url_for("metraz", house_id=house_id))


@app.route("/house/<int:house_id>/metraz/room/new", methods=["POST"])
@login_required
def metraz_room_new(house_id):
    get_owned_house(house_id)
    name = ((request.form.get("name") or "")[:200] or "").strip()[:200]
    if not name:
        return redirect(url_for("metraz", house_id=house_id))
    tag = (request.form.get("room_tag") or "")[:100]
    custom_tag = (request.form.get("custom_tag") or "").strip()[:100]
    if tag == "__custom__":
        tag = custom_tag

    shape = request.form.get("room_shape") or "irregular"
    if shape not in ("irregular", "rectangular", "sloped"):
        shape = "irregular"
    area = clamp_number(request.form.get("room_area"), min_val=0, max_val=10000)
    side_a = clamp_number(request.form.get("room_side_a"), min_val=0, max_val=1000)
    side_b = clamp_number(request.form.get("room_side_b"), min_val=0, max_val=1000)
    height = clamp_number(request.form.get("room_height"), min_val=0, max_val=20)
    height_low = clamp_number(request.form.get("room_height_low"), min_val=0, max_val=20)
    height_high = clamp_number(request.form.get("room_height_high"), min_val=0, max_val=20)
    perimeter = clamp_number(request.form.get("room_perimeter"), min_val=0, max_val=1000)
    area = area or None
    side_a = side_a or None
    side_b = side_b or None
    height = height or None
    height_low = height_low or None
    height_high = height_high or None
    perimeter = perimeter or None
    if shape == "irregular":
        side_a = side_b = height_low = height_high = None
    elif shape == "rectangular":
        height_low = height_high = None
    elif shape == "sloped":
        height = perimeter = None

    item = Item(house_id=house_id, segment_key="wykonczenie", name=name, is_room=True, is_custom=True,
                room_tag=tag or None, room_area=area, room_shape=shape, room_side_a=side_a, room_side_b=side_b,
                room_height=height, room_height_low=height_low, room_height_high=height_high,
                room_perimeter=perimeter)
    db.session.add(item)
    db.session.commit()

    if not InspirationCategory.query.filter_by(house_id=house_id, name=name).first():
        db.session.add(InspirationCategory(house_id=house_id, name=name))
        db.session.commit()

    sync_house_from_rooms(house_id)
    return redirect(url_for("metraz", house_id=house_id))


@app.route("/house/<int:house_id>/metraz/room/<int:item_id>/edit", methods=["GET", "POST"])
@login_required
def metraz_room_edit(house_id, item_id):
    house = get_owned_house(house_id)
    room = get_owned_item(house_id, item_id)
    if not room.is_room:
        abort(404)

    if request.method == "POST":
        old_name = room.name
        new_name = ((request.form.get("name") or "")[:200] or "").strip()[:200] or old_name

        tag = (request.form.get("room_tag") or "")[:100]
        custom_tag = (request.form.get("custom_tag") or "").strip()[:100]
        if tag == "__custom__":
            tag = custom_tag
        room.room_tag = tag or None

        shape = request.form.get("room_shape") or "irregular"
        room.room_shape = shape if shape in ("irregular", "rectangular", "sloped") else "irregular"
        area = clamp_number(request.form.get("room_area"), min_val=0, max_val=10000) or None
        side_a = clamp_number(request.form.get("room_side_a"), min_val=0, max_val=1000) or None
        side_b = clamp_number(request.form.get("room_side_b"), min_val=0, max_val=1000) or None
        height = clamp_number(request.form.get("room_height"), min_val=0, max_val=20) or None
        height_low = clamp_number(request.form.get("room_height_low"), min_val=0, max_val=20) or None
        height_high = clamp_number(request.form.get("room_height_high"), min_val=0, max_val=20) or None
        perimeter = clamp_number(request.form.get("room_perimeter"), min_val=0, max_val=1000) or None
        if room.room_shape == "irregular":
            side_a = side_b = height_low = height_high = None
        elif room.room_shape == "rectangular":
            height_low = height_high = None
        elif room.room_shape == "sloped":
            height = perimeter = None
        room.room_area = area
        room.room_side_a = side_a
        room.room_side_b = side_b
        room.room_height = height
        room.room_height_low = height_low
        room.room_height_high = height_high
        room.room_perimeter = perimeter

        if new_name != old_name:
            room.name = new_name
            cat = InspirationCategory.query.filter_by(house_id=house_id, name=old_name).first()
            if cat:
                cat.name = new_name

        db.session.commit()
        sync_house_from_rooms(house_id)
        return redirect(url_for("metraz", house_id=house_id))

    return render_template("metraz_room_edit.html", house=house, room=room, room_tags=ROOM_TAGS)


@app.route("/house/<int:house_id>/segment/new", methods=["POST"])
@login_required
def segment_new(house_id):
    get_owned_house(house_id)
    name = ((request.form.get("name") or "")[:200] or "").strip()[:200]
    if name:
        key = slugify(name)
        max_order = db.session.query(db.func.max(Segment.order)).filter_by(house_id=house_id).scalar() or 0
        db.session.add(Segment(house_id=house_id, key=key, name=name, icon="folder", order=max_order + 1))
        db.session.commit()
    return redirect(url_for("dashboard", house_id=house_id))


@app.route("/house/<int:house_id>/segment/<segment_key>")
@login_required
def segment_view(house_id, segment_key):
    house = get_owned_house(house_id)
    if segment_key == "dzialka":
        ensure_dzialka_segment(house)
    seg = Segment.query.filter_by(house_id=house_id, key=segment_key).first_or_404()
    items = Item.query.filter_by(house_id=house_id, segment_key=segment_key).all()
    lo, hi = segment_range(house_id, segment_key)

    room_tile_counts = {}
    room_quote_sums = {}
    if segment_key == "wykonczenie":
        for it in items:
            if it.is_room:
                cat = InspirationCategory.query.filter_by(house_id=house_id, name=it.name).first()
                room_tile_counts[it.id] = len(cat.tiles) if cat else 0
                quotes_sum = sum(q.price for q in it.room_quotes if q.include_in_budget)
                materials_sum = sum(m.price for m in it.materials if m.include_in_budget)
                room_quote_sums[it.id] = quotes_sum + materials_sum

    services = []
    all_tags = []
    active_tag = None
    if segment_key == "wykonczenie":
        active_tag = request.args.get("tag") or None
        q = Service.query.filter_by(house_id=house_id)
        if active_tag:
            q = q.filter(Service.tags.contains(active_tag))
        services = q.all()
        used_tags = set()
        for s in Service.query.filter_by(house_id=house_id).all():
            used_tags.update(s.tag_list)
        all_tags = SERVICE_TAGS + sorted(t for t in used_tags if t not in SERVICE_TAGS)

    saved_services = SavedService.query.filter_by(user_id=current_user.id).all() if segment_key == "wykonczenie" else []

    documents = Document.query.filter_by(house_id=house_id, segment_key=segment_key).all()
    segment_documents = [d for d in documents if d.item_id is None]
    item_documents = {}
    for d in documents:
        if d.item_id is not None:
            item_documents.setdefault(d.item_id, []).append(d)

    return render_template("segment.html", house=house, segment=seg, items=items, lo=lo, hi=hi,
                            services=services, all_tags=all_tags, active_tag=active_tag,
                            room_tile_counts=room_tile_counts, room_quote_sums=room_quote_sums,
                            saved_services=saved_services, segment_documents=segment_documents,
                            item_documents=item_documents)


@app.route("/house/<int:house_id>/services/new", methods=["POST"])
@login_required
def service_new(house_id):
    tags_selected = request.form.getlist("tags")
    custom_tag = (request.form.get("custom_tag") or "").strip()
    if custom_tag:
        tags_selected.append(custom_tag)
    s = Service(
        house_id=house_id,
        company=(request.form.get("company") or "")[:200],
        link=(request.form.get("link") or "")[:400],
        description=(request.form.get("description") or "")[:5000],
        tags=",".join(tags_selected),
    )
    db.session.add(s)
    db.session.commit()
    return redirect(url_for("segment_view", house_id=house_id, segment_key="wykonczenie"))


@app.route("/house/<int:house_id>/services/from-saved/<int:sid>", methods=["POST"])
@login_required
def service_from_saved(house_id, sid):
    get_owned_house(house_id)
    sv = get_owned_saved_service(sid)
    s = Service(house_id=house_id, company=sv.company, tags=sv.tags, link=sv.link, description=sv.description)
    db.session.add(s)
    db.session.commit()
    return redirect(url_for("segment_view", house_id=house_id, segment_key="wykonczenie"))


@app.route("/house/<int:house_id>/services/<int:service_id>/edit", methods=["GET", "POST"])
@login_required
def service_edit(house_id, service_id):
    house = get_owned_house(house_id)
    s = get_owned_service(house_id, service_id)
    if request.method == "POST":
        tags_selected = request.form.getlist("tags")
        custom_tag = (request.form.get("custom_tag") or "").strip()
        if custom_tag:
            tags_selected.append(custom_tag)
        s.company = (request.form.get("company") or "")[:200]
        s.link = (request.form.get("link") or "")[:400]
        s.description = (request.form.get("description") or "")[:5000]
        s.tags = ",".join(tags_selected)
        db.session.commit()
        return redirect(url_for("segment_view", house_id=house_id, segment_key="wykonczenie"))
    return render_template("service_edit.html", house=house, service=s, all_tags=SERVICE_TAGS)


@app.route("/house/<int:house_id>/services/<int:service_id>/delete", methods=["GET"])
@login_required
def service_delete_confirm(house_id, service_id):
    house = get_owned_house(house_id)
    s = get_owned_service(house_id, service_id)
    return render_template("confirm_delete.html", house=house, target_name=s.company or "ta usługa",
                            action_url=url_for("service_delete", house_id=house_id, service_id=service_id),
                            cancel_url=url_for("segment_view", house_id=house_id, segment_key="wykonczenie"))


@app.route("/house/<int:house_id>/services/<int:service_id>/delete", methods=["POST"])
@login_required
def service_delete(house_id, service_id):
    s = get_owned_service(house_id, service_id)
    db.session.delete(s)
    db.session.commit()
    return redirect(url_for("segment_view", house_id=house_id, segment_key="wykonczenie"))


@app.route("/house/<int:house_id>/item/<int:item_id>/quote/new")
@login_required
def quote_new(house_id, item_id):
    house = get_owned_house(house_id)
    item = get_owned_item(house_id, item_id)
    tag = request.args.get("tag")

    used_tags = set()
    for s in Service.query.filter_by(house_id=house_id).all():
        used_tags.update(s.tag_list)
    all_tags = SERVICE_TAGS + sorted(t for t in used_tags if t not in SERVICE_TAGS)

    services = []
    if tag:
        services = Service.query.filter_by(house_id=house_id).filter(Service.tags.contains(tag)).all()

    return render_template("quote_new.html", house=house, item=item, all_tags=all_tags, tag=tag, services=services)


@app.route("/house/<int:house_id>/item/<int:item_id>/quote/create/<int:service_id>", methods=["POST"])
@login_required
def quote_create(house_id, item_id, service_id):
    get_owned_item(house_id, item_id)
    get_owned_service(house_id, service_id)
    q = RoomQuote(
        item_id=item_id,
        service_id=service_id,
        tag=request.form.get("tag"),
        price=clamp_number(request.form.get("price")),
        note=(request.form.get("note") or "")[:5000],
    )
    db.session.add(q)
    db.session.commit()
    return redirect(url_for("item_detail", house_id=house_id, item_id=item_id))


@app.route("/house/<int:house_id>/item/<int:item_id>/quote/diy", methods=["POST"])
@login_required
def quote_diy(house_id, item_id):
    get_owned_item(house_id, item_id)
    q = RoomQuote(item_id=item_id, service_id=None, tag=DIY_LABEL,
                  price=clamp_number(request.form.get("price")), note=(request.form.get("note") or "")[:5000])
    db.session.add(q)
    db.session.commit()
    return redirect(url_for("item_detail", house_id=house_id, item_id=item_id))


@app.route("/house/<int:house_id>/quote/<int:quote_id>/toggle-include", methods=["POST"])
@login_required
def quote_toggle_include(house_id, quote_id):
    q = get_owned_quote(house_id, quote_id)
    q.include_in_budget = not q.include_in_budget
    db.session.commit()
    return redirect(url_for("item_detail", house_id=house_id, item_id=q.item_id))


@app.route("/house/<int:house_id>/quote/<int:quote_id>/delete", methods=["GET"])
@login_required
def quote_delete_confirm(house_id, quote_id):
    house = get_owned_house(house_id)
    q = get_owned_quote(house_id, quote_id)
    label = (q.service.company if q.service else q.tag) or "ta wycena"
    return render_template("confirm_delete.html", house=house, target_name=label,
                            action_url=url_for("quote_delete", house_id=house_id, quote_id=quote_id),
                            cancel_url=url_for("item_detail", house_id=house_id, item_id=q.item_id))


@app.route("/house/<int:house_id>/quote/<int:quote_id>/delete", methods=["POST"])
@login_required
def quote_delete(house_id, quote_id):
    q = get_owned_quote(house_id, quote_id)
    item_id = q.item_id
    db.session.delete(q)
    db.session.commit()
    return redirect(url_for("item_detail", house_id=house_id, item_id=item_id))


@app.route("/house/<int:house_id>/quote/<int:quote_id>/edit", methods=["GET", "POST"])
@login_required
def quote_edit(house_id, quote_id):
    house = get_owned_house(house_id)
    q = get_owned_quote(house_id, quote_id)
    if request.method == "POST":
        q.note = (request.form.get("note") or "")[:5000]
        q.price = clamp_number(request.form.get("price"), min_val=0, max_val=100_000_000)
        db.session.commit()
        return redirect(url_for("item_detail", house_id=house_id, item_id=q.item_id))
    return render_template("quote_edit.html", house=house, quote=q, item=q.item)


@app.route("/house/<int:house_id>/variant/<int:variant_id>/edit", methods=["GET", "POST"])
@login_required
def variant_edit(house_id, variant_id):
    house = get_owned_house(house_id)
    variant = get_owned_variant(house_id, variant_id)
    item = variant.item

    if request.method == "POST":
        if item.template == 1:
            if request.form.get("labor_mode") == "self":
                variant.labor_contractor = "robię sam(a)"
                variant.labor_price = 0.0
                variant.labor_included = True
                variant.material_name = (request.form.get("material_name") or "")[:200]
                variant.material_company = (request.form.get("material_company") or "")[:200]
                variant.material_price = clamp_number(request.form.get("material_price"))
            else:
                variant.labor_contractor = (request.form.get("labor_contractor") or "")[:200]
                variant.labor_price = clamp_number(request.form.get("labor_price"))
                variant.labor_included = False
                variant.material_name = (request.form.get("material_name_c") or "")[:200]
                variant.material_company = (request.form.get("material_company_c") or "")[:200]
                variant.material_price = clamp_number(request.form.get("material_price_c"))
        else:
            variant.material_name = (request.form.get("material_name") or "")[:200]
            variant.material_company = ""
            variant.material_price = clamp_number(request.form.get("material_price"))
            variant.labor_contractor = (request.form.get("labor_contractor") or "")[:200]
            variant.labor_price = 0.0
            variant.labor_included = False
        variant.link = (request.form.get("link") or "")[:400]
        variant.note = (request.form.get("note") or "")[:5000]
        db.session.commit()
        return redirect(url_for("item_detail", house_id=house_id, item_id=item.id))

    return render_template("variant_edit.html", house=house, item=item, variant=variant)


@app.route("/house/<int:house_id>/variant/<int:variant_id>/set-group", methods=["POST"])
@login_required
def variant_set_group(house_id, variant_id):
    v = get_owned_variant(house_id, variant_id)
    name = (request.form.get("group_name") or "").strip()
    v.group_name = name or None
    db.session.commit()
    return redirect(url_for("item_detail", house_id=house_id, item_id=v.item_id))


@app.route("/house/<int:house_id>/variant/<int:variant_id>/toggle-include", methods=["POST"])
@login_required
def variant_toggle_include(house_id, variant_id):
    v = get_owned_variant(house_id, variant_id)
    v.include_in_budget = not v.include_in_budget
    db.session.commit()
    return redirect(url_for("item_detail", house_id=house_id, item_id=v.item_id))


@app.route("/house/<int:house_id>/variant/<int:variant_id>/delete", methods=["GET"])
@login_required
def variant_delete_confirm(house_id, variant_id):
    house = get_owned_house(house_id)
    v = get_owned_variant(house_id, variant_id)
    return render_template("confirm_delete.html", house=house, target_name=v.material_name or "ta opcja",
                            action_url=url_for("variant_delete", house_id=house_id, variant_id=variant_id),
                            cancel_url=url_for("item_detail", house_id=house_id, item_id=v.item_id))


@app.route("/house/<int:house_id>/variant/<int:variant_id>/delete", methods=["POST"])
@login_required
def variant_delete(house_id, variant_id):
    v = get_owned_variant(house_id, variant_id)
    item_id = v.item_id
    db.session.delete(v)
    db.session.commit()
    return redirect(url_for("item_detail", house_id=house_id, item_id=item_id))


@app.route("/house/<int:house_id>/item/<int:item_id>")
@login_required
def item_detail(house_id, item_id):
    house = get_owned_house(house_id)
    item = get_owned_item(house_id, item_id)
    insp_category = None
    if item.segment_key == "wykonczenie":
        insp_category = InspirationCategory.query.filter_by(house_id=house_id, name=item.name).first()
        if not insp_category:
            insp_category = InspirationCategory(house_id=house_id, name=item.name)
            db.session.add(insp_category)
            db.session.commit()
    documents = Document.query.filter_by(house_id=house_id, item_id=item.id).all()
    return render_template("item_detail.html", house=house, item=item, insp_category=insp_category, documents=documents)


@app.route("/house/<int:house_id>/segment/<segment_key>/item/new", methods=["POST"])
@login_required
def item_new(house_id, segment_key):
    get_owned_house(house_id)
    if not Segment.query.filter_by(house_id=house_id, key=segment_key).first():
        abort(404)
    name = ((request.form.get("name") or "")[:200] or "").strip()[:200]
    template = request.form.get("template")
    template = int(template) if template in ("1", "2", "3", "4") else 1
    if name:
        item = Item(house_id=house_id, segment_key=segment_key, name=name, template=template, is_custom=True)
        db.session.add(item)
        db.session.commit()
        return redirect(url_for("item_variant_add", house_id=house_id, item_id=item.id))
    return redirect(url_for("segment_view", house_id=house_id, segment_key=segment_key))


@app.route("/house/<int:house_id>/item/<int:item_id>/delete", methods=["GET"])
@login_required
def item_delete_confirm(house_id, item_id):
    house = get_owned_house(house_id)
    item = get_owned_item(house_id, item_id)
    return render_template("confirm_delete.html", house=house, target_name=item.name,
                            action_url=url_for("item_delete", house_id=house_id, item_id=item_id),
                            cancel_url=url_for("segment_view", house_id=house_id, segment_key=item.segment_key))


@app.route("/house/<int:house_id>/item/<int:item_id>/delete", methods=["POST"])
@login_required
def item_delete(house_id, item_id):
    item = get_owned_item(house_id, item_id)
    segment_key = item.segment_key
    was_room = item.is_room
    if item.is_room:
        cat = InspirationCategory.query.filter_by(house_id=house_id, name=item.name).first()
        if cat:
            db.session.delete(cat)
    db.session.delete(item)
    db.session.commit()
    if was_room:
        sync_house_from_rooms(house_id)
    return redirect(url_for("segment_view", house_id=house_id, segment_key=segment_key))


@app.route("/house/<int:house_id>/segment/<segment_key>/delete", methods=["GET"])
@login_required
def segment_delete_confirm(house_id, segment_key):
    house = get_owned_house(house_id)
    if segment_key in [k for k, n, i in DEFAULT_SEGMENTS]:
        abort(404)
    seg = Segment.query.filter_by(house_id=house_id, key=segment_key).first_or_404()
    return render_template("confirm_delete.html", house=house, target_name=seg.name,
                            action_url=url_for("segment_delete", house_id=house_id, segment_key=segment_key),
                            cancel_url=url_for("dashboard", house_id=house_id))


@app.route("/house/<int:house_id>/segment/<segment_key>/delete", methods=["POST"])
@login_required
def segment_delete(house_id, segment_key):
    get_owned_house(house_id)
    if segment_key in [k for k, n, i in DEFAULT_SEGMENTS]:
        abort(404)
    Segment.query.filter_by(house_id=house_id, key=segment_key).delete()
    Item.query.filter_by(house_id=house_id, segment_key=segment_key).delete()
    db.session.commit()
    return redirect(url_for("dashboard", house_id=house_id))


@app.route("/house/<int:house_id>/item/<int:item_id>/toggle-excluded", methods=["POST"])
@login_required
def item_toggle_excluded(house_id, item_id):
    item = get_owned_item(house_id, item_id)
    item.excluded = not item.excluded
    db.session.commit()
    return redirect(url_for("item_detail", house_id=house_id, item_id=item.id))


@app.route("/house/<int:house_id>/item/<int:item_id>/toggle-will-change", methods=["POST"])
@login_required
def item_toggle_will_change(house_id, item_id):
    item = get_owned_item(house_id, item_id)
    item.will_change = not item.will_change
    db.session.commit()
    return redirect(url_for("item_detail", house_id=house_id, item_id=item.id))


@app.route("/house/<int:house_id>/item/<int:item_id>/material/new", methods=["POST"])
@login_required
def material_new(house_id, item_id):
    get_owned_item(house_id, item_id)
    m = Material(
        item_id=item_id,
        name=(request.form.get("name") or "")[:200],
        shop=(request.form.get("shop") or "")[:200],
        price=clamp_number(request.form.get("price")),
        description=(request.form.get("description") or "")[:5000],
        link=(request.form.get("link") or "")[:400],
    )
    db.session.add(m)
    db.session.commit()
    return redirect(url_for("item_detail", house_id=house_id, item_id=item_id))


@app.route("/house/<int:house_id>/item/<int:item_id>/material/from-saved/<int:pid>", methods=["POST"])
@login_required
def material_from_saved(house_id, item_id, pid):
    get_owned_item(house_id, item_id)
    p = get_owned_saved_product(pid)
    m = Material(item_id=item_id, name=p.name, shop=p.shop, price=p.price,
                 description=p.description, link=p.link)
    db.session.add(m)
    db.session.commit()
    return redirect(url_for("item_detail", house_id=house_id, item_id=item_id))


@app.route("/house/<int:house_id>/material/<int:material_id>/edit", methods=["GET", "POST"])
@login_required
def material_edit(house_id, material_id):
    house = get_owned_house(house_id)
    m = get_owned_material(house_id, material_id)
    if request.method == "POST":
        m.name = (request.form.get("name") or "")[:200]
        m.shop = (request.form.get("shop") or "")[:200]
        m.price = clamp_number(request.form.get("price"))
        m.description = (request.form.get("description") or "")[:5000]
        m.link = (request.form.get("link") or "")[:400]
        db.session.commit()
        return redirect(url_for("item_detail", house_id=house_id, item_id=m.item_id))
    return render_template("material_edit.html", house=house, material=m, item=m.item)


@app.route("/house/<int:house_id>/material/<int:material_id>/toggle-include", methods=["POST"])
@login_required
def material_toggle_include(house_id, material_id):
    m = get_owned_material(house_id, material_id)
    m.include_in_budget = not m.include_in_budget
    db.session.commit()
    return redirect(url_for("item_detail", house_id=house_id, item_id=m.item_id))


@app.route("/house/<int:house_id>/material/<int:material_id>/delete", methods=["GET"])
@login_required
def material_delete_confirm(house_id, material_id):
    house = get_owned_house(house_id)
    m = get_owned_material(house_id, material_id)
    return render_template("confirm_delete.html", house=house, target_name=m.name,
                            action_url=url_for("material_delete", house_id=house_id, material_id=material_id),
                            cancel_url=url_for("item_detail", house_id=house_id, item_id=m.item_id))


@app.route("/house/<int:house_id>/material/<int:material_id>/delete", methods=["POST"])
@login_required
def material_delete(house_id, material_id):
    m = get_owned_material(house_id, material_id)
    item_id = m.item_id
    db.session.delete(m)
    db.session.commit()
    return redirect(url_for("item_detail", house_id=house_id, item_id=item_id))


@app.route("/house/<int:house_id>/segment/<segment_key>/document/new", methods=["POST"])
@login_required
def document_new(house_id, segment_key):
    get_owned_house(house_id)
    if not Segment.query.filter_by(house_id=house_id, key=segment_key).first():
        abort(404)
    item_id_raw = request.form.get("item_id")
    item_id = None
    if item_id_raw:
        item = get_owned_item(house_id, int(item_id_raw))
        item_id = item.id
    name = (request.form.get("name") or "").strip()[:200]
    if not name:
        return redirect(request.referrer or url_for("segment_view", house_id=house_id, segment_key=segment_key))
    filename, file_type = save_document(request.files.get("file"))
    doc = Document(
        house_id=house_id, segment_key=segment_key, item_id=item_id,
        name=name, description=(request.form.get("description") or "")[:5000],
        filename=filename, file_type=file_type,
    )
    db.session.add(doc)
    db.session.commit()
    return redirect(request.form.get("next") or url_for("segment_view", house_id=house_id, segment_key=segment_key))


@app.route("/house/<int:house_id>/document/<int:doc_id>/delete", methods=["GET"])
@login_required
def document_delete_confirm(house_id, doc_id):
    house = get_owned_house(house_id)
    d = get_owned_document(house_id, doc_id)
    referrer = request.referrer or ""
    cancel_url = referrer if referrer.startswith(request.host_url) else url_for("segment_view", house_id=house_id, segment_key=d.segment_key)
    return render_template("confirm_delete.html", house=house, target_name=d.name,
                            action_url=url_for("document_delete", house_id=house_id, doc_id=doc_id),
                            cancel_url=cancel_url, next_url=cancel_url)


@app.route("/house/<int:house_id>/document/<int:doc_id>/delete", methods=["POST"])
@login_required
def document_delete(house_id, doc_id):
    d = get_owned_document(house_id, doc_id)
    segment_key = d.segment_key
    db.session.delete(d)
    db.session.commit()
    return safe_next_redirect(request.form.get("next"), "segment_view", house_id=house_id, segment_key=segment_key)


@app.route("/house/<int:house_id>/segment/wykonczenie/produkt-m2/apply", methods=["POST"])
@login_required
def product_m2_apply(house_id):
    get_owned_house(house_id)
    name = (request.form.get("name") or "").strip()[:200]
    shop = (request.form.get("shop") or "")[:200]
    surface_type = request.form.get("surface_type") or "floor"
    if surface_type not in ("floor", "wall"):
        surface_type = "floor"
    price_per_m2 = clamp_number(request.form.get("price_per_m2"), min_val=0, max_val=100000)
    room_ids = request.form.getlist("room_ids")

    if not name or not price_per_m2 or not room_ids:
        return redirect(url_for("segment_view", house_id=house_id, segment_key="wykonczenie"))

    created = 0
    for rid in room_ids:
        try:
            room = get_owned_item(house_id, int(rid))
        except (ValueError, TypeError):
            continue
        if not room.is_room:
            continue
        area = room.wall_area if surface_type == "wall" else room.computed_area
        if not area:
            continue
        total_price = round(price_per_m2 * area, 2)
        surface_label = "ściany" if surface_type == "wall" else "podłoga"
        m = Material(
            item_id=room.id, name=name, shop=shop,
            price=total_price, price_per_m2=price_per_m2, surface_type=surface_type,
            description=f"{price_per_m2:.2f} zł/m² × {area:.2f} m² ({surface_label})",
        )
        db.session.add(m)
        created += 1
    db.session.commit()
    return redirect(url_for("segment_view", house_id=house_id, segment_key="wykonczenie"))


@app.route("/house/<int:house_id>/item/<int:item_id>/task/new", methods=["POST"])
@login_required
def task_new(house_id, item_id):
    get_owned_item(house_id, item_id)
    text = (request.form.get("text") or "").strip()[:300]
    if text:
        db.session.add(Task(item_id=item_id, text=text))
        db.session.commit()
    return redirect(url_for("item_detail", house_id=house_id, item_id=item_id))


@app.route("/house/<int:house_id>/task/<int:task_id>/toggle", methods=["POST"])
@login_required
def task_toggle(house_id, task_id):
    t = get_owned_task(house_id, task_id)
    t.done = not t.done
    db.session.commit()
    return redirect(url_for("item_detail", house_id=house_id, item_id=t.item_id))


@app.route("/house/<int:house_id>/task/<int:task_id>/delete", methods=["POST"])
@login_required
def task_delete(house_id, task_id):
    t = get_owned_task(house_id, task_id)
    item_id = t.item_id
    db.session.delete(t)
    db.session.commit()
    return redirect(url_for("item_detail", house_id=house_id, item_id=item_id))


@app.route("/house/<int:house_id>/item/<int:item_id>/variant/diy", methods=["POST"])
@login_required
def item_variant_diy(house_id, item_id):
    item = get_owned_item(house_id, item_id)
    v = Variant(item_id=item.id, material_name=DIY_LABEL, labor_contractor=DIY_LABEL,
                labor_included=True, material_price=0, selected=True)
    db.session.add(v)
    db.session.commit()
    return redirect(url_for("item_detail", house_id=house_id, item_id=item.id))


@app.route("/house/<int:house_id>/item/<int:item_id>/variant/add", methods=["GET", "POST"])
@login_required
def item_variant_add(house_id, item_id):
    house = get_owned_house(house_id)
    item = get_owned_item(house_id, item_id)

    if request.method == "POST":
        if item.template == 1:
            if request.form.get("labor_mode") == "self":
                labor_contractor = "robię sam(a)"
                labor_price = 0.0
                labor_included = True
                material_name = (request.form.get("material_name") or "")[:200]
                material_company = (request.form.get("material_company") or "")[:200]
                material_price = clamp_number(request.form.get("material_price"))
            else:
                labor_contractor = (request.form.get("labor_contractor") or "")[:200]
                labor_price = clamp_number(request.form.get("labor_price"))
                labor_included = False  # "montaz wliczony" nie istnieje juz - material to osobna, opcjonalna sekcja
                material_name = (request.form.get("material_name_c") or "")[:200]
                material_company = (request.form.get("material_company_c") or "")[:200]
                material_price = clamp_number(request.form.get("material_price_c"))
        else:
            labor_contractor = (request.form.get("labor_contractor") or "")[:200]
            labor_price = 0.0
            labor_included = False
            material_name = (request.form.get("material_name") or "")[:200]
            material_company = ""
            material_price = clamp_number(request.form.get("material_price"))
        variant = Variant(
            item_id=item.id,
            material_name=material_name,
            material_company=material_company,
            material_price=material_price,
            labor_contractor=labor_contractor,
            labor_price=labor_price,
            labor_included=labor_included,
            link=(request.form.get("link") or "")[:400],
            note=(request.form.get("note") or "")[:5000],
            selected=True,
        )
        if request.form.get("excluded") == "on":
            item.excluded = True
        db.session.add(variant)
        db.session.commit()
        return redirect(url_for("item_detail", house_id=house_id, item_id=item.id))

    saved = SavedProduct.query.filter_by(user_id=current_user.id).all()
    return render_template("item_variant_add.html", house=house, item=item, saved=saved)


@app.route("/house/<int:house_id>/item/<int:item_id>/variant/add-from-saved/<int:saved_id>", methods=["POST"])
@login_required
def item_variant_from_saved(house_id, item_id, saved_id):
    item = get_owned_item(house_id, item_id)
    s = get_owned_saved_product(saved_id)
    variant = Variant(
        item_id=item.id,
        material_name=s.name,
        material_company=s.shop,
        material_price=s.price or 0,
        link=s.link,
        note=s.description,
        selected=True,
    )
    db.session.add(variant)
    db.session.commit()
    return redirect(url_for("item_detail", house_id=house_id, item_id=item.id))


@app.route("/robocze")
@login_required
def robocze():
    products = SavedProduct.query.filter_by(user_id=current_user.id).all()
    services = SavedService.query.filter_by(user_id=current_user.id).all()
    inspirations = SavedInspiration.query.filter_by(user_id=current_user.id).all()
    return render_template("robocze.html", products=products, services=services,
                            inspirations=inspirations, all_tags=SERVICE_TAGS)


@app.route("/robocze/product/new", methods=["POST"])
@login_required
def saved_product_new():
    p = SavedProduct(
        user_id=current_user.id, name=(request.form.get("name") or "")[:200], shop=(request.form.get("shop") or "")[:200],
        price=clamp_number(request.form.get("price")), description=(request.form.get("description") or "")[:5000],
        link=(request.form.get("link") or "")[:400],
    )
    db.session.add(p)
    db.session.commit()
    return redirect(url_for("robocze"))


@app.route("/robocze/product/<int:pid>/edit", methods=["GET", "POST"])
@login_required
def saved_product_edit(pid):
    p = SavedProduct.query.get_or_404(pid)
    if p.user_id != current_user.id:
        abort(404)
    if request.method == "POST":
        p.name = (request.form.get("name") or "")[:200]
        p.shop = (request.form.get("shop") or "")[:200]
        p.price = clamp_number(request.form.get("price"))
        p.description = (request.form.get("description") or "")[:5000]
        p.link = (request.form.get("link") or "")[:400]
        db.session.commit()
        return redirect(url_for("robocze"))
    return render_template("saved_product_edit.html", product=p)


@app.route("/robocze/product/<int:pid>/delete", methods=["GET"])
@login_required
def saved_product_delete_confirm(pid):
    p = SavedProduct.query.get_or_404(pid)
    if p.user_id != current_user.id:
        abort(404)
    return render_template("confirm_delete.html", target_name=p.name,
                            action_url=url_for("saved_product_delete", pid=pid), cancel_url=url_for("robocze"))


@app.route("/robocze/product/<int:pid>/delete", methods=["POST"])
@login_required
def saved_product_delete(pid):
    p = SavedProduct.query.get_or_404(pid)
    if p.user_id != current_user.id:
        abort(404)
    db.session.delete(p)
    db.session.commit()
    return redirect(url_for("robocze"))


EXAMPLE_SERVICES = [
    {"company": "Bracia Kowalscy", "tags": ["tynki", "płyty G-K"],
     "description": "polecani, szybki termin realizacji", "link": ""},
    {"company": "ElektroMax", "tags": ["elektryk (gniazdka)"],
     "description": "instalacje elektryczne, pomiary", "link": ""},
    {"company": "HydroFlow", "tags": ["hydraulik (rurowanie)"],
     "description": "hydraulika, ogrzewanie podłogowe", "link": ""},
    {"company": "Parkiet Expert", "tags": ["parkiety", "panele"],
     "description": "układanie i cyklinowanie", "link": ""},
    {"company": "GlazBud", "tags": ["glazurnik", "posadzki"],
     "description": "glazura, terakota, mozaiki", "link": ""},
    {"company": "MalPro", "tags": ["malarz"],
     "description": "malowanie, gładzie, tapetowanie", "link": ""},
    {"company": "StolMistrz", "tags": ["stolarz"],
     "description": "meble na wymiar, zabudowy", "link": ""},
    {"company": "OciepleniaPlus", "tags": ["ocieplenia"],
     "description": "ocieplenia elewacji i poddaszy", "link": ""},
    {"company": "TapicerArt", "tags": ["tapicer"],
     "description": "tapicerowanie mebli i wnęk", "link": ""},
    {"company": "Wszystko w Jednym", "tags": ["tynki", "malarz", "płyty G-K"],
     "description": "ekipa remontowa - kompleksowo", "link": ""},
]


@app.route("/robocze/seed-example-services", methods=["POST"])
@login_required
def seed_example_services():
    existing_companies = {s.company for s in SavedService.query.filter_by(user_id=current_user.id).all()}
    for ex in EXAMPLE_SERVICES:
        if ex["company"] in existing_companies:
            continue
        db.session.add(SavedService(
            user_id=current_user.id, company=ex["company"], tags=",".join(ex["tags"]),
            description=ex["description"], link=ex["link"] or None,
        ))
    db.session.commit()
    return redirect(url_for("robocze"))


@app.route("/robocze/service/new", methods=["POST"])
@login_required
def saved_service_new():
    tags_selected = request.form.getlist("tags")
    custom_tag = (request.form.get("custom_tag") or "").strip()
    if custom_tag:
        tags_selected.append(custom_tag)
    s = SavedService(
        user_id=current_user.id, company=(request.form.get("company") or "")[:200],
        tags=",".join(tags_selected), link=(request.form.get("link") or "")[:400],
        description=(request.form.get("description") or "")[:5000],
    )
    db.session.add(s)
    db.session.commit()
    return redirect(url_for("robocze"))


@app.route("/robocze/service/<int:sid>/edit", methods=["GET", "POST"])
@login_required
def saved_service_edit(sid):
    s = SavedService.query.get_or_404(sid)
    if s.user_id != current_user.id:
        abort(404)
    if request.method == "POST":
        tags_selected = request.form.getlist("tags")
        custom_tag = (request.form.get("custom_tag") or "").strip()
        if custom_tag:
            tags_selected.append(custom_tag)
        s.company = (request.form.get("company") or "")[:200]
        s.tags = ",".join(tags_selected)
        s.link = (request.form.get("link") or "")[:400]
        s.description = (request.form.get("description") or "")[:5000]
        db.session.commit()
        return redirect(url_for("robocze"))
    return render_template("saved_service_edit.html", service=s, all_tags=SERVICE_TAGS)


@app.route("/robocze/service/<int:sid>/delete", methods=["GET"])
@login_required
def saved_service_delete_confirm(sid):
    s = SavedService.query.get_or_404(sid)
    if s.user_id != current_user.id:
        abort(404)
    return render_template("confirm_delete.html", target_name=s.company,
                            action_url=url_for("saved_service_delete", sid=sid), cancel_url=url_for("robocze"))


@app.route("/robocze/service/<int:sid>/delete", methods=["POST"])
@login_required
def saved_service_delete(sid):
    s = SavedService.query.get_or_404(sid)
    if s.user_id != current_user.id:
        abort(404)
    db.session.delete(s)
    db.session.commit()
    return redirect(url_for("robocze"))


@app.route("/robocze/inspiracja/new", methods=["POST"])
@login_required
def saved_inspiration_new():
    i = SavedInspiration(
        user_id=current_user.id, name=(request.form.get("name") or "")[:200],
        description=(request.form.get("description") or "")[:5000], link=(request.form.get("link") or "")[:400],
        photo_filename=save_photo(request.files.get("photo")),
    )
    db.session.add(i)
    db.session.commit()
    return redirect(url_for("robocze"))


@app.route("/robocze/inspiracja/<int:iid>/edit", methods=["GET", "POST"])
@login_required
def saved_inspiration_edit(iid):
    i = SavedInspiration.query.get_or_404(iid)
    if i.user_id != current_user.id:
        abort(404)
    if request.method == "POST":
        i.name = (request.form.get("name") or "")[:200]
        i.description = (request.form.get("description") or "")[:5000]
        i.link = (request.form.get("link") or "")[:400]
        new_photo = save_photo(request.files.get("photo"))
        if new_photo:
            i.photo_filename = new_photo
        db.session.commit()
        return redirect(url_for("robocze"))
    return render_template("saved_inspiration_edit.html", inspiration=i)


@app.route("/robocze/inspiracja/<int:iid>/delete", methods=["GET"])
@login_required
def saved_inspiration_delete_confirm(iid):
    i = SavedInspiration.query.get_or_404(iid)
    if i.user_id != current_user.id:
        abort(404)
    return render_template("confirm_delete.html", target_name=i.name,
                            action_url=url_for("saved_inspiration_delete", iid=iid), cancel_url=url_for("robocze"))


@app.route("/robocze/inspiracja/<int:iid>/delete", methods=["POST"])
@login_required
def saved_inspiration_delete(iid):
    i = SavedInspiration.query.get_or_404(iid)
    if i.user_id != current_user.id:
        abort(404)
    db.session.delete(i)
    db.session.commit()
    return redirect(url_for("robocze"))


@app.route("/house/<int:house_id>/inspiracje")
@login_required
def inspiracje(house_id):
    house = get_owned_house(house_id)
    unassigned = SavedInspiration.query.filter_by(user_id=current_user.id).all()
    return render_template("inspiracje.html", house=house, unassigned=unassigned)


@app.route("/house/<int:house_id>/inspiracje/category/new", methods=["POST"])
@login_required
def inspiracje_category_new(house_id):
    get_owned_house(house_id)
    name = ((request.form.get("name") or "")[:200] or "").strip()[:100]
    if name:
        db.session.add(InspirationCategory(house_id=house_id, name=name))
        db.session.commit()
    return redirect(url_for("inspiracje", house_id=house_id))


def safe_next_redirect(next_url, fallback_endpoint, **fallback_kwargs):
    """Przekierowuje na 'next' TYLKO jesli to wzgledna sciezka albo adres tej samej domeny
    (ochrona przed open redirect na obcy adres)."""
    if next_url:
        if next_url.startswith("/") and not next_url.startswith("//"):
            return redirect(next_url)
        if next_url.startswith(request.host_url):
            return redirect(next_url)
    return redirect(url_for(fallback_endpoint, **fallback_kwargs))


@app.route("/house/<int:house_id>/inspiracje/category/<int:category_id>/add", methods=["POST"])
@login_required
def inspiracje_tile_add(house_id, category_id):
    cat = get_owned_category(house_id, category_id)
    tile = InspirationTile(
        category_id=category_id,
        name=request.form["name"][:200],
        description=(request.form.get("description") or "")[:5000],
        link=(request.form.get("link") or "")[:400],
        photo_filename=save_photo(request.files.get("photo")),
    )
    db.session.add(tile)
    db.session.commit()
    return safe_next_redirect(request.form.get("next"), "inspiracje", house_id=house_id)


@app.route("/house/<int:house_id>/inspiracje/from-saved/<int:iid>", methods=["POST"])
@login_required
def inspiracje_tile_from_saved(house_id, iid):
    get_owned_house(house_id)
    saved = get_owned_saved_inspiration(iid)
    category_id = clamp_int(request.form.get("category_id"), min_val=0, max_val=2**31)
    cat = get_owned_category(house_id, category_id)
    tile = InspirationTile(
        category_id=category_id, name=saved.name, description=saved.description,
        link=saved.link, photo_filename=saved.photo_filename,
    )
    db.session.add(tile)
    db.session.commit()
    return safe_next_redirect(request.form.get("next"), "inspiracje", house_id=house_id)


@app.route("/house/<int:house_id>/inspiracje/tile/<int:tile_id>/delete", methods=["GET"])
@login_required
def inspiracje_tile_delete_confirm(house_id, tile_id):
    house = get_owned_house(house_id)
    t = get_owned_tile(house_id, tile_id)
    referrer = request.referrer or ""
    cancel_url = referrer if referrer.startswith(request.host_url) else url_for("inspiracje", house_id=house_id)
    return render_template("confirm_delete.html", house=house, target_name=t.name,
                            action_url=url_for("inspiracje_tile_delete", house_id=house_id, tile_id=tile_id),
                            cancel_url=cancel_url, next_url=cancel_url)


@app.route("/house/<int:house_id>/inspiracje/tile/<int:tile_id>/delete", methods=["POST"])
@login_required
def inspiracje_tile_delete(house_id, tile_id):
    t = get_owned_tile(house_id, tile_id)
    db.session.delete(t)
    db.session.commit()
    return safe_next_redirect(request.form.get("next"), "inspiracje", house_id=house_id)


import re as _re

def password_error(password):
    if len(password) < 8:
        return "hasło musi mieć co najmniej 8 znaków"
    if not _re.search(r"[A-ZĄĆĘŁŃÓŚŹŻ]", password):
        return "hasło musi zawierać wielką literę"
    if not _re.search(r"[a-ząćęłńóśźż]", password):
        return "hasło musi zawierać małą literę"
    if not _re.search(r"[0-9]", password):
        return "hasło musi zawierać cyfrę"
    return None


@app.route("/account", methods=["GET"])
@login_required
def account():
    return render_template("account.html", error=None, success=None)


@app.route("/account/email", methods=["POST"])
@login_required
def account_change_email():
    new_email = (request.form.get("email") or "").strip().lower()
    error = success = None
    if not new_email:
        error = "podaj nowy e-mail"
    elif User.query.filter(User.email == new_email, User.id != current_user.id).first():
        error = "ten e-mail jest już zajęty"
    else:
        current_user.email = new_email
        db.session.commit()
        success = "e-mail zmieniony"
    return render_template("account.html", error=error, success=success)


@app.route("/account/password", methods=["POST"])
@login_required
@limiter.limit("10 per hour")
def account_change_password():
    old_password = request.form.get("old_password") or ""
    new_password = request.form.get("new_password") or ""
    new_password2 = request.form.get("new_password2") or ""
    error = success = None
    if not current_user.check_password(old_password):
        error = "aktualne hasło jest nieprawidłowe"
    elif new_password != new_password2:
        error = "nowe hasła nie są takie same"
    elif password_error(new_password):
        error = password_error(new_password)
    else:
        current_user.set_password(new_password)
        db.session.commit()
        success = "hasło zmienione"
    return render_template("account.html", error=error, success=success)


@app.route("/account/delete", methods=["GET"])
@login_required
def account_delete_confirm():
    return render_template("confirm_delete.html", target_name="Twoje konto i wszystkie Twoje projekty",
                            action_url=url_for("account_delete"), cancel_url=url_for("account"))


@app.route("/account/delete", methods=["POST"])
@login_required
def account_delete():
    user = db.session.get(User, current_user.id)
    logout_user()
    db.session.delete(user)
    db.session.commit()
    return redirect(url_for("landing"))


@app.route("/house/<int:house_id>/wspolpraca")
@login_required
def house_collab(house_id):
    house = get_owned_house(house_id)
    return render_template("house_collab.html", house=house, is_owner=is_house_owner(house),
                            collaborators=house.collaborators, invites=house.invites)


@app.route("/house/<int:house_id>/wspolpraca/zapros", methods=["POST"])
@login_required
@limiter.limit("20 per hour")
def house_collab_invite(house_id):
    house = get_owned_house(house_id)
    if not is_house_owner(house):
        abort(403)
    email = (request.form.get("email") or "").strip().lower()[:200]
    error = None
    if not email:
        error = "podaj e-mail"
    elif email == current_user.email:
        error = "to Twój własny adres e-mail"
    elif HouseCollaborator.query.join(User).filter(
            HouseCollaborator.house_id == house_id, User.email == email).first():
        error = "ta osoba już ma dostęp do projektu"
    elif HouseInvite.query.filter_by(house_id=house_id, invited_email=email).first():
        error = "zaproszenie do tej osoby już czeka na odpowiedź"
    else:
        db.session.add(HouseInvite(house_id=house_id, invited_email=email, invited_by_id=current_user.id))
        db.session.commit()
    if error:
        return render_template("house_collab.html", house=house, is_owner=True,
                                collaborators=house.collaborators, invites=house.invites, error=error)
    return redirect(url_for("house_collab", house_id=house_id))


@app.route("/house/<int:house_id>/wspolpraca/zaproszenie/<int:invite_id>/anuluj", methods=["POST"])
@login_required
def house_collab_cancel_invite(house_id, invite_id):
    house = get_owned_house(house_id)
    if not is_house_owner(house):
        abort(403)
    inv = HouseInvite.query.get_or_404(invite_id)
    if inv.house_id != house_id:
        abort(404)
    db.session.delete(inv)
    db.session.commit()
    return redirect(url_for("house_collab", house_id=house_id))


@app.route("/house/<int:house_id>/wspolpraca/<int:collab_id>/usun", methods=["POST"])
@login_required
def house_collab_remove(house_id, collab_id):
    house = get_owned_house(house_id)
    if not is_house_owner(house):
        abort(403)
    collab = HouseCollaborator.query.get_or_404(collab_id)
    if collab.house_id != house_id:
        abort(404)
    db.session.delete(collab)
    db.session.commit()
    return redirect(url_for("house_collab", house_id=house_id))


@app.route("/house/<int:house_id>/opusc", methods=["POST"])
@login_required
def house_leave(house_id):
    house = get_owned_house(house_id)
    if is_house_owner(house):
        abort(403)  # wlasciciel nie moze "opuscic" wlasnego domu
    collab = HouseCollaborator.query.filter_by(house_id=house_id, user_id=current_user.id).first()
    if collab:
        db.session.delete(collab)
        db.session.commit()
    return redirect(url_for("index"))


@app.route("/zaproszenia/<int:invite_id>/akceptuj", methods=["POST"])
@login_required
def invite_accept(invite_id):
    inv = HouseInvite.query.get_or_404(invite_id)
    if inv.invited_email != current_user.email:
        abort(404)
    db.session.add(HouseCollaborator(house_id=inv.house_id, user_id=current_user.id))
    house_id = inv.house_id
    db.session.delete(inv)
    db.session.commit()
    return redirect(url_for("dashboard", house_id=house_id))


@app.route("/zaproszenia/<int:invite_id>/odrzuc", methods=["POST"])
@login_required
def invite_decline(invite_id):
    inv = HouseInvite.query.get_or_404(invite_id)
    if inv.invited_email != current_user.email:
        abort(404)
    db.session.delete(inv)
    db.session.commit()
    return redirect(url_for("index"))


@app.route("/register", methods=["GET", "POST"])
@limiter.limit("10 per hour")
def register():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    error = None
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()[:200]
        password = request.form.get("password") or ""
        password2 = request.form.get("password2") or ""
        if not email or not password:
            error = "podaj e-mail i hasło"
        elif password != password2:
            error = "hasła nie są takie same"
        elif password_error(password):
            error = password_error(password)
        elif User.query.filter_by(email=email).first():
            error = "konto z tym e-mailem już istnieje"
        else:
            u = User(email=email)
            u.set_password(password)
            db.session.add(u)
            db.session.commit()
            login_user(u)
            return redirect(url_for("index"))
    return render_template("register.html", error=error)


@app.route("/login", methods=["GET", "POST"])
@limiter.limit("15 per hour")
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    error = None
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()[:200]
        password = request.form.get("password") or ""
        u = User.query.filter_by(email=email).first()
        if u and u.check_password(password):
            login_user(u)
            return safe_next_redirect(request.args.get("next"), "index")
        error = "nieprawidłowy e-mail lub hasło"
    return render_template("login.html", error=error)


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


with app.app_context():
    db.create_all()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug_mode = os.environ.get("FLASK_DEBUG", "1") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug_mode)
