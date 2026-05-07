import streamlit as st
import json
import re
import time
import requests
import docx
from PyPDF2 import PdfReader

st.set_page_config(page_title="SIPA-KTI Poltekkes Maluku", layout="wide", page_icon="📚")
st.markdown("""
    <style>
    .stAlert { border-radius: 10px; }
    .stButton>button { width:100%; background-color:#00796b; color:white; border-radius:8px; padding:0.5rem; font-size:16px; }
    </style>
""", unsafe_allow_html=True)

# Model Gemini yang dicoba berurutan (fallback jika satu tidak tersedia)
GEMINI_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-2.0-flash",
    "gemini-1.5-flash",
]


def extract_text(file) -> str:
    try:
        if file.type == "application/pdf":
            reader = PdfReader(file)
            return "\n".join(p.extract_text() for p in reader.pages if p.extract_text())
        elif "wordprocessingml" in file.type:
            doc = docx.Document(file)
            return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    except Exception as e:
        st.error(f"Gagal membaca file: {e}")
    return ""


def potong_teks(teks: str, ukuran: int = 3000) -> list:
    paragraf = teks.split("\n")
    chunks, chunk_saat_ini = [], ""
    for par in paragraf:
        if not par.strip():
            continue
        if len(chunk_saat_ini) + len(par) + 1 <= ukuran:
            chunk_saat_ini += par + "\n"
        else:
            if chunk_saat_ini.strip():
                chunks.append(chunk_saat_ini.strip())
            chunk_saat_ini = par + "\n"
    if chunk_saat_ini.strip():
        chunks.append(chunk_saat_ini.strip())
    return chunks if chunks else [teks[:3000]]


def parse_hasil(raw: str):
    bersih = re.sub(r"```(?:json)?", "", raw, flags=re.IGNORECASE).strip().strip("`").strip()
    try:
        parsed = json.loads(bersih)
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict):
            for key in ("items", "errors", "hasil", "data", "temuan", "perbaikan"):
                if key in parsed and isinstance(parsed[key], list):
                    return parsed[key]
        return []
    except json.JSONDecodeError:
        pass
    match = re.search(r"\[.*\]", bersih, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group())
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            pass
    return []


def coba_model_gemini(prompt, api_key):
    """Coba semua model Gemini sampai ada yang berhasil."""
    for model in GEMINI_MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.0, "maxOutputTokens": 2000}
        }
        try:
            resp = requests.post(url, json=payload, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                if "candidates" in data and data["candidates"]:
                    return data["candidates"][0]["content"]["parts"][0]["text"], None
            elif resp.status_code == 404:
                continue  # Coba model berikutnya
            elif resp.status_code == 400:
                return None, "API key tidak valid"
            elif resp.status_code == 429:
                time.sleep(30)
                # Retry model yang sama
                resp2 = requests.post(url, json=payload, timeout=30)
                if resp2.status_code == 200:
                    data = resp2.json()
                    if "candidates" in data and data["candidates"]:
                        return data["candidates"][0]["content"]["parts"][0]["text"], None
        except Exception:
            continue
    return None, "Semua model Gemini tidak tersedia. Cek API key."


def buat_prompt(mode_audit, bagian, chunk):
    base = (
        f"Kamu adalah auditor akademik yang sangat teliti untuk Karya Tulis Ilmiah (KTI) kesehatan Indonesia.\n"
        f"Bagian: {bagian}\n\n"
        f"Balas HANYA dengan JSON valid. Format wajib:\n"
        f'{{"items": [{{"salah":"teks asli persis dari dokumen","benar":"koreksi benar","ket":"aturan yang dilanggar"}}]}}\n'
        f'Jika tidak ada kesalahan: {{"items": []}}\n\n'
    )

    if mode_audit == "Perbaikan Typo / EYD / PUEBI":
        aturan = """PERIKSA SETIAP KATA. Laporkan SEMUA kesalahan:

1. KATA TIDAK BAKU → ganti kata baku KBBI:
praktek→praktik, apotik→apotek, nasehat→nasihat, ijin→izin, resiko→risiko,
aktifitas→aktivitas, prosentase→persentase, sistim→sistem, tehnik→teknik,
analisa→analisis, hipotesa→hipotesis, standart→standar, obyek→objek,
subyek→subjek, nampak→tampak, merubah→mengubah, jaman→zaman,
nafas→napas, isteri→istri, kwalitas→kualitas, kwalitatif→kualitatif,
kuisioner→kuesioner, diagnosa→diagnosis, komplek→kompleks,
efektifitas→efektivitas, sekedar→sekadar, karir→karier,
survey→survei, sample→sampel, nomer→nomor, berfikir→berpikir,
fotocopy→fotokopi, menejemen→manajemen, managemen→manajemen

2. AWALAN "di-":
- di + kata kerja DISAMBUNG: "di lakukan"→"dilakukan", "di temukan"→"ditemukan",
  "di peroleh"→"diperoleh", "di gunakan"→"digunakan", "di ketahui"→"diketahui",
  "di analisis"→"dianalisis", "di buat"→"dibuat", "di berikan"→"diberikan",
  "di uji"→"diuji", "di terapkan"→"diterapkan", "di rekam"→"direkam"
- di + tempat DIPISAH: "dirumah sakit"→"di rumah sakit", "dipuskesmas"→"di puskesmas",
  "diIndonesia"→"di Indonesia", "diklinik"→"di klinik"

3. KATA ULANG: "sehari - hari"→"sehari-hari", "lain - lain"→"lain-lain",
"masing - masing"→"masing-masing", "bermacam - macam"→"bermacam-macam"

4. SPASI SEBELUM TANDA BACA: "kata ,"→"kata,", "kata ."→"kata.", "kata :"→"kata:"

5. PLEONASME: "adalah merupakan"→pilih salah satu, "agar supaya"→pilih salah satu,
"para hadirin"→"hadirin"

6. "dimana" sebagai kata tanya → "di mana"

7. Angka 1-9 dalam kalimat ditulis huruf: "1 orang"→"satu orang"

ABAIKAN: sitasi (Nama, 2021), angka statistik, satuan ukuran.
Salin teks SALAH PERSIS dari dokumen termasuk spasinya."""

    elif mode_audit == "Audit Sitasi APA 7":
        aturan = """PERIKSA SETIAP SITASI dalam teks.

CARA HITUNG PENULIS:
- Penulis dipisahkan KOMA atau "&" atau "dan" — BUKAN spasi
- Angka 4 digit di akhir = tahun, bukan penulis
- "(Wally, 2021)" = 1 penulis
- "(Paparang A, Sondakh R, 2021)" = 2 penulis → wajib "&"
- "(Rahantan, Sondakh, Paparang, 2021)" = 3 penulis → wajib et al.

ATURAN APA 7:
1. Satu penulis: (Nama_Belakang, Tahun) — hapus inisial jika ada
2. Dua penulis dalam kurung: wajib "&" bukan "dan"
   Dua penulis di narasi: wajib "dan" bukan "&"
3. Tiga+ penulis: wajib "et al." — BUKAN "dkk." atau "dkk"
4. Wajib koma antara nama dan tahun
5. "ibid." dan "op.cit." tidak digunakan di APA 7

Salin sitasi SALAH PERSIS dari teks."""

    else:
        aturan = """PERIKSA SETIAP ENTRI daftar pustaka.

ATURAN APA 7:
1. Nama belakang dulu: Santoso, B. — bukan Budi Santoso
2. Tahun dalam kurung + titik: (2021).
3. Dua penulis: Nama1, I., & Nama2, I. — "&" bukan "dan"
4. Judul artikel: huruf kapital hanya kata pertama dan nama diri
5. DOI: https://doi.org/10.xxx — bukan "doi:" atau "http://dx.doi.org"
6. Urutan alfabetis A-Z berdasarkan nama belakang
7. Nama jurnal ditulis lengkap

Salin teks SALAH PERSIS dari entri."""

    return base + aturan + f"\n\nTEKS:\n{chunk}"


def panggil_gemini(teks_input, mode_audit, bagian):
    try:
        api_key = st.secrets["GEMINI_API_KEY"]
    except Exception:
        st.error("❌ GEMINI_API_KEY tidak ditemukan di Streamlit Secrets!")
        st.info("Buka Streamlit Cloud → app → ⋮ → Settings → Secrets → tambahkan GEMINI_API_KEY")
        return None

    chunks = potong_teks(teks_input, ukuran=3000)
    total = len(chunks)

    if total == 1:
        st.info("Memproses 1 bagian teks...")
    else:
        st.info(f"Dokumen dibagi menjadi **{total} bagian**. Diproses otomatis.")

    progress = st.progress(0, text="Memulai analisis...")
    semua_hasil = []

    for i, chunk in enumerate(chunks):
        progress.progress(int((i / total) * 100), text=f"Menganalisis bagian {i+1} dari {total}...")
        prompt = buat_prompt(mode_audit, bagian, chunk)
        raw, error = coba_model_gemini(prompt, api_key)

        if error:
            st.error(f"❌ {error}")
            progress.empty()
            return None

        if raw:
            hasil = parse_hasil(raw)
            if hasil:
                semua_hasil.extend(hasil)

        if i < total - 1:
            time.sleep(4)

    progress.progress(100, text="Analisis selesai!")
    time.sleep(0.5)
    progress.empty()

    seen, unik = set(), []
    for r in semua_hasil:
        key = r.get("salah", "").strip().lower()
        if key and key not in seen:
            seen.add(key)
            unik.append(r)
    return unik


# ── SIDEBAR ──────────────────────────────────
with st.sidebar:
    st.title("📚 SIPA-KTI")
    st.caption("AI-Powered Library Assistant")
    st.caption("Powered by Google Gemini · Poltekkes Kemenkes Maluku")
    st.divider()
    mode_audit = st.selectbox("Pilih Fokus Audit:", [
        "Perbaikan Typo / EYD / PUEBI",
        "Audit Sitasi APA 7",
        "Audit Daftar Pustaka APA 7",
    ])
    pilihan_bab = st.radio("Pilih Bagian yang Diupload:", [
        "Abstrak", "Bab I - Pendahuluan", "Bab II - Tinjauan Pustaka",
        "Bab III - Metodologi", "Bab IV - Hasil & Pembahasan",
        "Bab V - Penutup/Simpulan", "Daftar Pustaka",
    ])
    st.divider()
    st.info("**Cara pakai:**\n\nUpload dokumen per bab atau tempel teks langsung.")


# ── AREA UTAMA ────────────────────────────────
st.header(f"📝 SIPA-KTI: {mode_audit}")

col1, col2 = st.columns([1, 1])
with col1:
    st.subheader("Upload Dokumen")
    uploaded_file = st.file_uploader("Upload file PDF atau DOCX", type=["pdf", "docx"])
with col2:
    st.subheader("Atau Tempel Teks")
    input_text = st.text_area("Tempel isi dokumen di sini...", height=200,
                               placeholder="Salin dan tempel teks KTI...")

if uploaded_file:
    extracted = extract_text(uploaded_file)
    if extracted:
        input_text = extracted
        jumlah_chunks = len(potong_teks(extracted))
        st.success(f"**{uploaded_file.name}** berhasil dimuat — {len(extracted):,} karakter, "
                   f"akan diproses dalam **{jumlah_chunks} bagian**.")
    else:
        st.warning("File terbaca tapi tidak ada teks yang bisa diekstrak.")

st.divider()

if st.button(f"🔍 Mulai Analisis — {pilihan_bab}", type="primary"):
    teks_analisis = input_text if input_text and input_text.strip() else ""
    if not teks_analisis:
        st.error("Silakan upload file atau tempel teks terlebih dahulu!")
    else:
        results = panggil_gemini(teks_analisis, mode_audit, pilihan_bab)
        if results is None:
            pass  # Error sudah ditampilkan di dalam fungsi
        elif len(results) == 0:
            st.success(f"✅ Tidak ditemukan kesalahan pada **{pilihan_bab}** "
                       f"untuk mode **{mode_audit}**.")
        else:
            st.subheader(f"Hasil Temuan — {pilihan_bab}")
            st.caption(f"Ditemukan **{len(results)}** item yang perlu diperbaiki.")
            kolom_valid = ["salah", "benar", "ket"]
            results_bersih = [{k: r[k] for k in kolom_valid if k in r}
                              for r in results if r.get("salah")]
            st.table(results_bersih)
            hasil_json = json.dumps(results, ensure_ascii=False, indent=2)
            nama_file = f"audit_{pilihan_bab.replace(' ', '_').replace('-', '').strip()}.json"
            st.download_button(
                label="⬇️ Unduh Hasil Audit (JSON)",
                data=hasil_json,
                file_name=nama_file,
                mime="application/json"
            )

st.divider()
st.caption("© 2026 SIPA-KTI · Perpustakaan Terpadu Poltekkes Kemenkes Maluku · Powered by Google Gemini AI")
