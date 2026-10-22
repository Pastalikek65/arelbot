# Görsel tanısı ve repo yayını — 23 Ekim 2026

- Aktif sunucu sürümü: `2026.10.23-image-diagnostics-1`.
- Sunucuda yalıtılmış test: **3239 geçti, 6 atlandı**. Yerel: **3235 geçti,
  19 atlandı**. İlk yerel çalıştırma gerçek `.env` etkisiyle 5 hata verdi;
  `PYTHON_DOTENV_DISABLED=1` ile yalıtılan takım geçti. Gerçek ortam
  dosyası test için değiştirilmedi.
- Mimari denetim: 0 sorun. Özellik kataloğu: 91 kayıt / 91 belgelenmiş.
- Bot ve worker sağlıklı; yeniden başlama sayısı 0. Yayın manifesti,
  çalışan iki modülün SHA-256 değerleri ve üç kaynak arşivi parçası doğrulandı.
- Önceki sürümle ortam dosyası, Compose, CLI, sohbet yönlendirmesi ve
  kişilik kaynağı birebir aynı. Kişilik içeriği bu işlemde incelenmedi.
- Normal Codex CLI: `gpt-5.6-luna`; sıra OpenAI → HRMN → Cloudflare →
  EVREN → NVIDIA. App-server eklenmedi.

## Kesin model sonucu

Çalışan yeni sürümde `scripts/check_hrmn_images.py` iki model için de
`HTTP 500 / no_provider_responded` verdi. Prism: 2,17 sn,
`req_20cb93ecaf3aa1029e6b8f30`; Flare: 2,01 sn,
`req_9d2f9fd455c4c1b1d2d69537`.

Bu nedenle iki model için durum **BLOCKED_UPSTREAM**. Açık model seçimi
başka modele sessiz geçmez; modelsiz isteklerin mevcut yedeği korunur.
[Ayrıntılı tanı](HRMN_IMAGE_DIAGNOSIS_2026-10-23.md).

## GitHub ve kapsam

İlk yayın commit'i `f2c05a35c657cf70fd287574cdcc80e753a2d84a`;
dosya ağacı `6bd911cf8be15bd73fc2728bed3fae810d31d3a7` ile yerel
test edilen ağacın aynı olduğu doğrulandı. Mevcut `main` geçmişi korundu.
326 ikili dosya ve metin dosyaları bağlı GitHub hesabıyla aktarıldı;
komut satırında yazma kimliği bulunmuyordu. Kaynak ve yayınlanmamış geçmişte
bilinen ortam sırları için tarama eşleşme vermedi.

README, 45 oyunun komut/oyuncu/mekanik rehberi ve tüm özellik rehberi
güncellendi. GitHub Actions ilk kontrol sırasında **IN_PROGRESS** idi;
CI başarısı iddia edilmez. Manuel Telegram kullanıcı testi **NOT_RUN**.
Genel faz 9 için Telegram mutasyon ve izleme alarmı teslim kanıtları hâlâ
eksik; bu yayın bütün bot için üretim kabulü olarak sunulmaz.
