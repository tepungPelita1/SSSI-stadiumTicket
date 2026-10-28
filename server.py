#!/usr/bin/env python3
"""Sistem tiket demo untuk Stadium Sultan Ibrahim (SSI), Iskandar Puteri, Johor.

Stdlib sahaja (http.server + sqlite3). Tiada dependency luar.
Jalankan:  PORT=8000 python3 server.py

Susun atur tempat duduk dan parkir ialah ANGGARAN (lihat README.md).
Tiada pembayaran sebenar diproses; ini mod demo.
"""
import json
import math
import os
import re
import secrets
import sqlite3
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("SSI_DB", os.path.join(BASE_DIR, "data", "ssi.db"))
STATIC_DIR = os.path.join(BASE_DIR, "static")

PITCH_X, PITCH_Y = 500.0, 350.0      # pusat padang pada peta ringkas (viewBox 1000x700)
MAX_SEATS = 8                        # had kerusi setiap tempahan
MAX_BODY = 64 * 1024
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # tanpa 0/O/1/I
SORTS = ("score", "price_asc", "price_desc", "row")

# Anggaran susun atur. (id, nama, tier, harga RM, baris, kerusi/baris, x, y, lebar, tinggi)
SECTION_DEFS = [
    ("N1",  "Utara Bawah",           "Premium",  200, 30, 115, 330, 200, 340, 52),
    ("S1",  "Selatan Bawah",         "Premium",  200, 30, 115, 330, 448, 340, 52),
    ("W1",  "Barat Bawah (VIP)",     "VIP",      350, 22, 100, 270, 258, 52, 184),
    ("E1",  "Timur Bawah (VIP)",     "VIP",      350, 22, 100, 678, 258, 52, 184),
    ("NW1", "Penjuru Barat Laut",    "Standard", 120, 22, 85,  270, 200, 52, 50),
    ("NE1", "Penjuru Timur Laut",    "Standard", 120, 22, 85,  678, 200, 52, 50),
    ("SW1", "Penjuru Barat Daya",    "Standard", 120, 22, 85,  270, 448, 52, 50),
    ("SE1", "Penjuru Tenggara",      "Standard", 120, 22, 85,  678, 448, 52, 50),
    ("N2",  "Utara Atas",            "Standard",  80, 34, 120, 330, 120, 340, 76),
    ("S2",  "Selatan Atas",          "Standard",  80, 34, 120, 330, 504, 340, 76),
    ("W2",  "Barat Atas",            "Ekonomi",   60, 30, 115, 170, 258, 96, 184),
    ("E2",  "Timur Atas",            "Ekonomi",   60, 30, 115, 734, 258, 96, 184),
    ("NW2", "Penjuru Atas Barat Laut", "Ekonomi", 40, 20, 80,  170, 120, 96, 76),
    ("NE2", "Penjuru Atas Timur Laut", "Ekonomi", 40, 20, 80,  734, 120, 96, 76),
    ("SW2", "Penjuru Atas Barat Daya", "Ekonomi", 40, 20, 80,  170, 504, 96, 76),
    ("SE2", "Penjuru Atas Tenggara",   "Ekonomi", 40, 20, 80,  734, 504, 96, 76),
]
SECTIONS = [
    {"id": s[0], "name": s[1], "tier": s[2], "price": s[3], "rows": s[4], "per": s[5],
     "box": {"x": s[6], "y": s[7], "w": s[8], "h": s[9]}}
    for s in SECTION_DEFS
]
SEC_BY_ID = {s["id"]: s for s in SECTIONS}

# Tempat letak kereta (anggaran / demo). (id, nama, kapasiti, harga sen, nota, tier diperlukan)
PARKING = [
    ("P1", "Parkir Tepi Barat (demo)", 800, 1000, "Anggaran lokasi, sahkan dengan pengurusan", None),
    ("P2", "Parkir Tepi Timur (demo)", 600, 1000, "Anggaran lokasi, sahkan dengan pengurusan", None),
    ("P3", "Parkir VIP (demo)", 120, 3000, "Untuk pemegang tiket VIP sahaja", "VIP"),
    ("P4", "Park & Ride (cadangan)", 300, 500, "Belum disahkan", None),
]

EVENTS = [
    (1, "Perlawanan Demo 1 · Liga Super", "Tarikh belum disahkan"),
    (2, "Perlawanan Demo 2 · Piala", "Tarikh belum disahkan"),
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS events(
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, starts_at TEXT);
CREATE TABLE IF NOT EXISTS seats(
    event_id INTEGER NOT NULL, section TEXT NOT NULL, row_no INTEGER NOT NULL,
    seat_no INTEGER NOT NULL, score REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'free', booking_id INTEGER,
    PRIMARY KEY(event_id, section, row_no, seat_no));
CREATE INDEX IF NOT EXISTS seats_status ON seats(event_id, status, section);
CREATE TABLE IF NOT EXISTS bookings(
    id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE NOT NULL,
    event_id INTEGER NOT NULL, name TEXT NOT NULL, email TEXT NOT NULL,
    phone TEXT NOT NULL, total_cents INTEGER NOT NULL, created_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending | paid | expired
    payment_ref TEXT UNIQUE, paid_at TEXT, expires_at TEXT);
CREATE TABLE IF NOT EXISTS parking_lots(
    event_id INTEGER NOT NULL, id TEXT NOT NULL, name TEXT NOT NULL,
    capacity INTEGER NOT NULL, sold INTEGER NOT NULL DEFAULT 0,
    price_cents INTEGER NOT NULL, note TEXT, requires_tier TEXT,
    PRIMARY KEY(event_id, id));
CREATE TABLE IF NOT EXISTS parking_passes(
    id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE NOT NULL,
    booking_id INTEGER NOT NULL, event_id INTEGER NOT NULL, lot_id TEXT NOT NULL,
    plate TEXT NOT NULL, price_cents INTEGER NOT NULL);
"""


class ApiError(Exception):
    def __init__(self, status, msg):
        super().__init__(msg)
        self.status = status
        self.msg = msg


def connect():
    conn = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


def seat_rows(event_id):
    """Jana kerusi. score = jarak anggaran ke tengah padang + kedudukan baris (lebih kecil = lebih dekat)."""
    out = []
    for s in SECTIONS:
        b = s["box"]
        cx, cy = b["x"] + b["w"] / 2, b["y"] + b["h"] / 2
        base = math.hypot(cx - PITCH_X, cy - PITCH_Y)
        mid = (s["per"] + 1) / 2
        for r in range(1, s["rows"] + 1):
            for k in range(1, s["per"] + 1):
                score = round(base + r * 1.0 + abs(k - mid) * 0.05, 2)
                out.append((event_id, s["id"], r, k, score))
    return out


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = connect()
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    if conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0:
        conn.execute("BEGIN IMMEDIATE")
        try:
            for ev_id, name, starts in EVENTS:
                conn.execute("INSERT INTO events(id,name,starts_at) VALUES(?,?,?)", (ev_id, name, starts))
                conn.executemany(
                    "INSERT INTO seats(event_id,section,row_no,seat_no,score) VALUES(?,?,?,?,?)",
                    seat_rows(ev_id))
                conn.executemany(
                    "INSERT INTO parking_lots(event_id,id,name,capacity,sold,price_cents,note,requires_tier)"
                    " VALUES(?,?,?,?,0,?,?,?)",
                    [(ev_id, p[0], p[1], p[2], p[3], p[4], p[5]) for p in PARKING])
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    conn.close()


# ---------- helpers ----------

def _clean(v, n):
    return str(v if v is not None else "").strip()[:n]


def _qty(v):
    try:
        q = int(v)
    except (TypeError, ValueError):
        raise ApiError(400, "Bilangan kerusi tidak sah")
    if not 1 <= q <= MAX_SEATS:
        raise ApiError(400, f"Pilih 1 hingga {MAX_SEATS} kerusi")
    return q


def _section_arg(v):
    if v in (None, "", "ALL"):
        return None
    if v not in SEC_BY_ID:
        raise ApiError(400, "Seksyen tidak sah")
    return v


def _new_code(conn, table, prefix):
    while True:
        c = prefix + "-" + "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
        if not conn.execute(f"SELECT 1 FROM {table} WHERE code=?", (c,)).fetchone():
            return c


def _mask_email(e):
    u, _, d = e.partition("@")
    return (u[:1] + "***@" + d) if d else "***"


def _find_block(conn, event_id, qty, section):
    """Cari blok kerusi bersebelahan (dalam satu baris) dengan purata skor paling kecil."""
    sql = "SELECT section,row_no,seat_no,score FROM seats WHERE event_id=? AND status='free'"
    params = [event_id]
    if section:
        sql += " AND section=?"
        params.append(section)
    sql += " ORDER BY section,row_no,seat_no"
    best = None

    def consider(run):
        nonlocal best
        for i in range(len(run) - qty + 1):
            win = run[i:i + qty]
            avg = sum(w["score"] for w in win) / qty
            key = (avg, win[0]["section"], win[0]["row_no"], win[0]["seat_no"])
            if best is None or key < best[0]:
                best = (key, win)

    run = []
    for r in conn.execute(sql, params):
        if run and r["section"] == run[-1]["section"] and r["row_no"] == run[-1]["row_no"] \
                and r["seat_no"] == run[-1]["seat_no"] + 1:
            run.append(r)
        else:
            if len(run) >= qty:
                consider(run)
            run = [r]
    if len(run) >= qty:
        consider(run)
    if best is None:
        return None
    return [(w["section"], w["row_no"], w["seat_no"]) for w in best[1]]


def release_expired(conn):
    """Lepas kerusi & parkir untuk tempahan belum dibayar yang dah tamat tempoh (10 minit)."""
    ids = [r[0] for r in conn.execute(
        "SELECT id FROM bookings WHERE status='pending' AND expires_at < datetime('now')")]
    if not ids:
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        for bid in ids:
            # semak semula dalam transaksi supaya tak lepas dua kali
            cur = conn.execute("UPDATE bookings SET status='expired' WHERE id=? AND status='pending'", (bid,))
            if cur.rowcount != 1:
                continue
            conn.execute("UPDATE seats SET status='free', booking_id=NULL WHERE booking_id=?", (bid,))
            for p in conn.execute("SELECT event_id, lot_id FROM parking_passes WHERE booking_id=?", (bid,)).fetchall():
                conn.execute("UPDATE parking_lots SET sold=sold-1 WHERE event_id=? AND id=?",
                             (p["event_id"], p["lot_id"]))
            conn.execute("DELETE FROM parking_passes WHERE booking_id=?", (bid,))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


# ---------- queries ----------

def list_events(conn):
    return [dict(r) for r in conn.execute("SELECT id,name,starts_at FROM events ORDER BY id")]


def event_detail(conn, event_id):
    ev = conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    if not ev:
        raise ApiError(404, "Acara tidak dijumpai")
    counts = {r["section"]: (r["free"] or 0, r["total"]) for r in conn.execute(
        "SELECT section, SUM(status='free') AS free, COUNT(*) AS total FROM seats"
        " WHERE event_id=? GROUP BY section", (event_id,))}
    sections = []
    for s in SECTIONS:
        free, total = counts.get(s["id"], (0, 0))
        sections.append({"id": s["id"], "name": s["name"], "tier": s["tier"],
                         "price_rm": s["price"], "rows": s["rows"], "seats_per_row": s["per"],
                         "box": s["box"], "free": free, "total": total})
    return {"id": ev["id"], "name": ev["name"], "starts_at": ev["starts_at"],
            "total_seats": sum(x["total"] for x in sections),
            "free_seats": sum(x["free"] for x in sections),
            "sections": sections}


def section_grid(conn, event_id, sec):
    if sec not in SEC_BY_ID:
        raise ApiError(404, "Seksyen tidak dijumpai")
    cfg = SEC_BY_ID[sec]
    grid = {}
    for r in conn.execute("SELECT row_no, status FROM seats WHERE event_id=? AND section=?"
                          " ORDER BY row_no, seat_no", (event_id, sec)):
        grid.setdefault(r["row_no"], []).append("f" if r["status"] == "free" else "s")
    return {"section": sec, "name": cfg["name"], "tier": cfg["tier"], "price_rm": cfg["price"],
            "rows": [{"row": r, "seats": "".join(v)} for r, v in sorted(grid.items())]}


def list_best(conn, event_id, section, sort, limit):
    if sort not in SORTS:
        raise ApiError(400, "Susunan tidak sah")
    sql = "SELECT section,row_no,seat_no,score FROM seats WHERE event_id=? AND status='free'"
    params = [event_id]
    if section:
        sql += " AND section=?"
        params.append(section)
    rows = conn.execute(sql, params).fetchall()
    price = lambda sec: SEC_BY_ID[sec]["price"]  # noqa: E731
    keyf = {
        "score": lambda r: (r["score"], r["row_no"], r["seat_no"]),
        "price_asc": lambda r: (price(r["section"]), r["score"]),
        "price_desc": lambda r: (-price(r["section"]), r["score"]),
        "row": lambda r: (r["section"], r["row_no"], r["seat_no"]),
    }[sort]
    rows.sort(key=keyf)
    return [{"section": r["section"], "row": r["row_no"], "seat": r["seat_no"],
             "tier": SEC_BY_ID[r["section"]]["tier"], "price_rm": price(r["section"])}
            for r in rows[:limit]]


def parking_list(conn, event_id):
    out = []
    for r in conn.execute("SELECT * FROM parking_lots WHERE event_id=? ORDER BY id", (event_id,)):
        out.append({"id": r["id"], "name": r["name"], "capacity": r["capacity"],
                    "free": r["capacity"] - r["sold"], "price_rm": r["price_cents"] / 100,
                    "note": r["note"], "requires_tier": r["requires_tier"]})
    return out


def get_booking(conn, code):
    b = conn.execute("SELECT b.*, e.name AS event_name FROM bookings b"
                     " JOIN events e ON e.id=b.event_id WHERE b.code=?", (code,)).fetchone()
    if not b:
        raise ApiError(404, "Tempahan tidak dijumpai")
    seats = [{"section": s["section"], "name": SEC_BY_ID[s["section"]]["name"],
              "tier": SEC_BY_ID[s["section"]]["tier"], "row": s["row_no"], "seat": s["seat_no"],
              "price_rm": SEC_BY_ID[s["section"]]["price"]}
             for s in conn.execute("SELECT section,row_no,seat_no FROM seats WHERE booking_id=?"
                                   " ORDER BY section,row_no,seat_no", (b["id"],))]
    p = conn.execute("SELECT p.code, p.plate, p.price_cents, l.name AS lot FROM parking_passes p"
                     " JOIN parking_lots l ON l.event_id=p.event_id AND l.id=p.lot_id"
                     " WHERE p.booking_id=?", (b["id"],)).fetchone()
    return {"code": b["code"], "event": b["event_name"], "name": b["name"],
            "email": _mask_email(b["email"]), "phone": b["phone"],
            "total_rm": b["total_cents"] / 100, "created_at": b["created_at"],
            "status": b["status"], "expires_at": b["expires_at"],
            "seats": seats,
            "parking": ({"code": p["code"], "lot": p["lot"], "plate": p["plate"],
                         "price_rm": p["price_cents"] / 100} if p else None)}


# ---------- commands ----------

def create_booking(conn, event_id, data):
    name = _clean(data.get("name"), 100)
    email = _clean(data.get("email"), 120).lower()
    phone = _clean(data.get("phone"), 20)
    if len(name) < 2:
        raise ApiError(400, "Nama diperlukan")
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise ApiError(400, "E-mel tidak sah")
    if not re.fullmatch(r"\+?[0-9 \-]{8,20}", phone):
        raise ApiError(400, "No. telefon tidak sah")
    if not conn.execute("SELECT 1 FROM events WHERE id=?", (event_id,)).fetchone():
        raise ApiError(404, "Acara tidak dijumpai")

    auto = data.get("autoassign")
    parking = data.get("parking")
    seats_req = data.get("seats")

    conn.execute("BEGIN IMMEDIATE")
    try:
        if auto:
            qty = _qty(auto.get("quantity"))
            seats = _find_block(conn, event_id, qty, _section_arg(auto.get("section")))
            if not seats:
                raise ApiError(409, "Tiada kerusi bersebelahan mencukupi")
        else:
            if not isinstance(seats_req, list) or not 1 <= len(seats_req) <= MAX_SEATS:
                raise ApiError(400, f"Pilih 1 hingga {MAX_SEATS} kerusi")
            seats = []
            for s in seats_req:
                try:
                    seats.append((str(s["section"]), int(s["row"]), int(s["seat"])))
                except (KeyError, TypeError, ValueError):
                    raise ApiError(400, "Format kerusi tidak sah")
        if len(set(seats)) != len(seats):
            raise ApiError(400, "Kerusi pendua")

        total = 0
        tiers = set()
        for sec, row, seat in seats:
            if sec not in SEC_BY_ID:
                raise ApiError(400, "Seksyen tidak sah")
            r = conn.execute("SELECT status FROM seats WHERE event_id=? AND section=? AND row_no=?"
                             " AND seat_no=?", (event_id, sec, row, seat)).fetchone()
            if r is None:
                raise ApiError(404, f"Kerusi {sec} baris {row} no {seat} tidak wujud")
            if r["status"] != "free":
                raise ApiError(409, f"Kerusi {sec} baris {row} no {seat} sudah ditempah")
            total += SEC_BY_ID[sec]["price"] * 100
            tiers.add(SEC_BY_ID[sec]["tier"])

        lot_id = None
        if parking:
            lot_id = _clean(parking.get("lot_id"), 10)
            plate = _clean(parking.get("plate"), 12).upper()
            lot = conn.execute("SELECT * FROM parking_lots WHERE event_id=? AND id=?",
                               (event_id, lot_id)).fetchone()
            if lot is None:
                raise ApiError(400, "Lot parkir tidak sah")
            if lot["requires_tier"] and lot["requires_tier"] not in tiers:
                raise ApiError(400, f"{lot['name']} hanya untuk pemegang tiket {lot['requires_tier']}")
            if not re.fullmatch(r"[A-Z0-9 ]{2,12}", plate):
                raise ApiError(400, "No. plat tidak sah")
            if lot["sold"] >= lot["capacity"]:
                raise ApiError(409, "Parkir penuh")
            total += lot["price_cents"]

        code = _new_code(conn, "bookings", "SSI")
        pref = "TNG" + secrets.token_hex(8).upper()
        cur = conn.execute("INSERT INTO bookings(code,event_id,name,email,phone,total_cents,created_at,"
                           "status,expires_at,payment_ref) VALUES(?,?,?,?,?,?,datetime('now'),'pending',"
                           "datetime('now','+10 minutes'),?)",
                           (code, event_id, name, email, phone, total, pref))
        bid = cur.lastrowid
        for sec, row, seat in seats:
            cur = conn.execute("UPDATE seats SET status='held', booking_id=? WHERE event_id=? AND"
                               " section=? AND row_no=? AND seat_no=? AND status='free'",
                               (bid, event_id, sec, row, seat))
            if cur.rowcount != 1:
                raise ApiError(409, "Kerusi baru sahaja ditempah, cuba semula")
        if lot_id is not None:
            cur = conn.execute("UPDATE parking_lots SET sold=sold+1 WHERE event_id=? AND id=?"
                               " AND sold<capacity", (event_id, lot_id))
            if cur.rowcount != 1:
                raise ApiError(409, "Parkir penuh")
            pcode = _new_code(conn, "parking_passes", "PKG")
            conn.execute("INSERT INTO parking_passes(code,booking_id,event_id,lot_id,plate,price_cents)"
                         " VALUES(?,?,?,?,?,?)", (pcode, bid, event_id, lot_id, plate, lot["price_cents"]))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    out = get_booking(conn, code)
    out["payment"] = {"provider": "TNG eWallet (DEMO)", "ref": pref,
                      "pay_url": f"/?pay={pref}", "expires_in_minutes": 10}
    return out


def tng_pay(conn, ref):
    """MOD DEMO: simulasi callback 'berjaya' dari TNG. Tiada wang sebenar."""
    b = conn.execute("SELECT * FROM bookings WHERE payment_ref=?", (ref,)).fetchone()
    if not b:
        raise ApiError(404, "Rujukan bayaran tidak dijumpai")
    if b["status"] == "paid":
        return get_booking(conn, b["code"])
    conn.execute("BEGIN IMMEDIATE")
    try:
        cur = conn.execute("UPDATE bookings SET status='paid', paid_at=datetime('now')"
                           " WHERE id=? AND status='pending'", (b["id"],))
        if cur.rowcount != 1:
            raise ApiError(409, "Tempahan tamat tempoh, sila buat tempahan baru")
        conn.execute("UPDATE seats SET status='sold' WHERE booking_id=?", (b["id"],))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return get_booking(conn, b["code"])


def autoassign_preview(conn, event_id, quantity, section):
    qty = _qty(quantity)
    found = _find_block(conn, event_id, qty, _section_arg(section))
    if not found:
        raise ApiError(409, "Tiada kerusi bersebelahan mencukupi")
    return [{"section": s, "row": r, "seat": k, "tier": SEC_BY_ID[s]["tier"],
             "price_rm": SEC_BY_ID[s]["price"]} for s, r, k in found]


# ---------- HTTP ----------

def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        raise ApiError(404, "Tidak dijumpai")


def route(method, path, q, read_body):
    parts = [p for p in path.split("/") if p]
    if parts[:1] != ["api"]:
        raise ApiError(404, "Tidak dijumpai")
    conn = connect()
    try:
        release_expired(conn)
        if method == "GET" and parts == ["api", "health"]:
            return 200, {"ok": True}
        if method == "GET" and parts == ["api", "events"]:
            return 200, list_events(conn)
        if len(parts) >= 3 and parts[1] == "events":
            eid = _int(parts[2])
            if method == "GET" and len(parts) == 3:
                return 200, event_detail(conn, eid)
            if method == "GET" and len(parts) == 5 and parts[3] == "sections":
                return 200, section_grid(conn, eid, parts[4])
            if method == "GET" and parts[3:] == ["seats", "best"]:
                limit = min(max(_int(q.get("limit", ["30"])[0]), 1), 200)
                sec = _section_arg(q.get("section", [None])[0])
                sort = q.get("sort", ["score"])[0]
                return 200, list_best(conn, eid, sec, sort, limit)
            if method == "POST" and parts[3:] == ["autoassign"]:
                body = read_body()
                return 200, {"seats": autoassign_preview(conn, eid, body.get("quantity"), body.get("section"))}
            if method == "GET" and parts[3:] == ["parking"]:
                return 200, parking_list(conn, eid)
            if method == "POST" and parts[3:] == ["bookings"]:
                return 201, create_booking(conn, eid, read_body())
        if method == "POST" and len(parts) == 5 and parts[1:3] == ["payments", "tng"] and parts[4] == "pay":
            return 200, tng_pay(conn, _clean(parts[3], 40))
        if method == "GET" and len(parts) == 3 and parts[1] == "bookings":
            return 200, get_booking(conn, _clean(parts[2], 20).upper())
        raise ApiError(404, "Laluan tidak dijumpai")
    finally:
        conn.close()


class Handler(BaseHTTPRequestHandler):
    server_version = "SSITicket/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, status, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > MAX_BODY:
            raise ApiError(400, "Badan permintaan tidak sah")
        try:
            body = json.loads(self.rfile.read(n))
        except json.JSONDecodeError:
            raise ApiError(400, "JSON tidak sah")
        if not isinstance(body, dict):
            raise ApiError(400, "JSON tidak sah")
        return body

    def _dispatch(self, method):
        url = urlparse(self.path)
        path = unquote(url.path)
        try:
            if method == "GET" and path in ("/", "/index.html"):
                with open(os.path.join(STATIC_DIR, "index.html"), "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            status, obj = route(method, path, parse_qs(url.query), self._read_body)
            self._send(status, obj)
        except ApiError as e:
            self._send(e.status, {"error": e.msg})
        except Exception:  # jangan bocorkan stack trace
            sys.stderr.write("internal error\n")
            import traceback
            traceback.print_exc()
            self._send(500, {"error": "Ralat dalaman"})

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")


def make_server(host="127.0.0.1", port=0):
    return ThreadingHTTPServer((host, port), Handler)


def main():
    init_db()
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    srv = make_server(host, port)
    print(f"SSI ticketing berjalan di http://{host}:{port}  (DB: {DB_PATH})", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
