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


def potong_teks(teks: str, ukuran: int = 1500) -> list:
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
    return chunks if chunks else [teks[:1500]]


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
    return None


def kirim_chunk_gemini(chunk, system_prompt, instruksi, api_key, bagian, mode_audit):
    """Kirim chunk ke Gemini API (gratis, 1500 req/hari)"""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
    
    prompt_lengkap = (
        f"{system_prompt}\n\n"
        f"Bagian: {bagian} | Mode: {mode_audit}\n"
        f"Instruksi: {instruksi}\n\n"
        f"=== TEKS YANG HARUS DIPERIKSA ===\n{chunk}\n\n"
        f"Balas HANYA dengan JSON valid. Tidak ada teks lain."
    )
    
    payload = {
        "contents": [{"parts": [{"text": prompt_lengkap}]}],
        "generationConfig": {
            "temperature": 0.0,
            "maxOutputTokens": 2000,
        }
    }
    
    try:
        resp = requests.post(url, json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        
        if "candidates" in data and data["candidates"]:
            teks = data["candidates"][0]["content"]["parts"][0]["text"]
            return teks
        elif "error" in data:
            st.warning(f"Gemini error: {data['error'].get('message', 'Unknown error')}")
            
    except requests.exceptions.Timeout:
        st.warning("Timeout. Bagian ini dilewati.")
    except requests.exceptions.HTTPError as e:
        kode = e.response.status_code
        if kode == 400:
            st.error("API key tidak valid. Periksa GEMINI_API_KEY di Streamlit Secrets.")
            return "STOP"
        elif kode == 429:
            st.warning("Rate limit Gemini. Menunggu 30 detik...")
            time.sleep(30)
            try:
                resp = requests.post(url, json=payload, timeout=60)
                resp.raise_for_status()
                data = resp.json()
                if "candidates" in data and data["candidates"]:
                    return data["candidates"][0]["content"]["parts"][0]["text"]
            except Exception:
                st.warning("Retry gagal. Bagian dilewati.")
        else:
            st.warning(f"HTTP {kode}. Bagian dilewati.")
    except Exception as e:
        st.warning(f"Error: {e}")
    return None


def panggil_gemini(teks_input, mode_audit, bagian):
    # API key dari Streamlit Secrets
    try:
        api_key = st.secrets["GEMINI_API_KEY"]
    except Exception:
        st.error("❌ API key tidak ditemukan! Tambahkan GEMINI_API_KEY di Streamlit Cloud → Settings → Secrets.")
        return None

    if mode_audit == "Perbaikan Typo / EYD / PUEBI":
        system_prompt = """Kamu adalah auditor EYD/PUEBI yang SANGAT TELITI untuk Karya Tulis Ilmiah (KTI) kesehatan Indonesia.
Balas HANYA dengan JSON valid. Tidak ada teks lain selain JSON.
Format wajib: {"items": [{"salah":"teks asli persis","benar":"teks benar","ket":"aturan yang dilanggar"}]}
Jika tidak ada kesalahan: {"items": []}

PERIKSA SETIAP KATA. Laporkan SEMUA kesalahan:

1. KATA TIDAK BAKU → ganti kata baku KBBI:
praktek→praktik, apotik→apotek, nasehat→nasihat, ijin→izin, resiko→risiko,
aktifitas→aktivitas, prosentase→persentase, sistim→sistem, tehnik→teknik,
analisa→analisis, hipotesa→hipotesis, standart→standar, obyek→objek,
subyek→subjek, nampak→tampak, merubah→mengubah, jaman→zaman,
nafas→napas, isteri→istri, kwalitas→kualitas, kwalitatif→kualitatif,
kwantitatif→kuantitatif, kuisioner→kuesioner, lembab→lembap,
diagnosa→diagnosis, anamnesa→anamnesis, komplek→kompleks,
efektifitas→efektivitas, sekedar→sekadar, karir→karier,
survey→survei, sample→sampel, nomer→nomor, jadual→jadwal,
berfikir→berpikir, fotocopy→fotokopi, menejemen→manajemen

2. AWALAN "di-":
- di + kata kerja DISAMBUNG: "di lakukan"→"dilakukan", "di temukan"→"ditemukan",
  "di peroleh"→"diperoleh", "di gunakan"→"digunakan", "di ketahui"→"diketahui",
  "di analisis"→"dianalisis", "di buat"→"dibuat", "di berikan"→"diberikan",
  "di ambil"→"diambil", "di uji"→"diuji", "di terapkan"→"diterapkan"
- di + tempat DIPISAH: "dirumah sakit"→"di rumah sakit", "dipuskesmas"→"di puskesmas",
  "diIndonesia"→"di Indonesia"

3. KATA ULANG: "sehari - hari"→"sehari-hari", "lain - lain"→"lain-lain",
"masing - masing"→"masing-masing", "bermacam - macam"→"bermacam-macam"

4. SPASI SEBELUM TANDA BACA: "kata ,"→"kata,", "kata ."→"kata.", "kata :"→"kata:"

5. PLEONASME: "adalah merupakan"→"adalah"/"merupakan", "agar supaya"→"agar"/"supaya",
"para hadirin"→"hadirin"

6. "dimana" sebagai kata tanya → "di mana"

7. Angka 1-9 dalam kalimat ditulis huruf: "1 orang"→"satu orang"

ABAIKAN: sitasi (Nama, 2021), angka statistik, satuan ukuran."""

        instruksi = "Periksa SETIAP kata. Laporkan SEMUA kesalahan EYD/PUEBI. Salin teks salah PERSIS dari dokumen."

    elif mode_audit == "Audit Sitasi APA 7":
        system_prompt = """Kamu adalah auditor sitasi APA 7 untuk KTI kesehatan Indonesia.
Balas HANYA dengan JSON valid.
Format: {"items": [{"salah":"sitasi asli persis","benar":"sitasi benar","ket":"aturan APA 7"}]}
Jika benar semua: {"items": []}

UGASMU HANYA SATU: cari dan periksa SEMUA sitasi dalam teks.

=== CARA MENGHITUNG JUMLAH PENULIS ===
PENTING: Penulis dipisahkan oleh TANDA KOMA atau "&" atau kata "dan"/"et al."/"dkk."
BUKAN dipisahkan oleh spasi. Nama satu orang bisa terdiri dari beberapa kata.

Contoh menghitung penulis dengan benar:
- "(Wally, 2021)" = 1 penulis. Tahun (4 digit) di akhir bukan penulis.
- "(Paparang A, Sondakh R, 2021)" = 2 penulis: [Paparang A] dan [Sondakh R]. Angka 2021 = tahun.
- "(Nely Rahmasari, Dhiah Novalina, 2023)" = 2 penulis: [Rahmasari] dan [Novalina].
- "(Rahantan, Sondakh, Paparang, 2021)" = 3 penulis: wajib et al.

KUNCI: Hitung koma di dalam kurung. Jika koma terakhir diikuti 4 angka = tahun, sisanya penulis.

=== ATURAN APA 7 ===

1. SATU PENULIS
   - Dalam kurung: (Nama_Belakang, Tahun) -> benar: (Wally, 2021)
   - SALAH jika ada inisial: (Wally R, 2021) -> harusnya (Wally, 2021)
   - Di narasi: Wally (2021) -> benar

2. DUA PENULIS
   - Dalam kurung: wajib "&" -> (Rahmasari & Novalina, 2021)
     SALAH jika pakai "dan": (Paparang dan Sondakh, 2021)
     SALAH jika ada inisial: (Paparang A & Sondakh R, 2021) -> harusnya (Paparang & Sondakh, 2021)
   - Di narasi: wajib "dan" -> Paparang dan Sondakh (2021)
     SALAH jika pakai "&" di narasi: Paparang & Sondakh (2021)

3. TIGA PENULIS ATAU LEBIH
   - Dalam kurung: wajib et al. -> (Rahantan et al., 2021)
     SALAH jika semua nama ditulis: (Rahantan, Sondakh, Paparang, 2021)
   - SALAH jika pakai dkk.: (Rahantan dkk., 2021) -> harusnya et al.
   - Di narasi: Rahantan et al. (2021)

4. NAMA INSTITUSI / ORGANISASI
   - Boleh langsung ditulis lengkap: (Kementerian Kesehatan, 2021) -> BENAR
   - (WHO, 2021) -> BENAR jika singkatan sudah umum dikenal
   - Yang SALAH: "ibid." atau "op.cit." -> tidak digunakan di APA 7

5. TEKNIS
   - Wajib koma antara nama/institusi dan tahun
   - Tidak boleh spasi sebelum titik/koma: "(Wally, 2021) ." -> SALAH
   - Tahun harus angka 4 digit"""

        instruksi = "Periksa SETIAP sitasi. Hitung penulis dari koma. Laporkan SEMUA yang salah."

    else:
        system_prompt = """Kamu adalah auditor daftar pustaka APA 7 untuk KTI kesehatan Indonesia.
Balas HANYA dengan JSON valid.
Format: {"items": [{"salah":"entri salah","benar":"entri benar","ket":"aturan APA 7"}]}
Jika benar semua: {"items": []}

ATURAN APA 7:
1. Nama belakang dulu: Santoso, B. (bukan Budi Santoso)
2. Tahun dalam kurung + titik: (2021).
3. Dua penulis: Nama1, I., & Nama2, I. — "&" bukan "dan"
4. Judul artikel: huruf kapital hanya kata pertama dan nama diri
5. DOI: https://doi.org/10.xxx — bukan "doi:" atau "http://dx.doi.org"
6. Urutan alfabetis A-Z
7. Nama jurnal ditulis lengkap"""

        instruksi = "Periksa SETIAP entri daftar pustaka. Laporkan SEMUA yang tidak sesuai APA 7."

    chunks = potong_teks(teks_input, ukuran=1500)
    total = len(chunks)

    if total == 1:
        st.info("Memproses 1 bagian teks...")
    else:
        st.info(f"Dokumen dibagi menjadi **{total} bagian**. Diproses otomatis.")

    progress = st.progress(0, text="Memulai analisis...")
    semua_hasil = []

    for i, chunk in enumerate(chunks):
        progress.progress(int((i / total) * 100), text=f"Menganalisis bagian {i+1} dari {total}...")
        raw = kirim_chunk_gemini(chunk, system_prompt, instruksi, api_key, bagian, mode_audit)
        if raw == "STOP":
            progress.empty()
            return None
        if raw:
            hasil = parse_hasil(raw)
            if hasil:
                semua_hasil.extend(hasil)
        if i < total - 1:
            time.sleep(1)  # Gemini lebih cepat, cukup 1 detik

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
    st.info("**Cara pakai:**\n\nUpload dokumen per bab atau tempel teks langsung. Dokumen panjang otomatis dipotong dan diproses per bagian.")


# ── AREA UTAMA ────────────────────────────────
st.header(f"📝 SIPA-KTI: {mode_audit}")

col1, col2 = st.columns([1, 1])
with col1:
    st.subheader("Upload Dokumen")
    uploaded_file = st.file_uploader("Upload file PDF atau DOCX", type=["pdf", "docx"])
with col2:
    st.subheader("Atau Tempel Teks")
    input_text = st.text_area("Tempel isi dokumen di sini...", height=200, placeholder="Salin dan tempel teks KTI...")

if uploaded_file:
    extracted = extract_text(uploaded_file)
    if extracted:
        input_text = extracted
        jumlah_chunks = len(potong_teks(extracted))
        st.success(f"**{uploaded_file.name}** berhasil dimuat — {len(extracted):,} karakter, akan diproses dalam **{jumlah_chunks} bagian**.")
    else:
        st.warning("File terbaca tapi tidak ada teks yang bisa diekstrak.")

st.divider()

if st.button(f"🔍 Mulai Analisis — {pilihan_bab}", type="primary"):
    if not input_text or not input_text.strip():
        st.error("Silakan upload file atau tempel teks terlebih dahulu!")
    else:
        results = panggil_gemini(input_text, mode_audit, pilihan_bab)
        if results is None:
            st.error("Analisis dihentikan karena kesalahan API.")
        elif len(results) == 0:
            st.success(f"✅ Tidak ditemukan kesalahan pada **{pilihan_bab}** untuk mode **{mode_audit}**.")
        else:
            st.subheader(f"Hasil Temuan — {pilihan_bab}")
            st.caption(f"Ditemukan **{len(results)}** item yang perlu diperbaiki.")
            kolom_valid = ["salah", "benar", "ket"]
            results_bersih = [{k: r[k] for k in kolom_valid if k in r} for r in results if r.get("salah")]
            st.table(results_bersih)
            hasil_json = json.dumps(results, ensure_ascii=False, indent=2)
            nama_file = f"audit_{pilihan_bab.replace(' ', '_').replace('-','').strip()}.json"
            st.download_button(label="⬇️ Unduh Hasil Audit (JSON)", data=hasil_json, file_name=nama_file, mime="application/json")

st.divider()
st.caption("© 2026 SIPA-KTI · Perpustakaan Terpadu Poltekkes Kemenkes Maluku · Powered by Google Gemini AI")
