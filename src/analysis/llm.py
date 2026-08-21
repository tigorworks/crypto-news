"""Klien OpenRouter dengan fallback antar model, budget, dan logging biaya."""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional

from ..utils.http import HttpError, request

log = logging.getLogger(__name__)

TIMEOUT = 60
RETRIES = 2


class BudgetExceeded(Exception):
    """Budget LLM per run sudah habis."""


class LLMError(Exception):
    """Panggilan LLM gagal atau balasannya tidak bisa dipakai."""


def _strip_fences(text: str) -> str:
    """Buang pagar markdown ```json ... ``` kalau model tetap memakainya."""
    text = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        return fence.group(1).strip()
    return text


_NILAI_ROMAWI = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5}

# Angka Romawi telanjang sebagai NILAI JSON — `"kekuatan": III,`. Bukan JSON
# valid, dan satu kemunculan menggugurkan SELURUH batch. Terlihat di produksi
# pada langkah `statements` setelah pindah ke DeepSeek: enam pernyataan hilang
# sekaligus hanya karena skala 1-5 ditulis I..V.
#
# Sengaja dibatasi I..V (jangkauan semua skala 1-5 di proyek ini) dan hanya
# yang berdiri sebagai nilai setelah titik dua. Membatasi seketat ini penting
# karena perbaikan bekerja di atas teks mentah: pola yang lebih longgar bisa
# ikut mengubah huruf di dalam string yang sah.
_POLA_ROMAWI_NILAI = re.compile(r"(:\s*)(I{1,3}|IV|V)(\s*[,}\]])")


# Karakter kontrol yang ILEGAL mentah-mentah di dalam string JSON. Spesifikasi
# JSON mewajibkan semuanya di-escape; model yang menulis narasi berparagraf
# kerap menaruh line break SUNGGUHAN di dalam nilai string, dan satu saja
# menggugurkan seluruh balasan.
_KONTROL_KE_ESCAPE = {
    "\n": "\\n", "\r": "\\r", "\t": "\\t", "\b": "\\b", "\f": "\\f",
}


def _escape_kontrol_dalam_string(teks: str) -> str:
    """Escape karakter kontrol mentah yang berada DI DALAM nilai string.

    Ini akar dari kegagalan parse yang paling merusak di proyek ini: langkah
    sintesis diminta menulis narasi 6-9 paragraf, dan model memisahkan
    paragrafnya dengan newline SUNGGUHAN, bukan `\\n`. Hasilnya JSON tidak
    sah ("Invalid control character") dan SELURUH narasi hangus — bukan cuma
    satu field.

    Pemindaian dilakukan per karakter dengan melacak status di-dalam-string,
    bukan lewat regex: batas string hanya bisa ditentukan dengan menghitung
    escape secara berurutan, dan regex tidak bisa melakukannya dengan benar.
    Struktur di LUAR string (newline antar field) sengaja tidak disentuh.
    """
    hasil: List[str] = []
    dalam_string = False
    escape = False
    for ch in teks:
        if escape:
            hasil.append(ch)
            escape = False
            continue
        if ch == "\\":
            hasil.append(ch)
            escape = True
            continue
        if ch == '"':
            dalam_string = not dalam_string
            hasil.append(ch)
            continue
        if dalam_string:
            pengganti = _KONTROL_KE_ESCAPE.get(ch)
            if pengganti is not None:
                hasil.append(pengganti)
                continue
            if ord(ch) < 0x20:
                hasil.append(f"\\u{ord(ch):04x}")
                continue
        hasil.append(ch)
    return "".join(hasil)


def _cacah_alnum(teks: str) -> int:
    return sum(1 for c in teks if c.isalnum())


def _isi_terjaga(asli: str, hasil: Any, ambang: float = 0.70) -> bool:
    """True kalau hasil perbaikan masih memuat sebagian besar isi aslinya.

    Pengaman terhadap kegagalan DIAM-DIAM, yang jauh lebih berbahaya daripada
    error: sebuah perbaikan bisa saja menghasilkan JSON yang sah tapi isinya
    sudah terpotong — misalnya string multi-baris ditutup lebih awal sehingga
    tiga dari empat paragraf hilang, namun sisanya kebetulan tetap bisa
    diparse. Tanpa pemeriksaan ini, brief akan terbit dengan analisa yang
    terpangkas TANPA satu pun peringatan.

    Dibandingkan lewat jumlah karakter alfanumerik supaya perbedaan tanda
    baca, escape, dan spasi tidak ikut terhitung.
    """
    asli_n = _cacah_alnum(asli)
    if asli_n == 0:
        return True
    hasil_n = _cacah_alnum(json.dumps(hasil, ensure_ascii=False))
    rasio = hasil_n / asli_n
    if rasio < ambang:
        log.warning(
            "Perbaikan JSON ditolak: isinya menyusut jadi %.0f%% dari balasan asli "
            "(kemungkinan sebagian konten terpotong diam-diam)", rasio * 100,
        )
        return False
    if rasio < 0.95:
        log.warning(
            "Perbaikan JSON dipakai, tapi isinya %.0f%% dari aslinya — periksa "
            "kalau ada bagian yang terasa hilang", rasio * 100,
        )
    return True


def _perbaiki_json(teks: str) -> str:
    """Perbaiki kerusakan JSON yang lazim dari keluaran model.

    Kerusakan yang paling sering muncul dan bisa dipulihkan dengan aman:
    angka Romawi telanjang sebagai nilai, koma menggantung sebelum penutup,
    string yang belum ditutup di ujung balasan, dan kurung yang belum
    seimbang karena keluarannya terpotong. Memulihkan sebagian temuan jauh
    lebih berguna daripada membuang seluruh balasan yang tokennya sudah
    dibayar.
    """
    teks = teks.strip()

    # Didahulukan sebelum penyeimbangan kutip/kurung: penggantiannya tidak
    # mengubah jumlah kutip maupun kurung, jadi urutan ini aman, sementara
    # kebalikannya bisa menambal kutip di tempat yang salah.
    teks = _POLA_ROMAWI_NILAI.sub(
        lambda m: f"{m.group(1)}{_NILAI_ROMAWI[m.group(2)]}{m.group(3)}", teks
    )

    # String yang belum ditutup: hitung kutip ganda yang tidak di-escape.
    kutip = len(re.findall(r'(?<!\\)"', teks))
    if kutip % 2 == 1:
        teks += '"'

    # Koma menggantung sebelum } atau ]
    teks = re.sub(r",\s*([}\]])", r"\1", teks)

    # Seimbangkan kurung mengikuti urutan pembukaannya. Kutip di dalam string
    # diabaikan supaya tanda kurung dalam teks tidak ikut terhitung.
    tumpukan: List[str] = []
    dalam_string = False
    escape = False
    for ch in teks:
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            dalam_string = not dalam_string
            continue
        if dalam_string:
            continue
        if ch in "{[":
            tumpukan.append(ch)
        elif ch in "}]":
            if tumpukan:
                tumpukan.pop()

    if tumpukan:
        teks += "".join("}" if ch == "{" else "]" for ch in reversed(tumpukan))
        # Elemen terakhir bisa saja belum lengkap; koma menggantung dibersihkan lagi.
        teks = re.sub(r",\s*([}\]])", r"\1", teks)

    return teks


# Baris berbentuk `  "kunci": "nilai...` — pintu masuk dua kerusakan yang
# paling sering merontokkan balasan panjang: kutip di dalam nilai yang lupa
# di-escape, dan string yang lupa ditutup.
_POLA_BARIS_FIELD = re.compile(r'^(\s*"[^"\\]+"\s*:\s*)"(.*)$')


def _rapikan_string_baris(teks: str) -> str:
    """Escape kutip liar di dalam nilai string, dan tutup string yang menggantung.

    Dua kerusakan nyata dari produksi, keduanya pada satu balasan sintesis
    (~700 kata) yang akhirnya hangus seluruhnya:

      "yang_diwaspadai": "... label tren "jual menguat" — ambigu."
                                        ^^^^^^^^^^^^^^ kutip tanpa escape
      "penyebab": "... tapi yield UST 10Y jus
                                            ^ string tidak pernah ditutup

    Keduanya tidak bisa ditangani penyeimbang kutip/kurung yang sudah ada:
    yang pertama jumlah kutipnya genap (jadi terlihat "seimbang"), yang kedua
    membuat sisa dokumen ikut tertelan ke dalam string.

    Bekerja per baris dan hanya pada baris yang benar-benar berbentuk
    `"kunci": "nilai`. Nilai non-string (angka, array, objek) tidak cocok
    dengan polanya, jadi tidak tersentuh.
    """
    baris = teks.split("\n")
    hasil: List[str] = []
    for i, isi in enumerate(baris):
        cocok = _POLA_BARIS_FIELD.match(isi)
        if not cocok:
            hasil.append(isi)
            continue

        awalan, sisa = cocok.group(1), cocok.group(2)

        # Terminator di ujung baris dilepas dulu supaya tidak ikut di-escape.
        ekor = ""
        tanpa_spasi = sisa.rstrip()
        for kandidat in ('",', '"'):
            if tanpa_spasi.endswith(kandidat):
                sisa = tanpa_spasi[: len(tanpa_spasi) - len(kandidat)]
                ekor = kandidat
                break

        if not ekor:
            # String menggantung. Ditutup di sini; komanya menyusul kalau
            # baris berikutnya memang memulai field baru.
            berikut = baris[i + 1].lstrip() if i + 1 < len(baris) else ""
            ekor = '",' if berikut.startswith('"') else '"'

        sisa = re.sub(r'(?<!\\)"', r'\\"', sisa)
        hasil.append(f"{awalan}\"{sisa}{ekor}")
    return "\n".join(hasil)


def _rapikan_kutip_liar(teks: str) -> str:
    """Escape kutip liar tanpa bergantung pada pergantian baris.

    `_rapikan_string_baris` bekerja per baris, jadi tidak menyentuh balasan
    yang ditulis MINIFIED — seluruh objek dalam satu baris. Model yang sama
    kadang mengembalikan bentuk itu, dan kutip liar di dalamnya membuat
    seluruh balasan hangus.

    Pemindainya per karakter: saat berada di dalam string dan bertemu kutip
    yang tidak di-escape, kutip itu dianggap PENUTUP hanya kalau karakter
    berikutnya (setelah spasi) memang bisa mengikuti akhir string dalam JSON
    — `, : } ]` atau akhir teks. Selain itu, kutipnya bagian dari isi dan
    di-escape.
    """
    hasil: List[str] = []
    dalam_string = False
    i = 0
    n = len(teks)
    while i < n:
        c = teks[i]
        if not dalam_string:
            hasil.append(c)
            if c == '"':
                dalam_string = True
            i += 1
            continue

        if c == "\\":
            hasil.append(teks[i : i + 2])
            i += 2
            continue

        if c == '"':
            j = i + 1
            while j < n and teks[j] in " \t\r\n":
                j += 1
            if j >= n or teks[j] in ",:}]":
                hasil.append(c)
                dalam_string = False
            else:
                hasil.append('\\"')
            i += 1
            continue

        hasil.append(c)
        i += 1
    return "".join(hasil)


def _extract_json(text: str) -> Any:
    """Parse JSON dari balasan model, seagresif yang diperlukan.

    Urutan: parse apa adanya -> buang pagar -> ambil blok terluar ->
    perbaiki kerusakan yang lazim.
    """
    candidates = [text, _strip_fences(text)]
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue

    cleaned = _strip_fences(text)

    # Perbaikan dicoba pada SELURUH teks lebih dulu: kalau berhasil,
    # strukturnya utuh dan tidak perlu menebak-nebak potongan mana yang benar.
    #
    # Urutannya dari yang paling aman ke yang paling agresif:
    #   1. escape karakter kontrol   — deterministik, tidak menebak apa pun
    #   2. rapikan nilai string      — memakai heuristik batas string per baris
    #   3. keduanya digabung         — untuk balasan yang rusak berlapis
    #   4. kutip liar tanpa baris    — untuk balasan minified, di mana (2)
    #                                  tidak punya baris untuk dijadikan patokan
    #
    # Tiap hasil WAJIB lolos _isi_terjaga(): sebuah perbaikan yang menghasilkan
    # JSON sah tapi isinya terpangkas lebih berbahaya daripada gagal parse,
    # karena brief akan terbit dengan analisa terpotong tanpa peringatan.
    perbaikan = (
        ("karakter kontrol mentah di dalam string", _escape_kontrol_dalam_string),
        ("nilai string rusak", _rapikan_string_baris),
        (
            "karakter kontrol + nilai string rusak",
            lambda t: _rapikan_string_baris(_escape_kontrol_dalam_string(t)),
        ),
        ("kutip liar di balasan satu baris", _rapikan_kutip_liar),
        (
            "karakter kontrol + kutip liar",
            lambda t: _rapikan_kutip_liar(_escape_kontrol_dalam_string(t)),
        ),
    )
    for nama, perbaiki in perbaikan:
        try:
            hasil = json.loads(perbaiki(cleaned))
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
        if not _isi_terjaga(cleaned, hasil):
            continue
        log.warning("JSON model rusak (%s), berhasil dipulihkan utuh", nama)
        return hasil

    potongan = []
    # Urutan mengikuti kurung yang muncul lebih dulu di teks. Kalau balasan
    # diawali '{' tapi memuat '[' di dalamnya, mencoba '[' lebih dulu akan
    # menghasilkan list dari isi perutnya — bukan objek yang diminta.
    pasangan = [("[", "]"), ("{", "}")]
    posisi = {o: (cleaned.find(o) if cleaned.find(o) != -1 else len(cleaned)) for o, _ in pasangan}
    pasangan.sort(key=lambda p: posisi[p[0]])
    # Objek yang rusak TIDAK boleh jatuh jadi array. Sebuah objek hampir selalu
    # memuat array di dalamnya (mis. `data_pendukung`), dan mengambil array itu
    # menghasilkan struktur yang parse-nya sukses tapi isinya sama sekali bukan
    # yang diminta — kegagalan diam-diam, yang jauh lebih berbahaya daripada
    # error. Lebih baik melempar dan membiarkan pemanggil menangani.
    if posisi["{"] < posisi["["]:
        pasangan = [("{", "}")]
    for opener, closer in pasangan:
        start = cleaned.find(opener)
        end = cleaned.rfind(closer)
        if start != -1 and end > start:
            potongan.append(cleaned[start : end + 1])
        elif start != -1:
            # Penutup tidak ada sama sekali: balasan terpotong di tengah.
            potongan.append(cleaned[start:])

    # Jalur terakhir: memotong blok kurung terluar lalu menambal. Paling
    # agresif, jadi ambang isi-terjaganya dinaikkan — pemulihan "sebagian"
    # yang menyisakan seperempat isi bukan pemulihan, itu kehilangan data
    # yang menyamar jadi keberhasilan.
    for bagian in potongan:
        # Pembandingnya `bagian`, BUKAN `cleaned`. Pertanyaan yang ingin
        # dijawab adalah "apakah menambal potongan ini membuang isinya",
        # dan preamble di luar potongan tidak pernah dimaksudkan jadi JSON.
        # Memakai `cleaned` membuat balasan yang diawali basa-basi panjang
        # ("Baik, saya sudah menganalisa ... Berikut hasilnya:") ditolak
        # padahal JSON-nya utuh dan sah — persis kesalahan yang sempat
        # terjadi setelah pengaman ini pertama dipasang.
        try:
            hasil = json.loads(bagian)
            if _isi_terjaga(bagian, hasil):
                return hasil
        except json.JSONDecodeError:
            pass
        try:
            hasil = json.loads(_perbaiki_json(bagian))
        except json.JSONDecodeError:
            continue
        if not _isi_terjaga(bagian, hasil, ambang=0.80):
            continue
        log.warning("JSON model rusak tapi berhasil dipulihkan sebagian")
        return hasil

    # Run produksi jalan di level INFO (lihat main.py), jadi log.debug tidak
    # pernah tercatat di sana — kegagalan macam ini jadi mustahil didiagnosis
    # karena satu-satunya bukti yang tersisa cuma potongan 300 karakter di
    # pesan exception. Dicatat di level warning supaya balasan penuhnya ikut
    # ada di log produksi saat ini terjadi lagi.
    log.warning("Balasan yang gagal diparse:\n%s", text)
    raise LLMError(f"Balasan model bukan JSON valid: {text[:300]}")


class LLMClient:
    """Pembungkus OpenRouter chat completions.

    Biaya diakumulasi per run. Begitu melewati batas, semua panggilan
    berikutnya melempar BudgetExceeded — pemanggil bertanggung jawab
    menangkapnya dan melanjutkan dengan data seadanya.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://openrouter.ai/api/v1",
        max_cost_usd: float = 0.15,
        referer: str = "",
        title: str = "BTC Market Brief",
        reasoning_effort: Optional[Dict[str, str]] = None,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.max_cost_usd = max_cost_usd
        # Upaya penalaran per step. Hanya diterapkan pada step yang memang
        # terdaftar — lihat catatan panjang di config.yaml soal kenapa ini
        # TIDAK boleh dipasang sembarangan pada model non-penalar.
        self.reasoning_effort = dict(reasoning_effort or {})
        self.total_cost = 0.0
        # Peringatan anggaran hanya dibunyikan sekali per run; kalau tiap
        # panggilan ikut berteriak, log-nya jadi tidak terbaca justru saat
        # yang penting terjadi.
        self._sudah_peringatkan_budget = False
        self.calls: List[Dict[str, Any]] = []
        self.models_used: List[str] = []
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        if referer:
            self._headers["HTTP-Referer"] = referer
        if title:
            self._headers["X-Title"] = title

    # -- budget --------------------------------------------------------
    @property
    def budget_habis(self) -> bool:
        return self.total_cost >= self.max_cost_usd

    def _cek_budget(self) -> None:
        if self.budget_habis:
            raise BudgetExceeded(
                f"Budget LLM habis: ${self.total_cost:.4f} >= ${self.max_cost_usd:.4f}"
            )

    # -- biaya ---------------------------------------------------------
    def _catat_biaya(self, model: str, usage: Dict[str, Any], durasi: float, step: str) -> None:
        """Ambil biaya dari usage OpenRouter.

        OpenRouter mengembalikan biaya aktual di `usage.cost` (USD) saat
        `usage.include` aktif. Kalau tidak ada, biaya dianggap 0 dan hanya
        token yang dicatat — lebih baik daripada menebak tarif per model.
        """
        cost = float(usage.get("cost") or 0.0)
        tokens_in = int(usage.get("prompt_tokens") or 0)
        tokens_out = int(usage.get("completion_tokens") or 0)
        self.total_cost += cost
        entry = {
            "step": step,
            "model": model,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cost_usd": round(cost, 6),
            "durasi_detik": round(durasi, 2),
        }
        self.calls.append(entry)
        if model not in self.models_used:
            self.models_used.append(model)
        log.info(
            "LLM %-9s | %-32s | in %5d out %5d | $%.5f | %.1fs | total $%.5f",
            step, model, tokens_in, tokens_out, cost, durasi, self.total_cost,
        )

        # Peringatan dini sebelum anggaran benar-benar habis. Tanpa ini,
        # satu-satunya pertanda adalah langkah yang tiba-tiba dilewati di
        # ujung pipeline — dan yang berada di ujung justru revisi critic,
        # bagian yang paling mahal kalau hilang. Ambang 85% dipilih supaya
        # masih ada ruang untuk satu panggilan besar sesudahnya.
        if (
            not self._sudah_peringatkan_budget
            and self.max_cost_usd
            and self.total_cost >= self.max_cost_usd * 0.85
        ):
            self._sudah_peringatkan_budget = True
            log.warning(
                "Anggaran LLM sudah terpakai %.0f%% ($%.4f dari $%.4f) setelah step '%s' "
                "— langkah berikutnya berisiko terpotong",
                self.total_cost / self.max_cost_usd * 100,
                self.total_cost, self.max_cost_usd, step,
            )

    # -- panggilan -----------------------------------------------------
    def chat(
        self,
        models: List[str],
        system: str,
        user: str,
        *,
        step: str = "unknown",
        temperature: float = 0.2,
        max_tokens: int = 2000,
        json_schema: Optional[Dict[str, Any]] = None,
        extra_body: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Kirim satu percakapan; return teks balasan mentah.

        `extra_body` menyisipkan field khusus provider ke payload — dipakai
        langkah `x_posts` untuk menyalakan Live Search xAI supaya Grok
        menjawab dari hasil pencarian X yang sungguhan, bukan dari ingatan
        model. Kalau OpenRouter tidak meneruskannya, field ini diabaikan
        server dan tidak merusak apa pun; jaring pengamannya tetap di kode
        (lihat src/collectors/x_grok.py).
        """
        if not models:
            raise LLMError(f"Tidak ada model terkonfigurasi untuk step '{step}'")
        self._cek_budget()

        payload: Dict[str, Any] = {
            "model": models[0],
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "usage": {"include": True},
        }
        # OpenRouter memakai `models` untuk fallback otomatis antar provider.
        if len(models) > 1:
            payload["models"] = models
        # Upaya penalaran, kalau step ini memang diatur. Pada model penalar
        # (GPT-5.x, Gemini) token penalaran ikut dihitung sebagai keluaran
        # DAN ikut memakan jatah max_tokens — jadi ini sekaligus soal biaya
        # dan soal balasan yang terpotong sebelum sempat menjawab.
        effort = self.reasoning_effort.get(step)
        if effort:
            payload["reasoning"] = {"effort": effort}

        if json_schema:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": step, "strict": False, "schema": json_schema},
            }
        if extra_body:
            payload.update(extra_body)

        mulai = time.time()
        try:
            resp = request(
                "POST",
                f"{self.base_url}/chat/completions",
                json_body=payload,
                headers=self._headers,
                timeout=TIMEOUT,
                retries=RETRIES,
            )
        except HttpError as exc:
            raise LLMError(f"Panggilan LLM step '{step}' gagal: {exc}") from exc

        durasi = time.time() - mulai
        try:
            data = resp.json()
        except ValueError as exc:
            raise LLMError(f"Balasan OpenRouter bukan JSON: {exc}") from exc

        if "error" in data and not data.get("choices"):
            raise LLMError(f"OpenRouter error pada step '{step}': {data['error']}")

        try:
            choice = data["choices"][0]
            content = choice["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Struktur balasan tidak dikenali pada step '{step}': {exc}") from exc

        # Biaya dicatat lebih dulu: token yang terpakai tetap ditagih walaupun
        # balasannya nanti kita tolak.
        self._catat_biaya(
            data.get("model", models[0]), data.get("usage") or {}, durasi, step
        )

        # Balasan yang terpotong di batas max_tokens hampir selalu JSON tak
        # lengkap. Tanpa pemeriksaan ini, parser akan menyelamatkan potongan
        # objek yang sekilas valid tapi kehilangan field wajib — dan step-nya
        # gagal diam-diam setelah token telanjur dibayar.
        finish = choice.get("finish_reason") or choice.get("native_finish_reason")
        if finish == "length":
            raise LLMError(
                f"Balasan step '{step}' terpotong di batas max_tokens "
                f"({max_tokens}). Naikkan max_tokens untuk step ini."
            )

        return content or ""

    def chat_json(self, models: List[str], system: str, user: str, **kwargs) -> Any:
        """Sama seperti chat(), tapi balasannya langsung diparse jadi objek Python."""
        return _extract_json(self.chat(models, system, user, **kwargs))

    # -- ringkasan -----------------------------------------------------
    def model_terpakai(self, step: str) -> Optional[str]:
        """Model yang benar-benar melayani step ini (bisa jadi cadangan)."""
        for panggilan in reversed(self.calls):
            if panggilan["step"] == step:
                return panggilan["model"]
        return None

    @property
    def total_token_masuk(self) -> int:
        return sum(int(c.get("tokens_in") or 0) for c in self.calls)

    @property
    def total_token_keluar(self) -> int:
        return sum(int(c.get("tokens_out") or 0) for c in self.calls)

    def ringkasan(self) -> Dict[str, Any]:
        return {
            "total_cost_usd": round(self.total_cost, 5),
            "jumlah_panggilan": len(self.calls),
            "token_masuk": self.total_token_masuk,
            "token_keluar": self.total_token_keluar,
            "models_used": self.models_used,
            "budget_habis": self.budget_habis,
        }
