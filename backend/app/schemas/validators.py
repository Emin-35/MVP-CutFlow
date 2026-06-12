"""
app/schemas/validators.py

Paylaşılan Pydantic alan doğrulayıcıları (saf fonksiyonlar — şema modülleri
buradan import eder, döngüsel import riski yoktur).
"""
import re
from typing import Optional

# Kişi/müşteri adı: yalnızca harf (Türkçe dahil), boşluk, kesme işareti, tire, nokta.
# Rakam veya başka sembol KABUL EDİLMEZ.
#   geçerli: "Ali Veli", "Mehmet-Ali", "O'Brien", "Aliş Öz", "A. Yılmaz"
#   geçersiz: "Ali123", "Ali_Veli", "Ali@", "12 Metal"
# [^\W\d_] = Unicode harf (rakam ve alt çizgi hariç); re.UNICODE varsayılan (str).
_PERSON_NAME_RE = re.compile(r"^[^\W\d_]+(?:[ '.\-][^\W\d_]+)*$")


def validate_person_name(value: Optional[str], *, field_label: str = "İsim") -> Optional[str]:
    """
    Kişi/müşteri adını doğrular ve kenar boşluklarını temizler.
    None veya boş string ise None döner (opsiyonel alanlar için güvenli).
    Geçersizse ValueError fırlatır (Pydantic 422'ye çevirir).
    """
    if value is None:
        return None
    cleaned = value.strip()
    if cleaned == "":
        return None
    if not _PERSON_NAME_RE.match(cleaned):
        raise ValueError(
            f"{field_label} yalnızca harf, boşluk ve ' . - karakterlerini içerebilir "
            f"(rakam veya sembol olamaz)."
        )
    return cleaned
