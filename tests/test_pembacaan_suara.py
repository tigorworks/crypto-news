"""Pembacaan suara brief (Web Speech API).

Dua lapis yang diuji terpisah, karena sifat kegagalannya berbeda:

  1. `untukSuara()` — penyiapan teks. Fungsi murni, dan di sinilah kesalahan
     paling merusak berada: titik ribuan gaya Indonesia yang dibaca sebagai
     titik desimal membuat harga $77.614 terdengar sebagai tujuh puluh tujuh.
     Pendengar tidak punya cara tahu ia salah dengar.
  2. Pemutarnya — antrean, jeda, lompat bagian. Chromium headless tidak
     punya satu pun suara TTS, jadi `speechSynthesis` di-stub sebelum halaman
     memuat: yang diuji adalah APA yang diantrekan dan kapan, bukan bunyinya.
"""

from __future__ import annotations

import pytest


# Stub speechSynthesis: merekam tiap utterance, dan menjalankan onend segera
# supaya antreannya mengalir tanpa menunggu bunyi yang memang tidak ada.
STUB_SUARA = """
window.__ucapan = [];
window.__batal = 0;
class SpeechSynthesisUtterance {
  constructor(teks) { this.text = teks; this.lang = ''; this.rate = 1; }
}
window.SpeechSynthesisUtterance = SpeechSynthesisUtterance;

// defineProperty, BUKAN `window.speechSynthesis = ...`. Di Chromium
// speechSynthesis adalah getter di Window.prototype, jadi assignment biasa
// gagal DIAM: stub-nya tidak terpasang, halaman memakai mesin sungguhan yang
// di headless tidak punya satu pun suara, dan seluruh uji pemutar gagal
// dengan gejala yang menyesatkan ("tombol Dengarkan tidak ada").
Object.defineProperty(window, 'speechSynthesis', {
  configurable: true,
  value: {
    _pending: null,
    getVoices() { return [{ name: 'Damayanti', lang: 'id-ID' }, { name: 'Alex', lang: 'en-US' }]; },
    addEventListener() {},
    speak(u) {
      window.__ucapan.push({ text: u.text, lang: u.lang, rate: u.rate, voice: u.voice?.lang });
      // onend ditunda satu tick: memanggilnya serentak membuat rekursi
      // sedalam antreannya dan menghabiskan stack di brief yang panjang.
      this._pending = setTimeout(() => u.onend && u.onend(), 0);
    },
    cancel() { window.__batal += 1; clearTimeout(this._pending); },
    pause() { this.paused = true; },
    resume() { this.paused = false; },
  },
});
"""

# Varian tanpa satu pun suara Indonesia: tombolnya harus hilang sama sekali.
STUB_TANPA_ID = STUB_SUARA.replace(
    "return [{ name: 'Damayanti', lang: 'id-ID' }, { name: 'Alex', lang: 'en-US' }];",
    "return [{ name: 'Alex', lang: 'en-US' }];",
)


@pytest.fixture
def halaman_suara(peramban, alamat):
    """Halaman brief dengan speechSynthesis yang bisa diperiksa."""
    def _buka(stub=STUB_SUARA):
        hal = peramban.new_page()
        hal.add_init_script(stub)
        hal.goto(alamat, wait_until="networkidle")
        return hal
    return _buka


# ---------------------------------------------------------------------
# 1. Penyiapan teks
# ---------------------------------------------------------------------

#: Kasus penyiapan teks. Dijalankan dalam SATU muat halaman, bukan lewat
#: parametrize: fungsinya murni dan tidak menyimpan state, sementara tiap
#: muat halaman ini memakan belasan detik. Kasus yang gagal tetap disebut
#: satu per satu di pesan assert-nya.
KASUS_TEKS = [
    # Titik ribuan gaya Indonesia — kesalahan yang paling merusak.
    ("$77.614", "77 ribu 614 dolar"),
    ("harga 90.598 ditembus", "harga 90 ribu 598 ditembus"),
    # Satuan besar tetap di antara angka dan "dolar", seperti diucapkan.
    ("$1,92 miliar", "1 koma 9 2 miliar dolar"),
    ("$307 juta", "307 juta dolar"),
    # Rentang butuh kata penghubung, bukan dua angka berdempet.
    ("$75.559–$78.065", "75 ribu 559 sampai 78 ribu 65 dolar"),
    ("1,5–2,5%", "1 koma 5 sampai 2 koma 5 persen"),
    # Desimal dibaca digit per digit.
    ("0,93%", "0 koma 9 3 persen"),
    ("~$77.024", "sekitar 77 ribu 24 dolar"),
    # 0,05 yang terbaca "nol koma lima" salah SEPULUH KALI LIPAT. Bukan
    # kasus karangan: pembulatan dua desimal tanpa padding memulangkan "5".
    ("0,05%", "0 koma 0 5 persen"),
    ("0,5%", "0 koma 5 persen"),
    # Akronim.
    ("EMA20", "E M A 20"),
    ("RSI dan MACD", "R S I dan M A C D"),
    ("BTC menguat", "Bitcoin menguat"),
    ("sesi AS", "sesi Amerika Serikat"),
    ("OI turun", "open interest turun"),
]


def test_teks_disiapkan_untuk_diucapkan(peramban, alamat):
    hal = peramban.new_page()
    hal.goto(alamat, wait_until="networkidle")
    hasil = hal.evaluate("(kasus) => kasus.map(([t]) => untukSuara(t))", KASUS_TEKS)

    salah = [
        f"{masuk!r} -> {dapat!r} (diharapkan {harap!r})"
        for (masuk, harap), dapat in zip(KASUS_TEKS, hasil)
        if dapat != harap
    ]
    assert not salah, "penyiapan teks meleset:\n  " + "\n  ".join(salah)


def test_titik_akhir_kalimat_tidak_ikut_termakan(peramban, alamat):
    """Angka di ujung kalimat sempat menelan titiknya.

    Akibatnya berantai: tanpa titik, pecahUcapan() kehilangan batas kalimat
    dan dua kalimat dibacakan menyambung tanpa jeda.
    """
    hal = peramban.new_page()
    hal.goto(alamat, wait_until="networkidle")
    hasil = hal.evaluate("() => untukSuara('rentang $75.559–$78.065. Harga kini naik.')")
    assert hasil == "rentang 75 ribu 559 sampai 78 ribu 65 dolar. Harga kini naik."
    assert hal.evaluate("(t) => pecahUcapan(t).length", hasil) == 2


def test_potongan_ucapan_selalu_pendek(peramban, alamat):
    """Chrome memotong utterance panjang di sekitar detik ke-15, diam-diam."""
    hal = peramban.new_page()
    hal.goto(alamat, wait_until="networkidle")
    panjang = hal.evaluate("""() => {
      // Kalimat tanpa titik: kasus terburuk bagi pemecah berbasis kalimat.
      const kalimat = 'Kalimat panjang tanpa titik yang terus menyambung dengan koma, '.repeat(20);
      return pecahUcapan(kalimat).map((p) => p.length);
    }""")
    assert panjang, "tidak ada potongan yang dihasilkan"
    assert max(panjang) <= 220

    # Prosa produksi sungguhan juga harus lolos batas yang sama.
    dari_brief = hal.evaluate("""() => {
      const ai = Alpine.$data(document.querySelector('[x-data]')).data.ai;
      return pecahUcapan(untukSuara(ai.narrative)).map((p) => p.length);
    }""")
    assert dari_brief and max(dari_brief) <= 220


# ---------------------------------------------------------------------
# 2. Pemutar
# ---------------------------------------------------------------------

def test_tombol_muncul_dan_membacakan_isi_brief(halaman_suara):
    hal = halaman_suara()
    hal.get_by_role("button", name="Dengarkan").click()
    hal.wait_for_function("() => window.__ucapan.length > 3")

    ucapan = hal.evaluate("() => window.__ucapan")
    # Bahasa dan suara yang dipakai harus Indonesia, bukan bawaan sistem.
    assert all(u["lang"] == "id-ID" for u in ucapan)
    assert all(u["voice"] == "id-ID" for u in ucapan)

    gabung = " ".join(u["text"] for u in ucapan)
    # Yang dibacakan adalah prosa brief yang sudah dinormalkan — bukan
    # angka mentah, dan bukan isi tabel.
    assert "Ringkasan pasar kripto" in gabung
    assert "$" not in gabung
    assert "ribu" in gabung


def test_tombol_ikon_benar_benar_bergambar(halaman_suara):
    """Tombolnya tanpa label teks, jadi ikon yang gagal render = tombol kosong.

    Lucide bekerja dengan MENGGANTI <i data-lucide> jadi SVG, dan hanya pada
    elemen yang sudah ada di DOM saat createIcons() dipanggil. Tombol di sini
    hidup di dalam <template x-if> yang dirender ulang Alpine tiap kali
    status berubah — tanpa penggambaran ulang, tombolnya kotak polos.
    """
    hal = halaman_suara()

    tombol = hal.get_by_role("button", name="Dengarkan")
    assert tombol.locator("svg").count() == 1, "ikon tombol Dengarkan tidak tergambar"

    # Dan setelah status berubah, tombol yang BARU dirender juga harus digambar.
    tombol.click()
    hal.wait_for_function("() => window.__ucapan.length > 1")
    for nama in ("Jeda", "Lewati", "Berhenti"):
        assert hal.get_by_role("button", name=nama).locator("svg").count() == 1, (
            f"ikon tombol {nama} tidak tergambar"
        )


def test_bar_suara_tetap_utuh(halaman_suara):
    """Tombol, pengatur kecepatan, dan keterangan harus satu bar.

    Ini menangkap pembungkus flex yang hilang: tanpa `<div class="flex">`
    di sekeliling tombol, tag-nya tidak seimbang, `</div>` menutup bar
    terlalu awal, dan blok kemajuan serta keterangan terlempar keluar —
    halamannya tetap render, cuma berantakan, jadi tidak ada yang gagal.
    """
    hal = halaman_suara()
    bar = hal.locator("#bar-suara")

    # Keterangan dan pengatur kecepatan benar-benar DI DALAM bar.
    assert "Dibacakan suara bawaan" in bar.inner_text()
    assert bar.get_by_label("Kecepatan baca").count() == 1

    # Tombol dan pengatur kecepatan berbagi satu baris.
    tombol = hal.get_by_role("button", name="Dengarkan").bounding_box()
    kecepatan = bar.get_by_label("Kecepatan baca").bounding_box()
    assert abs(tombol["y"] - kecepatan["y"]) < tombol["height"], (
        "tombol dan pengatur kecepatan tidak sebaris — pembungkus flex hilang?"
    )

    # Kemajuan muncul saat membaca, dan tetap di dalam bar.
    hal.get_by_role("button", name="Dengarkan").click()
    hal.wait_for_function("() => window.__ucapan.length > 1")
    assert "%" in bar.inner_text()


def test_tombol_hilang_kalau_tidak_ada_suara_indonesia(halaman_suara):
    """Brief Indonesia dengan fonetik Inggris lebih buruk daripada diam."""
    hal = halaman_suara(STUB_TANPA_ID)
    assert hal.get_by_role("button", name="Dengarkan").count() == 0


def test_jeda_menghentikan_antrean(halaman_suara):
    hal = halaman_suara()
    hal.get_by_role("button", name="Dengarkan").click()
    hal.wait_for_function("() => window.__ucapan.length > 2")
    hal.get_by_role("button", name="Jeda").click()

    jumlah = hal.evaluate("() => window.__ucapan.length")
    hal.wait_for_timeout(150)
    # Setelah jeda, tidak boleh ada potongan baru yang diantrekan.
    assert hal.evaluate("() => window.__ucapan.length") == jumlah
    hal.get_by_role("button", name="Lanjut").wait_for(state="visible")


def test_berhenti_membatalkan_dan_mengembalikan_tombol(halaman_suara):
    hal = halaman_suara()
    hal.get_by_role("button", name="Dengarkan").click()
    hal.wait_for_function("() => window.__ucapan.length > 2")
    hal.get_by_role("button", name="Berhenti").click()

    assert hal.evaluate("() => window.__batal") > 0
    hal.get_by_role("button", name="Dengarkan").wait_for(state="visible")


def test_lewati_melompat_ke_bagian_berikutnya(halaman_suara):
    hal = halaman_suara()
    hal.get_by_role("button", name="Dengarkan").click()
    hal.wait_for_function("() => window.__ucapan.length > 1")

    judul_awal = hal.evaluate("() => Alpine.$data(document.querySelector('[x-data]')).suaraJudul")
    hal.get_by_role("button", name="Lewati").click()
    hal.wait_for_function(
        "(awal) => Alpine.$data(document.querySelector('[x-data]')).suaraJudul !== awal",
        arg=judul_awal,
    )


def test_kecepatan_diterapkan_ke_ucapan_berikutnya(halaman_suara):
    hal = halaman_suara()
    # Halaman ini punya lima <select>; harus yang benar.
    hal.get_by_label("Kecepatan baca").select_option("1.5")
    hal.get_by_role("button", name="Dengarkan").click()
    hal.wait_for_function("() => window.__ucapan.length > 1")
    assert hal.evaluate("() => window.__ucapan[0].rate") == 1.5


def test_segmen_mengikuti_urutan_baca_halaman(halaman_suara):
    """Geopolitik lebih dulu, sama seperti urutan tampil di halaman."""
    hal = halaman_suara()
    judul = hal.evaluate(
        "() => Alpine.$data(document.querySelector('[x-data]')).segmenSuara.map((s) => s.judul)"
    )
    assert judul[0] == "Pembuka"
    assert "Geopolitik & regulasi" in judul
    assert judul.index("Geopolitik & regulasi") < judul.index("Narasi utama")
    # Tabel, chip, dan angka makro sengaja tidak ikut dibacakan.
    assert not any("Makro" in j for j in judul)
