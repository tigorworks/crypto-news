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
    potongan = []
    # Urutan mengikuti kurung yang muncul lebih dulu di teks. Kalau balasan
    # diawali '{' tapi memuat '[' di dalamnya, mencoba '[' lebih dulu akan
    # menghasilkan list dari isi perutnya — bukan objek yang diminta.
    pasangan = [("[", "]"), ("{", "}")]
    posisi = {o: (cleaned.find(o) if cleaned.find(o) != -1 else len(cleaned)) for o, _ in pasangan}
    pasangan.sort(key=lambda p: posisi[p[0]])
    for opener, closer in pasangan:
        start = cleaned.find(opener)
        end = cleaned.rfind(closer)
        if start != -1 and end > start:
            potongan.append(cleaned[start : end + 1])
        elif start != -1:
            # Penutup tidak ada sama sekali: balasan terpotong di tengah.
            potongan.append(cleaned[start:])

    for bagian in potongan:
        try:
            return json.loads(bagian)
        except json.JSONDecodeError:
            pass
        try:
            hasil = json.loads(_perbaiki_json(bagian))
            log.warning("JSON model rusak tapi berhasil dipulihkan sebagian")
            return hasil
        except json.JSONDecodeError:
            continue

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
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.max_cost_usd = max_cost_usd
        self.total_cost = 0.0
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
    ) -> str:
        """Kirim satu percakapan; return teks balasan mentah."""
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
        if json_schema:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": step, "strict": False, "schema": json_schema},
            }

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
