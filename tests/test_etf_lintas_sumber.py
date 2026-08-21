"""Pemeriksaan silang arus ETF: cocok, berbeda, dan tanpa pembanding.

Arus ETF diambil dari satu API berbayar dengan cadangan scrape HTML. Kalau
salah satunya rusak — kolom bergeser, satuan berubah dari juta ke ribu —
tidak ada yang menyadarinya, karena angkanya tetap masuk akal dilihat
sekilas. Uji ini menjaga agar perbedaan besar SELALU jadi keterangan yang
terlihat, dan agar kecocokan TIDAK ikut diumumkan tiap hari.
"""

from __future__ import annotations

import copy

from src.collectors import market


def _pasang(monkeypatch, soso_usd, farside_usd, tanggal_soso="2026-08-20",
            tanggal_farside="2026-08-20"):
    monkeypatch.setattr(
        market.sosovalue, "fetch_etf_flow",
        lambda key: {"etf_flow_usd": soso_usd, "etf_flow_date": tanggal_soso},
    )
    if farside_usd is None:
        def _gagal(timeout=20):
            raise market.HttpError("HTTP 403 dari Farside", status_code=403)
        monkeypatch.setattr(market, "_etf_flow_farside", _gagal)
    else:
        monkeypatch.setattr(
            market, "_etf_flow_farside",
            lambda timeout=20: {"etf_flow_usd": farside_usd, "etf_flow_date": tanggal_farside},
        )


def test_dua_sumber_sepakat(monkeypatch):
    _pasang(monkeypatch, 240_000_000, 238_500_000)
    hasil = market._etf_flow("kunci")
    assert hasil["etf_flow_sumber"] == "SoSoValue"
    assert hasil["etf_flow_verifikasi"]["status"] == "cocok"
    # Angka utamanya tidak boleh tergeser oleh pembanding.
    assert hasil["etf_flow_usd"] == 240_000_000


def test_selisih_besar_ditandai(monkeypatch):
    _pasang(monkeypatch, 240_000_000, 24_000_000)
    v = market._etf_flow("kunci")["etf_flow_verifikasi"]
    assert v["status"] == "berbeda"
    assert v["tanggal_sama"] is True
    assert v["selisih_usd"] == 216_000_000


def test_tanggal_berbeda_ikut_dilaporkan(monkeypatch):
    """Selisih besar sering cuma soal satu sumber sudah memuat hari baru.

    Bedanya tetap ditandai — tapi keterangan tanggalnya yang membuat pembaca
    bisa membedakan "sumbernya rusak" dari "sumbernya belum sinkron".
    """
    _pasang(monkeypatch, 240_000_000, -80_000_000, tanggal_farside="2026-08-19")
    v = market._etf_flow("kunci")["etf_flow_verifikasi"]
    assert v["status"] == "berbeda"
    assert v["tanggal_sama"] is False
    assert v["pembanding_tanggal"] == "2026-08-19"


def test_arus_mendekati_nol_tidak_dianggap_berbeda(monkeypatch):
    """Persentase selisih meledak saat kedua angkanya kecil.

    $3 juta vs -$1 juta berbeda 400% tanpa satu pun sumber bermasalah, dan
    peringatan seperti itu cuma melatih pembaca mengabaikan barisnya.
    """
    _pasang(monkeypatch, 3_000_000, -1_000_000)
    v = market._etf_flow("kunci")["etf_flow_verifikasi"]
    assert v["status"] == "terlalu_kecil_untuk_dibandingkan"


def test_pembanding_gagal_bukan_kegagalan(monkeypatch):
    """Farside menolak IP pusat data hampir selalu; itu bukan masalah."""
    _pasang(monkeypatch, 240_000_000, None)
    hasil = market._etf_flow("kunci")
    assert hasil["etf_flow_usd"] == 240_000_000
    assert hasil["etf_flow_verifikasi"]["status"] == "tanpa_pembanding"


def test_keterangan_hanya_muncul_saat_berbeda(peramban, alamat, tulis_data, brief_asli):
    brief = copy.deepcopy(brief_asli)
    brief["market"]["etf_flow_verifikasi"] = {"status": "cocok", "pembanding_sumber": "Farside"}
    tulis_data(brief)

    page = peramban.new_page()
    page.goto(alamat)
    page.wait_for_selector("#s-pasar", timeout=10_000)
    assert "Sumber pembanding" not in page.inner_text("#s-pasar")
    page.close()


def test_keterangan_tampil_saat_sumber_tidak_sepakat(peramban, alamat, tulis_data, brief_asli):
    brief = copy.deepcopy(brief_asli)
    brief["market"]["etf_flow_usd"] = 240_000_000
    brief["market"]["etf_flow_verifikasi"] = {
        "status": "berbeda",
        "pembanding_sumber": "Farside",
        "pembanding_usd": 24_000_000,
        "pembanding_tanggal": "2026-08-20",
        "selisih_usd": 216_000_000,
        "selisih_pct": 90.0,
        "tanggal_sama": True,
    }
    tulis_data(brief)

    page = peramban.new_page()
    page.goto(alamat)
    page.wait_for_selector("#s-pasar", timeout=10_000)
    teks = page.inner_text("#s-pasar")
    assert "Sumber pembanding Farside" in teks
    assert "salah satunya kemungkinan keliru" in teks
    page.close()
