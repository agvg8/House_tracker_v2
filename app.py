from flask import Flask, render_template, request, redirect, url_for, abort
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
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
db = SQLAlchemy(app)

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


DEFAULT_SEGMENTS = [
    ("stan0", "stan 0", "shovel"),
    ("surowy_otwarty", "stan surowy otwarty", "wall"),
    ("surowy_zamkniety", "stan surowy zamknięty", "window"),
    ("instalacje", "instalacje", "plug"),
    ("wykonczenie", "wykończenie", "paint"),
]

# szablon 1 = material + wykonawca, 2 = szacowana wycena + wykonawca + info, 3 = urzad: dokument + oplata + info
DEFAULT_ITEMS = {
    "stan0": [
        ("ogrodzenie", 1), ("geodeta", 2), ("przyłącze: woda", 2), ("przyłącze: szambo", 2),
        ("przyłącze: prąd", 2), ("przyłącze: gaz", 2), ("urząd", 3), ("fundamenty", 1),
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
    saved_solutions = db.relationship("SavedSolution", backref="owner", cascade="all, delete-orphan")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


def get_owned_house(house_id):
    house = House.query.get_or_404(house_id)
    if house.user_id != current_user.id:
        abort(404)
    return house


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
    variants = db.relationship("Variant", backref="item", cascade="all, delete-orphan")
    room_quotes = db.relationship("RoomQuote", backref="item", cascade="all, delete-orphan")
    materials = db.relationship("Material", backref="item", cascade="all, delete-orphan")
    tasks = db.relationship("Task", backref="item", cascade="all, delete-orphan")

    @property
    def icon(self):
        return get_item_icon(self)

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


class SavedSolution(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    category = db.Column(db.String(100))
    name = db.Column(db.String(200), nullable=False)
    company = db.Column(db.String(200))
    price_min = db.Column(db.Float)
    price_max = db.Column(db.Float)
    link = db.Column(db.String(400))
    note = db.Column(db.Text)


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


class Task(db.Model):
    """Zadanie na liscie 'do zrobienia' per pokoj."""
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    text = db.Column(db.String(300), nullable=False)
    done = db.Column(db.Boolean, default=False)


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
    if item.template == 3:
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
    houses = House.query.filter_by(user_id=current_user.id).all()
    return render_template("index.html", houses=houses, type_icons=PROJECT_TYPE_ICONS, type_names=PROJECT_TYPE_NAMES)


@app.route("/house/new", methods=["GET", "POST"])
@login_required
def house_new():
    if request.method == "POST":
        project_type = request.form.get("project_type") or "dom"
        budget_raw = request.form.get("budget_total")
        h = House(
            user_id=current_user.id,
            name=request.form["name"],
            link=request.form.get("link"),
            area_m2=float(request.form.get("area_m2") or 0),
            rooms=int(request.form.get("rooms") or 0),
            project_types=project_type,
            budget_total=float(budget_raw) if budget_raw else None,
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

    center_label = "tys zł łącznie"
    if house.budget_total:
        center_value = f"{(remaining/1000):.0f}"
        center_label = "tys zł pozostało"
    else:
        center_value = f"{(lo_total/1000):.0f}–{(hi_total/1000):.0f}"

    return render_template("dashboard.html", house=house, lo_total=lo_total, hi_total=hi_total,
                            breakdown=breakdown, slices=slices, center_value=center_value, center_label=center_label)


@app.route("/house/<int:house_id>/segment/new", methods=["POST"])
@login_required
def segment_new(house_id):
    name = request.form.get("name")
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
                room_quote_sums[it.id] = sum(q.price for q in it.room_quotes)

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

    return render_template("segment.html", house=house, segment=seg, items=items, lo=lo, hi=hi,
                            services=services, all_tags=all_tags, active_tag=active_tag,
                            room_tile_counts=room_tile_counts, room_quote_sums=room_quote_sums)


@app.route("/house/<int:house_id>/services/new", methods=["POST"])
@login_required
def service_new(house_id):
    tags_selected = request.form.getlist("tags")
    custom_tag = (request.form.get("custom_tag") or "").strip()
    if custom_tag:
        tags_selected.append(custom_tag)
    s = Service(
        house_id=house_id,
        company=request.form.get("company"),
        link=request.form.get("link"),
        description=request.form.get("description"),
        tags=",".join(tags_selected),
    )
    db.session.add(s)
    db.session.commit()
    return redirect(url_for("segment_view", house_id=house_id, segment_key="wykonczenie"))


@app.route("/house/<int:house_id>/services/<int:service_id>/edit", methods=["GET", "POST"])
@login_required
def service_edit(house_id, service_id):
    house = get_owned_house(house_id)
    s = Service.query.get_or_404(service_id)
    if request.method == "POST":
        tags_selected = request.form.getlist("tags")
        custom_tag = (request.form.get("custom_tag") or "").strip()
        if custom_tag:
            tags_selected.append(custom_tag)
        s.company = request.form.get("company")
        s.link = request.form.get("link")
        s.description = request.form.get("description")
        s.tags = ",".join(tags_selected)
        db.session.commit()
        return redirect(url_for("segment_view", house_id=house_id, segment_key="wykonczenie"))
    return render_template("service_edit.html", house=house, service=s, all_tags=SERVICE_TAGS)


@app.route("/house/<int:house_id>/services/<int:service_id>/delete", methods=["GET"])
@login_required
def service_delete_confirm(house_id, service_id):
    house = get_owned_house(house_id)
    s = Service.query.get_or_404(service_id)
    return render_template("confirm_delete.html", house=house, target_name=s.company or "ta usługa",
                            action_url=url_for("service_delete", house_id=house_id, service_id=service_id),
                            cancel_url=url_for("segment_view", house_id=house_id, segment_key="wykonczenie"))


@app.route("/house/<int:house_id>/services/<int:service_id>/delete", methods=["POST"])
@login_required
def service_delete(house_id, service_id):
    s = Service.query.get_or_404(service_id)
    db.session.delete(s)
    db.session.commit()
    return redirect(url_for("segment_view", house_id=house_id, segment_key="wykonczenie"))


@app.route("/house/<int:house_id>/item/<int:item_id>/quote/new")
@login_required
def quote_new(house_id, item_id):
    house = get_owned_house(house_id)
    item = Item.query.get_or_404(item_id)
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
    q = RoomQuote(
        item_id=item_id,
        service_id=service_id,
        tag=request.form.get("tag"),
        price=float(request.form.get("price") or 0),
        note=request.form.get("note"),
    )
    db.session.add(q)
    db.session.commit()
    return redirect(url_for("item_detail", house_id=house_id, item_id=item_id))


@app.route("/house/<int:house_id>/item/<int:item_id>/quote/diy", methods=["POST"])
@login_required
def quote_diy(house_id, item_id):
    q = RoomQuote(item_id=item_id, service_id=None, tag=DIY_LABEL,
                  price=float(request.form.get("price") or 0), note=request.form.get("note"))
    db.session.add(q)
    db.session.commit()
    return redirect(url_for("item_detail", house_id=house_id, item_id=item_id))


@app.route("/house/<int:house_id>/quote/<int:quote_id>/toggle-include", methods=["POST"])
@login_required
def quote_toggle_include(house_id, quote_id):
    q = RoomQuote.query.get_or_404(quote_id)
    q.include_in_budget = not q.include_in_budget
    db.session.commit()
    return redirect(url_for("item_detail", house_id=house_id, item_id=q.item_id))


@app.route("/house/<int:house_id>/quote/<int:quote_id>/delete", methods=["GET"])
@login_required
def quote_delete_confirm(house_id, quote_id):
    house = get_owned_house(house_id)
    q = RoomQuote.query.get_or_404(quote_id)
    label = (q.service.company if q.service else q.tag) or "ta wycena"
    return render_template("confirm_delete.html", house=house, target_name=label,
                            action_url=url_for("quote_delete", house_id=house_id, quote_id=quote_id),
                            cancel_url=url_for("item_detail", house_id=house_id, item_id=q.item_id))


@app.route("/house/<int:house_id>/quote/<int:quote_id>/delete", methods=["POST"])
@login_required
def quote_delete(house_id, quote_id):
    q = RoomQuote.query.get_or_404(quote_id)
    item_id = q.item_id
    db.session.delete(q)
    db.session.commit()
    return redirect(url_for("item_detail", house_id=house_id, item_id=item_id))


@app.route("/house/<int:house_id>/variant/<int:variant_id>/edit", methods=["GET", "POST"])
@login_required
def variant_edit(house_id, variant_id):
    house = get_owned_house(house_id)
    variant = Variant.query.get_or_404(variant_id)
    item = variant.item

    if request.method == "POST":
        variant.material_name = request.form.get("material_name")
        variant.material_company = request.form.get("material_company")
        variant.material_price = float(request.form.get("material_price") or 0)
        if item.template == 1 and request.form.get("labor_mode") == "self":
            variant.labor_contractor = "robię sam(a)"
            variant.labor_price = 0.0
            variant.labor_included = True
        else:
            variant.labor_contractor = request.form.get("labor_contractor")
            variant.labor_price = float(request.form.get("labor_price") or 0)
            variant.labor_included = request.form.get("labor_included") == "on"
        variant.link = request.form.get("link")
        variant.note = request.form.get("note")
        db.session.commit()
        return redirect(url_for("item_detail", house_id=house_id, item_id=item.id))

    return render_template("variant_edit.html", house=house, item=item, variant=variant)


@app.route("/house/<int:house_id>/variant/<int:variant_id>/set-group", methods=["POST"])
@login_required
def variant_set_group(house_id, variant_id):
    v = Variant.query.get_or_404(variant_id)
    name = (request.form.get("group_name") or "").strip()
    v.group_name = name or None
    db.session.commit()
    return redirect(url_for("item_detail", house_id=house_id, item_id=v.item_id))


@app.route("/house/<int:house_id>/variant/<int:variant_id>/toggle-include", methods=["POST"])
@login_required
def variant_toggle_include(house_id, variant_id):
    v = Variant.query.get_or_404(variant_id)
    v.include_in_budget = not v.include_in_budget
    db.session.commit()
    return redirect(url_for("item_detail", house_id=house_id, item_id=v.item_id))


@app.route("/house/<int:house_id>/variant/<int:variant_id>/delete", methods=["GET"])
@login_required
def variant_delete_confirm(house_id, variant_id):
    house = get_owned_house(house_id)
    v = Variant.query.get_or_404(variant_id)
    return render_template("confirm_delete.html", house=house, target_name=v.material_name or "ta opcja",
                            action_url=url_for("variant_delete", house_id=house_id, variant_id=variant_id),
                            cancel_url=url_for("item_detail", house_id=house_id, item_id=v.item_id))


@app.route("/house/<int:house_id>/variant/<int:variant_id>/delete", methods=["POST"])
@login_required
def variant_delete(house_id, variant_id):
    v = Variant.query.get_or_404(variant_id)
    item_id = v.item_id
    db.session.delete(v)
    db.session.commit()
    return redirect(url_for("item_detail", house_id=house_id, item_id=item_id))


@app.route("/house/<int:house_id>/item/<int:item_id>")
@login_required
def item_detail(house_id, item_id):
    house = get_owned_house(house_id)
    item = Item.query.get_or_404(item_id)
    insp_category = None
    if item.segment_key == "wykonczenie":
        insp_category = InspirationCategory.query.filter_by(house_id=house_id, name=item.name).first()
        if not insp_category:
            insp_category = InspirationCategory(house_id=house_id, name=item.name)
            db.session.add(insp_category)
            db.session.commit()
    return render_template("item_detail.html", house=house, item=item, insp_category=insp_category)


@app.route("/house/<int:house_id>/segment/<segment_key>/item/new", methods=["POST"])
@login_required
def item_new(house_id, segment_key):
    name = request.form.get("name")
    template = int(request.form.get("template") or 1)
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
    item = Item.query.get_or_404(item_id)
    return render_template("confirm_delete.html", house=house, target_name=item.name,
                            action_url=url_for("item_delete", house_id=house_id, item_id=item_id),
                            cancel_url=url_for("segment_view", house_id=house_id, segment_key=item.segment_key))


@app.route("/house/<int:house_id>/item/<int:item_id>/delete", methods=["POST"])
@login_required
def item_delete(house_id, item_id):
    item = Item.query.get_or_404(item_id)
    segment_key = item.segment_key
    db.session.delete(item)
    db.session.commit()
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
    if segment_key in [k for k, n, i in DEFAULT_SEGMENTS]:
        abort(404)
    Segment.query.filter_by(house_id=house_id, key=segment_key).delete()
    Item.query.filter_by(house_id=house_id, segment_key=segment_key).delete()
    db.session.commit()
    return redirect(url_for("dashboard", house_id=house_id))


@app.route("/house/<int:house_id>/item/<int:item_id>/toggle-excluded", methods=["POST"])
@login_required
def item_toggle_excluded(house_id, item_id):
    item = Item.query.get_or_404(item_id)
    item.excluded = not item.excluded
    db.session.commit()
    return redirect(url_for("item_detail", house_id=house_id, item_id=item.id))


@app.route("/house/<int:house_id>/item/<int:item_id>/toggle-will-change", methods=["POST"])
@login_required
def item_toggle_will_change(house_id, item_id):
    item = Item.query.get_or_404(item_id)
    item.will_change = not item.will_change
    db.session.commit()
    return redirect(url_for("item_detail", house_id=house_id, item_id=item.id))


@app.route("/house/<int:house_id>/item/<int:item_id>/material/new", methods=["POST"])
@login_required
def material_new(house_id, item_id):
    m = Material(
        item_id=item_id,
        name=request.form.get("name"),
        shop=request.form.get("shop"),
        price=float(request.form.get("price") or 0),
        description=request.form.get("description"),
        link=request.form.get("link"),
    )
    db.session.add(m)
    db.session.commit()
    return redirect(url_for("item_detail", house_id=house_id, item_id=item_id))


@app.route("/house/<int:house_id>/material/<int:material_id>/edit", methods=["GET", "POST"])
@login_required
def material_edit(house_id, material_id):
    house = get_owned_house(house_id)
    m = Material.query.get_or_404(material_id)
    if request.method == "POST":
        m.name = request.form.get("name")
        m.shop = request.form.get("shop")
        m.price = float(request.form.get("price") or 0)
        m.description = request.form.get("description")
        m.link = request.form.get("link")
        db.session.commit()
        return redirect(url_for("item_detail", house_id=house_id, item_id=m.item_id))
    return render_template("material_edit.html", house=house, material=m, item=m.item)


@app.route("/house/<int:house_id>/material/<int:material_id>/toggle-include", methods=["POST"])
@login_required
def material_toggle_include(house_id, material_id):
    m = Material.query.get_or_404(material_id)
    m.include_in_budget = not m.include_in_budget
    db.session.commit()
    return redirect(url_for("item_detail", house_id=house_id, item_id=m.item_id))


@app.route("/house/<int:house_id>/material/<int:material_id>/delete", methods=["GET"])
@login_required
def material_delete_confirm(house_id, material_id):
    house = get_owned_house(house_id)
    m = Material.query.get_or_404(material_id)
    return render_template("confirm_delete.html", house=house, target_name=m.name,
                            action_url=url_for("material_delete", house_id=house_id, material_id=material_id),
                            cancel_url=url_for("item_detail", house_id=house_id, item_id=m.item_id))


@app.route("/house/<int:house_id>/material/<int:material_id>/delete", methods=["POST"])
@login_required
def material_delete(house_id, material_id):
    m = Material.query.get_or_404(material_id)
    item_id = m.item_id
    db.session.delete(m)
    db.session.commit()
    return redirect(url_for("item_detail", house_id=house_id, item_id=item_id))


@app.route("/house/<int:house_id>/item/<int:item_id>/task/new", methods=["POST"])
@login_required
def task_new(house_id, item_id):
    text = (request.form.get("text") or "").strip()
    if text:
        db.session.add(Task(item_id=item_id, text=text))
        db.session.commit()
    return redirect(url_for("item_detail", house_id=house_id, item_id=item_id))


@app.route("/house/<int:house_id>/task/<int:task_id>/toggle", methods=["POST"])
@login_required
def task_toggle(house_id, task_id):
    t = Task.query.get_or_404(task_id)
    t.done = not t.done
    db.session.commit()
    return redirect(url_for("item_detail", house_id=house_id, item_id=t.item_id))


@app.route("/house/<int:house_id>/task/<int:task_id>/delete", methods=["POST"])
@login_required
def task_delete(house_id, task_id):
    t = Task.query.get_or_404(task_id)
    item_id = t.item_id
    db.session.delete(t)
    db.session.commit()
    return redirect(url_for("item_detail", house_id=house_id, item_id=item_id))


@app.route("/house/<int:house_id>/item/<int:item_id>/variant/diy", methods=["POST"])
@login_required
def item_variant_diy(house_id, item_id):
    item = Item.query.get_or_404(item_id)
    v = Variant(item_id=item.id, material_name=DIY_LABEL, labor_contractor=DIY_LABEL,
                labor_included=True, material_price=0, selected=True)
    db.session.add(v)
    db.session.commit()
    return redirect(url_for("item_detail", house_id=house_id, item_id=item.id))


@app.route("/house/<int:house_id>/item/<int:item_id>/variant/add", methods=["GET", "POST"])
@login_required
def item_variant_add(house_id, item_id):
    house = get_owned_house(house_id)
    item = Item.query.get_or_404(item_id)

    if request.method == "POST":
        if item.template == 1 and request.form.get("labor_mode") == "self":
            labor_contractor = "robię sam(a)"
            labor_price = 0.0
            labor_included = True
        else:
            labor_contractor = request.form.get("labor_contractor")
            labor_price = float(request.form.get("labor_price") or 0)
            labor_included = request.form.get("labor_included") == "on"
        variant = Variant(
            item_id=item.id,
            material_name=request.form.get("material_name"),
            material_company=request.form.get("material_company"),
            material_price=float(request.form.get("material_price") or 0),
            labor_contractor=labor_contractor,
            labor_price=labor_price,
            labor_included=labor_included,
            link=request.form.get("link"),
            note=request.form.get("note"),
            selected=True,
        )
        if request.form.get("excluded") == "on":
            item.excluded = True
        db.session.add(variant)
        db.session.commit()
        return redirect(url_for("item_detail", house_id=house_id, item_id=item.id))

    saved = SavedSolution.query.filter(
        SavedSolution.user_id == current_user.id,
        (SavedSolution.category == item.segment_key) | (SavedSolution.category == None)
    ).all()
    return render_template("item_variant_add.html", house=house, item=item, saved=saved)


@app.route("/house/<int:house_id>/item/<int:item_id>/variant/add-from-saved/<int:saved_id>", methods=["POST"])
@login_required
def item_variant_from_saved(house_id, item_id, saved_id):
    item = Item.query.get_or_404(item_id)
    s = SavedSolution.query.get_or_404(saved_id)
    variant = Variant(
        item_id=item.id,
        material_name=s.name,
        material_company=s.company,
        material_price=s.price_min or 0,
        link=s.link,
        note=s.note,
        selected=True,
    )
    db.session.add(variant)
    db.session.commit()
    return redirect(url_for("item_detail", house_id=house_id, item_id=item.id))


@app.route("/robocze", methods=["GET", "POST"])
@login_required
def robocze():
    if request.method == "POST":
        s = SavedSolution(
            user_id=current_user.id,
            category=request.form.get("category") or None,
            name=request.form["name"],
            company=request.form.get("company"),
            price_min=float(request.form.get("price_min") or 0),
            price_max=float(request.form.get("price_max") or 0),
            link=request.form.get("link"),
            note=request.form.get("note"),
        )
        db.session.add(s)
        db.session.commit()
        return redirect(url_for("robocze"))
    items = SavedSolution.query.filter_by(user_id=current_user.id).all()
    return render_template("robocze.html", items=items, segments=DEFAULT_SEGMENTS)


@app.route("/house/<int:house_id>/inspiracje")
@login_required
def inspiracje(house_id):
    house = get_owned_house(house_id)
    return render_template("inspiracje.html", house=house)


@app.route("/house/<int:house_id>/inspiracje/category/new", methods=["POST"])
@login_required
def inspiracje_category_new(house_id):
    name = request.form.get("name")
    if name:
        db.session.add(InspirationCategory(house_id=house_id, name=name))
        db.session.commit()
    return redirect(url_for("inspiracje", house_id=house_id))


@app.route("/house/<int:house_id>/inspiracje/category/<int:category_id>/add", methods=["POST"])
@login_required
def inspiracje_tile_add(house_id, category_id):
    tile = InspirationTile(
        category_id=category_id,
        name=request.form["name"],
        description=request.form.get("description"),
        link=request.form.get("link"),
        photo_filename=save_photo(request.files.get("photo")),
    )
    db.session.add(tile)
    db.session.commit()
    next_url = request.form.get("next")
    if next_url:
        return redirect(next_url)
    return redirect(url_for("inspiracje", house_id=house_id))


@app.route("/house/<int:house_id>/inspiracje/tile/<int:tile_id>/delete", methods=["GET"])
@login_required
def inspiracje_tile_delete_confirm(house_id, tile_id):
    house = get_owned_house(house_id)
    t = InspirationTile.query.get_or_404(tile_id)
    return render_template("confirm_delete.html", house=house, target_name=t.name,
                            action_url=url_for("inspiracje_tile_delete", house_id=house_id, tile_id=tile_id),
                            cancel_url=request.referrer or url_for("inspiracje", house_id=house_id))


@app.route("/house/<int:house_id>/inspiracje/tile/<int:tile_id>/delete", methods=["POST"])
@login_required
def inspiracje_tile_delete(house_id, tile_id):
    t = InspirationTile.query.get_or_404(tile_id)
    db.session.delete(t)
    db.session.commit()
    return redirect(request.form.get("next") or url_for("inspiracje", house_id=house_id))


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


@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    error = None
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
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
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    error = None
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        u = User.query.filter_by(email=email).first()
        if u and u.check_password(password):
            login_user(u)
            next_url = request.args.get("next")
            return redirect(next_url or url_for("index"))
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
