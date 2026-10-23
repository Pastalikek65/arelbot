# HRMN görsel modelleri — 23 Ekim 2026

## Seçim ve sıra

`image5 → image4_ultra → image4 → image3_fast → image3 → mevcut Cloudflare`

Model belirtilmeyen `/image açıklama` ve açık doğal dil görsel istekleri bu sırayı kullanır. `image5`, isteğe uygun ve temiz çıkan incelenmiş örneği nedeniyle birincil seçildi. Bu sınırlı örnek, her istemde en iyi model olduğu veya kalıcı kalite sıralaması anlamına gelmez.

| Doğrulanan model | Tek örnekte toplam süre | Çıktı |
|---|---:|---|
| `image5` | 16,33 sn | 1024×1024 JPEG |
| `image4_ultra` | 16,78 sn | 1024×1024 JPEG |
| `image4` | 16,99 sn | 1024×1024 JPEG |
| `image3_fast` | 17,59 sn | 1024×1024 JPEG |
| `image3` | 17,75 sn | 1024×1024 JPEG |

Model listesindeki 180 kayıt arasından 21 görsel kimliği gerçek üretimle ayrı ayrı denendi; 5 model geçerli dosya üretti, 16 model `HTTP 500 / no_provider_responded` verdi. Her başarılı dosya Pillow ile doğrulandı; image5 ayrıca görsel olarak incelendi. Fiyat bilgisi model listesinde yoktur.

## Açık seçim

```text
/image Ormanda bir T-rex --model image5
/image Mavi çaydanlık --model image4_ultra
/image Bir uzay istasyonu --model image4
/image Gün batımı --model image3_fast
/image Bir kitaplık --model image3
```

Açık seçim yalnız istenen modeli çağırır; hata durumunda başka modelin çıktısı seçilen modelmiş gibi sunulmaz. `--model prism`, `--model flare` ve bunların tam adları eski kullanımlarla uyumluluk/tanı için korunur. Başarısız oldukları için otomatik üretim sırasında denenmezler.

## Süre ve diğer sağlayıcılar

HRMN otomatik denemeleri toplam iş süresinin en fazla %60’ını kullanır. Böylece yavaşlayan beş modelin mevcut Cloudflare yedeğinin bütün süresini tüketmesi önlenir. Açık seçimin provider zaman aşımı korunur; iptal sinyali yutulmaz. Hiçbir sohbet sağlayıcısı, anahtar, model sırası veya Codex CLI bağlantısı bu model değişikliğiyle değiştirilmez.

## Tanı

```text
python scripts/check_hrmn_images.py
python scripts/check_hrmn_images.py --models image5 image4_ultra
python scripts/check_hrmn_images.py --models prism flare
```

Bu komutlar gerçek üretim çağrısı yapar; varsayılan olarak çalışan beş modeli kontrol eder. Ayrıntılı 21 model kanıtı: `deploy/evidence/hrmn-image-models-20261023.json`. `image3` ile `imagen3`, `image4` ile `imagen4` farklı kimliklerdir; yalnız ilk isimler bu denemede çalıştı. Tüm modellerin `firefly` rotasını paylaşması tek başına aynı kullanılabilirliği garanti etmez.
