# SSI Ticketing (DEMO)

Sistem tiket demo untuk Stadium Sultan Ibrahim, Iskandar Puteri, Johor.
Python stdlib + SQLite, tiada dependency luar.

## Jalankan
    PORT=8000 python3 server.py
    python3 test_api.py        # 30 ujian API

## Ciri
- **Sorting cadangan kerusi**: paling dekat padang (default), harga termurah, harga tertinggi, ikut seksyen & baris.
- **Peta seksyen**: 16 seksyen, klik untuk lihat kerusi.
- **Auto pilih**: blok kerusi bersebelahan dalam satu baris (1-8 kerusi), dipilih ikut skor terbaik.
- **Parkir**: 4 lot demo. Lot VIP hanya untuk tiket VIP.
- **Tahan kerusi 10 minit**: jika tidak dibayar, kerusi dan parkir dilepaskan automatik.
- **TNG eWallet (DEMO)**: aliran bayaran disimulasikan. Butang "Bayar" mengesahkan tempahan tanpa wang sebenar.

## Data yang ANGGARAN (bukan rasmi)
- Kapasiti 40,000 dan lokasi di Iskandar Puteri disahkan dari Wikipedia.
- Bilangan seksyen, baris, kerusi per baris, harga dan lot parkir ialah **anggaran demo**. Edit `SECTION_DEFS` dan `PARKING` dalam `server.py`.
- Tarikh perlawanan belum disahkan (`EVENTS`).

## Yang belum ada (untuk produksi)
- Integrasi TNG sebenar: dokumentasi API developer TNG tidak dapat diakses dari sini. Perlu akaun merchant dan API key rasmi.
- Pengesahan callback/webhook bayaran, log audit, pembayaran berulang.
- Peta kerusi sebenar SSI (pelan rasmi perlu dari pihak pengurusan stadium).
- Penghantaran e-tiket dan QR.

## API ringkas
| Kaedah | Laluan | Fungsi |
|---|---|---|
| GET | /api/events | senarai acara |
| GET | /api/events/{id} | ringkasan & seksyen |
| GET | /api/events/{id}/sections/{sec} | peta baris kerusi (f=kosong, s=ditempah) |
| GET | /api/events/{id}/seats/best?sort=&section=&limit= | cadangan kerusi (sort: score, price_asc, price_desc, row) |
| POST | /api/events/{id}/autoassign | `{quantity, section}` |
| GET | /api/events/{id}/parking | lot parkir |
| POST | /api/events/{id}/bookings | tempahan (status pending, 10 minit) |
| POST | /api/payments/tng/{ref}/pay | bayaran demo |
| GET | /api/bookings/{code} | semakan tempahan |
