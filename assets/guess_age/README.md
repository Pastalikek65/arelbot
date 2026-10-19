# Yaş Tahmini fotoğraf kataloğu

Etkin veritabanı **300 gerçek Wikimedia Commons portresinden** oluşur. Makine tarafından okunabilir kayıt [catalog.json](catalog.json), fotoğraf başına kaynak ve lisans listesi [ATTRIBUTION.md](ATTRIBUTION.md) dosyasındadır. Katalogda fotoğrafın çekildiği tarihte 18–88 yaş aralığında insanlar bulunur. Oyun fotoğrafları yerelden kullanır; internet kesilmesi mevcut kataloğu etkilemez.

Veritabanını genişletmek için `python scripts/import_age_catalog.py --target 300`, mevcut kayıtları yeniden kaynak kontrolünden geçirmek için aynı komutla `--revalidate` kullanılır. Her kabul edilen fotoğraf için gün hassasiyetindeki doğum günü, Commons çekim tarihi, kamera EXIF çekim tarihi, lisans ve SHA-256 kaydedilir. İki çekim tarihi uyuşmadığında, dosya adındaki yıl çeliştiğinde veya lisans bilgisi eksik olduğunda fotoğraf alınmaz. Yaş hesabı çekim günündeki tamamlanmış yıl sayısıdır.

Aşağıdaki altı dosya eski sürümler için geriye uyumlu başlangıç varlıklarıdır. Etkin katalog bulunduğunda oyun bu küçük listeyi kullanmaz; bozuk bir katalog sessizce bu listeye düşmez.

Fotoğraflar Wikimedia Commons'tan alınmış 960 piksel önizlemelerdir. Fotoğrafçı Gage Skidmore'dur. Her fotoğraf kendi sayfasındaki Creative Commons Attribution-ShareAlike (CC BY-SA) lisansıyla kullanılır; kaynak bağlantısını ve fotoğrafçıyı koruyun. Hedef yaş, kişinin doğum tarihi ile fotoğrafın çekim tarihi arasındaki tamamlanmış yıl sayısıdır. Fotoğrafların tamamı oyunun sonundaki kaynak bağlantısıyla da gösterilir.

| Yerel dosya | Kişi | Doğum tarihi | Fotoğraf tarihi | Yaş | Fotoğraf ve lisans |
| --- | --- | --- | --- | ---: | --- |
| `jared_goff_2023.jpg` | Jared Goff | 1994-10-14 | 2023-02-10 | 28 | [Commons](https://commons.wikimedia.org/wiki/File:Jared_Goff_by_Gage_Skidmore.jpg) · CC BY-SA 3.0 |
| `cate_blanchett_2017.jpg` | Cate Blanchett | 1969-05-14 | 2017-07-22 | 48 | [Commons](https://commons.wikimedia.org/wiki/File:Cate_Blanchett_by_Gage_Skidmore.jpg) · CC BY-SA 3.0 |
| `nile_rodgers_2023.jpg` | Nile Rodgers | 1952-09-19 | 2023-04-01 | 70 | [Commons](https://commons.wikimedia.org/wiki/File:Nile_Rodgers_by_Gage_Skidmore.jpg) · CC BY-SA 3.0 |
| `johnny_depp_2018.jpg` | Johnny Depp | 1963-06-09 | 2018-07-21 | 55 | [Commons](https://commons.wikimedia.org/wiki/File:Johnny_Depp_by_Gage_Skidmore.jpg) · CC BY-SA 3.0 |
| `sonia_sotomayor_2017.jpg` | Sonia Sotomayor | 1954-06-25 | 2017-01-23 | 62 | [Commons](https://commons.wikimedia.org/wiki/File:Sonia_Sotomayor_by_Gage_Skidmore.jpg) · CC BY-SA 3.0 |
| `sonequa_martin_green_2019.jpg` | Sonequa Martin-Green | 1985-03-21 | 2019-07-20 | 34 | [Commons](https://commons.wikimedia.org/wiki/File:Sonequa_Martin-Green_Gage_Skidmore_2019_(cropped).jpg) · CC BY-SA 2.0 |

Doğum tarihi kayıtları: [Jared Goff](https://www.wikidata.org/wiki/Q14950697), [Cate Blanchett](https://www.wikidata.org/wiki/Q80966), [Nile Rodgers](https://www.wikidata.org/wiki/Q497759), [Johnny Depp](https://www.wikidata.org/wiki/Q23004202), [Sonia Sotomayor](https://www.wikidata.org/wiki/Q11107), [Sonequa Martin-Green](https://www.wikidata.org/wiki/Q7560935).

Dosyaların adlarını veya lisans bilgilerini değiştirmeyin. Yeni fotoğraf eklerken çekim günü, doğum günü ve lisansı doğrulayın; yalnızca yılı bilinen fotoğraflar hedef yaş için yeterli değildir.
