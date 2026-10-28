import json, os, sys, tempfile, threading, urllib.request, urllib.error
os.environ["SSI_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import server
server.init_db()
srv = server.make_server("127.0.0.1", 0)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
B = f"http://127.0.0.1:{port}"

def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(B + path, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())

def ok(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond: ok.fail += 1
ok.fail = 0

st, ev = call("GET", "/api/events"); ok(st == 200 and len(ev) == 2, "events list")
eid = ev[0]["id"]
st, d = call("GET", f"/api/events/{eid}")
total = d["total_seats"]; ok(total == sum(s["rows"] * s["per"] for s in server.SECTIONS), f"total seats {total}")
print("   sections:", len(d["sections"]), "total seats:", total)
st, best = call("GET", f"/api/events/{eid}/seats/best?sort=score&limit=3"); ok(st == 200 and len(best) == 3, "best by score")
ok(best[0]["section"] in ("N1","S1","W1","E1"), f"closest seat is lower bowl: {best[0]}")
st, cheap = call("GET", f"/api/events/{eid}/seats/best?sort=price_asc&limit=1"); ok(cheap[0]["price_rm"] == 40, f"cheapest {cheap[0]['price_rm']}")
st, dear = call("GET", f"/api/events/{eid}/seats/best?sort=price_desc&limit=1"); ok(dear[0]["price_rm"] == 350, f"priciest {dear[0]['price_rm']}")
st, rowsrt = call("GET", f"/api/events/{eid}/seats/best?sort=row&section=N1&limit=2"); ok(rowsrt[0]["row"] == 1 and rowsrt[0]["seat"] == 1, "row sort N1 starts B1 no1")
st, bad = call("GET", f"/api/events/{eid}/seats/best?sort=zzz"); ok(st == 400, "bad sort rejected")

st, auto = call("POST", f"/api/events/{eid}/autoassign", {"quantity": 3, "section": "N1"})
ok(st == 200 and len(auto["seats"]) == 3 and len({(s['row']) for s in auto["seats"]}) == 1, f"autoassign 3 adjacent {auto['seats'] if st==200 else auto}")

seats = [{"section": s["section"], "row": s["row"], "seat": s["seat"]} for s in auto["seats"]]
st, park = call("GET", f"/api/events/{eid}/parking"); ok(len(park) == 4, "4 parking lots")
body = {"name": "Ali Bin Abu", "email": "ali@example.com", "phone": "+60123456789", "seats": seats,
        "parking": {"lot_id": "P1", "plate": "jhr 1234"}}
st, bk = call("POST", f"/api/events/{eid}/bookings", body)
ok(st == 201 and bk["status"] == "pending" and bk["payment"]["ref"].startswith("TNG"), f"booking pending {st}")
expect = sum(server.SEC_BY_ID[s["section"]]["price"] for s in seats) + 10
ok(abs(bk["total_rm"] - expect) < 1e-6, f"total {bk['total_rm']} == {expect}")
st, p2 = call("GET", f"/api/events/{eid}/parking"); p1 = [p for p in p2 if p["id"] == "P1"][0]
ok(p1["free"] == 799, "parking P1 held 1")
st, g = call("GET", f"/api/events/{eid}/sections/N1"); ok("s" in "".join(r["seats"] for r in g["rows"]), "held seats show as taken")

st, dup = call("POST", f"/api/events/{eid}/bookings", {**body, "email": "x@y.com", "parking": None})
ok(st == 409, f"double-booking blocked {st} {dup}")

st, cur = call("POST", f"/api/events/{eid}/bookings", {"name": "Siti", "email": "siti@example.com", "phone": "0123456789", "seats": [{"section": "W1", "row": 1, "seat": 1}]})
ok(st == 201, "second booking")
st, r = call("POST", f"/api/payments/tng/{cur['payment']['ref']}/pay", {})
ok(st == 200 and r["status"] == "paid", "tng demo pay marks paid")
st, r2 = call("POST", f"/api/payments/tng/{cur['payment']['ref']}/pay", {}); ok(st == 200 and r2["status"] == "paid", "pay is idempotent")
st, look = call("GET", f"/api/bookings/{cur['code']}"); ok(look["status"] == "paid" and "payment" not in look and "ref" not in json.dumps(look).lower() or "payment_ref" not in look, "lookup hides payment ref")
st, cnt = call("GET", f"/api/events/{eid}"); ok(cnt["free_seats"] == total - 3 - 1, f"free seats after 2 bookings {cnt['free_seats']}")

st, vip = call("POST", f"/api/events/{eid}/bookings", {"name": "Budi", "email": "b@example.com", "phone": "0123456789", "seats": [{"section": "N1", "row": 2, "seat": 2}], "parking": {"lot_id": "P3", "plate": "ABC1"}})
ok(st == 400 and "VIP" in vip["error"], f"VIP parking blocked for standard ticket: {vip.get('error')}")

# expiry: force the pending booking to expire, its seats must come back and parking freed
server.connect().execute("UPDATE bookings SET expires_at=datetime('now','-1 minutes') WHERE id=?", (bk["code"] and 1,))
st, after = call("GET", f"/api/events/{eid}/sections/N1")
freed = all(c in "fs" for r in after["rows"] for c in r["seats"])
st, p3 = call("GET", f"/api/events/{eid}/parking"); p1b = [p for p in p3 if p["id"] == "P1"][0]
ok(p1b["free"] == 800, f"expired hold frees parking {p1b['free']}")
st, lk = call("GET", f"/api/bookings/{bk['code']}"); ok(lk["status"] == "expired", "booking marked expired")
st, res = call("POST", f"/api/payments/tng/{bk['payment']['ref']}/pay", {}); ok(st == 409, "cannot pay expired booking")
st, nf = call("GET", "/api/nope"); ok(st == 404, "404 for unknown route")

# concurrency: 20 threads try to take the same seat
results = []
def grab():
    s, _ = call("POST", f"/api/events/{eid}/bookings", {"name": "Race", "email": "r@example.com", "phone": "0123456789", "seats": [{"section": "E2", "row": 5, "seat": 5}]})
    results.append(s)
ths = [threading.Thread(target=grab) for _ in range(20)]
[t.start() for t in ths]; [t.join() for t in ths]
ok(results.count(201) == 1 and results.count(409) == 19, f"race: one winner {results.count(201)}")

srv.shutdown()
print("FAILURES:", ok.fail)
sys.exit(1 if ok.fail else 0)
