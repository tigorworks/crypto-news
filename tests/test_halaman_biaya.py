"""Halaman biaya per run (docs/cost.html).

Yang dijaga di sini bukan tata letaknya, tapi tiga hal yang membuat halaman
ini berguna atau tidak sama sekali:

  1. Tiap run muncul sebagai satu baris, dan angkanya angka run ITU — bukan
     rata-rata. Halaman ini ada justru karena rata-rata menghapus sebab
     sebuah run jadi mahal.
  2. Rincian per langkah benar-benar terbuka saat barisnya diklik. Kalau
     popupnya tidak muncul, seluruh isi halaman ini tinggal delapan kolom
     angka tanpa jawaban "kenapa".
  3. Run lama yang rinciannya tidak pernah tercatat dikatakan apa adanya.
     Tabel kosong tanpa keterangan terbaca sebagai run yang gratis.
"""

from __future__ import annotations

import pytest


def _buka(peramban, alamat_biaya):
    halaman = peramban.new_page()
    halaman.goto(alamat_biaya, wait_until="networkidle")
    return halaman


@pytest.fixture
def ringkasan_uji():
    """Dua run: satu dengan rincian lengkap, satu dari era sebelum ada rincian."""
    return {
        "dibuat": "2026-08-23T00:00:00Z",
        "jumlah_run": 2,
        "total_run_tersimpan": 2,
        "biaya": {
            "rata_usd": 0.25,
            "maks_usd": 0.3,
            "terakhir_usd": 0.2,
            "run_di_atas_85_persen_budget": 0,
        },
        "run": [
            {
                "waktu_utc": "2026-08-22T23:43:00Z",
                "run_type": "pagi",
                "harga": 76923.3,
                "biaya_usd": 0.2,
                "budget_maks_usd": 0.4,
                "budget_terpakai_pct": 50.0,
                "token_masuk": 74986,
                "token_keluar": 36699,
                "panggilan_llm": 21,
                "durasi_detik": 443.8,
                "critic": {"dijalankan": True, "lolos": True},
                "langkah": [
                    {
                        "langkah": "synthesis", "biaya_usd": 0.15,
                        "masuk": 12698, "keluar": 5850, "prompt_char": 45000,
                        "panggilan": 1, "model": ["openai/gpt-5.1"],
                    },
                    {
                        "langkah": "filter", "biaya_usd": 0.05,
                        "masuk": 9992, "keluar": 4234, "prompt_char": 32000,
                        "panggilan": 2,
                        "model": ["deepseek/deepseek-v3.2", "anthropic/claude-haiku-4.5"],
                    },
                ],
            },
            {
                "waktu_utc": "2026-08-17T11:28:02Z",
                "run_type": "sore",
                "harga": 74000.0,
                "biaya_usd": 0.3,
                "budget_maks_usd": 0.75,
                "budget_terpakai_pct": 40.0,
                "token_masuk": 60000,
                "token_keluar": 30000,
                "panggilan_llm": 19,
                "durasi_detik": 456.8,
                "critic": {"dijalankan": True, "lolos": False},
                "langkah": [],
            },
        ],
    }


def test_tabel_menampilkan_satu_baris_per_run(peramban, alamat_biaya, tulis_telemetri, ringkasan_uji):
    tulis_telemetri(ringkasan_uji)
    halaman = _buka(peramban, alamat_biaya)

    baris = halaman.locator("tbody tr")
    assert baris.count() == 2

    # Angka yang ditampilkan harus angka run itu sendiri. 74.986 adalah token
    # masuk run pertama; kalau yang muncul rata-rata kedua run, halaman ini
    # sedang menjawab pertanyaan yang salah.
    pertama = baris.nth(0)
    assert "74.986" in pertama.inner_text()
    assert "$0,20" in pertama.inner_text()

    # Total di kaki tabel menjumlah, bukan merata-rata.
    kaki = halaman.locator("tfoot").inner_text()
    assert "134.986" in kaki      # 74.986 + 60.000
    assert "$0,50" in kaki        # 0,20 + 0,30


def test_run_yang_memicu_revisi_ditandai(peramban, alamat_biaya, tulis_telemetri, ringkasan_uji):
    """Critic yang menahan narasi adalah sebab run mahal yang paling sering."""
    tulis_telemetri(ringkasan_uji)
    halaman = _buka(peramban, alamat_biaya)

    assert "revisi" in halaman.locator("tbody tr").nth(1).inner_text()
    assert "revisi" not in halaman.locator("tbody tr").nth(0).inner_text()


def test_klik_baris_membuka_rincian_per_langkah(peramban, alamat_biaya, tulis_telemetri, ringkasan_uji):
    tulis_telemetri(ringkasan_uji)
    halaman = _buka(peramban, alamat_biaya)

    assert halaman.locator("[role=dialog]").count() == 0
    halaman.locator("tbody tr").nth(0).click()

    dialog = halaman.locator("[role=dialog]")
    dialog.wait_for(state="visible")
    isi = dialog.inner_text()

    # Langkah, biayanya, dan model yang melayaninya.
    assert "synthesis" in isi
    assert "openai/gpt-5.1" in isi
    assert "$0,15" in isi
    # Satu langkah yang dibatch bisa dilayani dua model; keduanya tampil.
    assert "deepseek/deepseek-v3.2" in isi
    assert "anthropic/claude-haiku-4.5" in isi
    # Porsi terhadap total run: 0,15 dari 0,20.
    assert "75,0%" in isi or "75.0%" in isi


def test_escape_menutup_rincian(peramban, alamat_biaya, tulis_telemetri, ringkasan_uji):
    tulis_telemetri(ringkasan_uji)
    halaman = _buka(peramban, alamat_biaya)

    halaman.locator("tbody tr").nth(0).click()
    halaman.locator("[role=dialog]").wait_for(state="visible")
    halaman.keyboard.press("Escape")
    halaman.wait_for_selector("[role=dialog]", state="detached")


def test_baris_bisa_dibuka_lewat_keyboard(peramban, alamat_biaya, tulis_telemetri, ringkasan_uji):
    """Pemicunya sebuah <tr>; tanpa penanganan tombol ia tak terjangkau keyboard."""
    tulis_telemetri(ringkasan_uji)
    halaman = _buka(peramban, alamat_biaya)

    halaman.locator("tbody tr").nth(0).focus()
    halaman.keyboard.press("Enter")
    halaman.locator("[role=dialog]").wait_for(state="visible")


def test_run_tanpa_rincian_dijelaskan(peramban, alamat_biaya, tulis_telemetri, ringkasan_uji):
    """Tabel kosong tanpa keterangan terbaca sebagai run yang tidak berbiaya."""
    tulis_telemetri(ringkasan_uji)
    halaman = _buka(peramban, alamat_biaya)

    halaman.locator("tbody tr").nth(1).click()
    dialog = halaman.locator("[role=dialog]")
    dialog.wait_for(state="visible")
    assert "rinciannya tidak tersedia" in dialog.inner_text()


def test_telemetri_gagal_dimuat_dinyatakan(peramban, alamat_biaya, situs):
    """Halaman kosong tidak boleh menyamar jadi 'belum ada run'."""
    (situs / "data" / "telemetri.json").unlink()
    halaman = _buka(peramban, alamat_biaya)
    assert "Gagal memuat" in halaman.inner_text("main")


def test_bisa_dicapai_dari_brief(peramban, alamat):
    """Halaman yang tidak ditautkan dari mana pun sama saja tidak ada."""
    halaman = peramban.new_page()
    halaman.goto(alamat, wait_until="networkidle")

    tautan = halaman.locator('a[href="cost.html"]')
    assert tautan.count() >= 1
    tautan.first.click()
    halaman.wait_for_url("**/cost.html")
    assert halaman.locator("tbody tr").count() > 0


def test_halaman_produksi_memuat_data_sungguhan(peramban, alamat_biaya):
    """Tanpa fixture: bentuk telemetri produksi harus benar-benar terbaca.

    Fixture buatan tangan selalu ketinggalan bentuk data yang sebenarnya —
    dan itu kelas kesalahan yang paling sering lolos di repo ini.
    """
    halaman = _buka(peramban, alamat_biaya)
    assert halaman.locator("tbody tr").count() > 0
    assert "Gagal memuat" not in halaman.inner_text("main")

    halaman.locator("tbody tr").nth(0).click()
    halaman.locator("[role=dialog]").wait_for(state="visible")
